"""Official SDK method contract exercised with synthetic SDK responses only."""
import asyncio
import copy

import pytest

from deskmate_hub.control.device_adapters import DeviceIOError, ThinQConnectAdapter, _profile_accepts
from deskmate_hub.control.devices import Capability, Device


PROFILE = {'property': {'operation': {'mockOperation': {
    'type': 'enum', 'mode': ['r', 'w'], 'value': {'r': ['START', 'STOP'], 'w': ['START', 'STOP']}}}}}


class SDK:
    """No network, credentials, LG account, or real device ID."""
    def __init__(self):
        self.profile = copy.deepcopy(PROFILE)
        self.state = {'operation': {'mockOperation': 'STOP'}}
        self.calls = []; self.error = None; self.delay = 0; self.on_profile = lambda: None

    async def async_get_device_profile(self, device_id, timeout=15):
        self.on_profile()
        if self.error: raise self.error
        return self.profile

    async def async_get_device_status(self, device_id, timeout=15):
        if self.delay: await asyncio.sleep(self.delay)
        if self.error: raise self.error
        return self.state

    async def async_post_device_control(self, device_id, payload, timeout=15):
        if self.error: raise self.error
        self.calls.append((device_id, payload))
        return None  # Official SDK can successfully POST without a response body.


def device():
    cap = Capability('set_power', ('on', 'off'), verified=True,
        profile_path=('property', 'operation', 'mockOperation'), state_path=('operation', 'mockOperation'),
        wire_values=(('on', 'START'), ('off', 'STOP')))
    return Device('test_device', 'thinq', ('cooling',), {'set_power': cap}, mode='live',
                  online=True, external_id='synthetic-test-device')


def test_default_live_write_disabled_but_readonly_state_available():
    sdk = SDK(); adapter = ThinQConnectAdapter(sdk)
    assert asyncio.run(adapter.read_state(device())) == {'set_power': 'off'}
    with pytest.raises(DeviceIOError, match='live_control_disabled'):
        asyncio.run(adapter.control(device(), 'set_power', 'on'))
    assert not sdk.calls


def test_verified_profile_maps_registered_values_and_receipt_is_not_state_confirmation():
    sdk = SDK(); adapter = ThinQConnectAdapter(sdk, allow_control=True)
    receipt = asyncio.run(adapter.control(device(), 'set_power', 'on'))
    assert receipt.api_accepted and receipt.mode == 'live'
    assert sdk.calls == [('synthetic-test-device', {'operation': {'mockOperation': 'START'}})]
    assert asyncio.run(adapter.read_state(device())) == {'set_power': 'off'}


@pytest.mark.parametrize('case', ['readonly', 'value_not_allowed', 'missing_property', 'unverified', 'no_binding'])
def test_unsupported_or_unverified_profiles_never_post(case):
    sdk = SDK(); d = device(); adapter = ThinQConnectAdapter(sdk, allow_control=True)
    if case == 'readonly': sdk.profile['property']['operation']['mockOperation']['mode'] = ['r']
    elif case == 'value_not_allowed': sdk.profile['property']['operation']['mockOperation']['value']['w'] = ['STOP']
    elif case == 'missing_property': sdk.profile = {}
    elif case == 'unverified':
        d.capabilities['set_power'] = Capability('set_power', ('on', 'off'))
    else: d.capabilities['set_power'] = Capability('set_power', ('on', 'off'), verified=True)
    with pytest.raises(DeviceIOError): asyncio.run(adapter.control(d, 'set_power', 'on'))
    assert not sdk.calls


@pytest.mark.parametrize('code,expected', [('1103', 'authentication_or_authority'), ('1218', 'authentication_or_authority'),
    ('1308', 'authentication_or_authority'), ('1222', 'offline'), ('2208', 'api_error')])
def test_sdk_errors_sanitized_without_headers_pat_or_account(code, expected):
    class FakeThinQAPIException(Exception):
        pass
    sdk = SDK(); error = FakeThinQAPIException('fake secret token/account must not escape')
    error.code = code; error.headers = {'Authorization': 'synthetic secret'}; sdk.error = error
    with pytest.raises(DeviceIOError) as caught:
        asyncio.run(ThinQConnectAdapter(sdk).read_state(device()))
    assert str(caught.value) == expected and caught.value.__cause__ is None


def test_network_timeout_and_network_error_safe():
    sdk = SDK(); sdk.delay = 0.02
    with pytest.raises(DeviceIOError, match='network_timeout'):
        asyncio.run(ThinQConnectAdapter(sdk, timeout_sec=0.001).read_state(device()))
    sdk.delay = 0; sdk.error = OSError('private network details')
    with pytest.raises(DeviceIOError, match='network_error'):
        asyncio.run(ThinQConnectAdapter(sdk).read_state(device()))


def test_command_expiring_during_profile_fetch_cannot_be_sent():
    t = [1000]; sdk = SDK(); sdk.on_profile = lambda: t.__setitem__(0, 1020)
    adapter = ThinQConnectAdapter(sdk, allow_control=True, clock=lambda: t[0])
    with pytest.raises(DeviceIOError, match='command_expired'):
        asyncio.run(adapter.control(device(), 'set_power', 'on', expires_at=1015))
    assert not sdk.calls


def test_profile_range_step_and_bool_are_strictly_validated():
    prop = {'type': 'range', 'mode': ['r', 'w'], 'value': {'w': {'min': 10, 'max': 30, 'step': 0.5}}}
    assert _profile_accepts(prop, 20.5)
    for value in (True, float('nan'), 20.2, 31, '20'):
        assert not _profile_accepts(prop, value)
    assert not _profile_accepts({'type': 'unknown', 'mode': ['r', 'w']}, 'anything')
