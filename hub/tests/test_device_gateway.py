"""In-process fake MQTT/SDK only. No broker or real appliances are contacted."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from deskmate_hub.control.device_adapters import MockDeviceAdapter
from deskmate_hub.control.device_gateway import CMD_TOPIC, RESULT_TOPIC, DeviceGateway
from deskmate_hub.control.devices import Capability, Device, DeviceRegistry


def setup(*, enabled=True, apply=True, **kwargs):
    t = [1000.0]; r = DeviceRegistry()
    d = Device('mock_vent', 'mock', ('outside_air',),
               {'set_power': Capability('set_power', ('on', 'off'), verified=True)},
               online=True, state={'set_power': 'off'}, observed_at=t[0])
    r.register(d)
    adapter = MockDeviceAdapter({d.target_id: d.state}, apply=apply, clock=lambda: t[0])
    sent = []
    gw = DeviceGateway(r, {'mock': adapter}, publish_result=lambda topic, env: sent.append((topic, env)),
                       enabled=enabled, clock=lambda: t[0], **kwargs)
    return gw, adapter, d, t, sent


def command(cid='c1', **extra):
    body = {'command_id': cid, 'target_id': 'mock_vent', 'operation': 'set_power', 'value': 'on',
            'origin_state': 'ACTION_ENV', 'cause': 'environment', 'gate': 'suggest',
            'requires_confirmation': False, 'expires_ts_ms': 1015000, **extra}
    return {'schema_version': '1.0', 'node': 'hub', 'ts': 1000, 'data': body}


def run(awaitable): return asyncio.run(awaitable)


def test_request_accepted_is_not_physical_success_and_duplicates_never_resend():
    g, a, d, t, sent = setup()
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['status'] == 'executing' and a.calls == [('mock_vent', 'set_power', 'on')]
    assert g.evidence['c1']['api_accepted'] and not g.evidence['c1']['state_confirmed']
    run(g.handle_message(CMD_TOPIC, json.dumps(command()).encode()))
    assert len(a.calls) == 1
    run(g.poll())
    assert sent[-1][0] == RESULT_TOPIC
    assert sent[-1][1]['data']['status'] == 'succeeded'
    assert sent[-1][1]['data']['actual_value'] == 'on'
    assert sent[-1][1]['node'] == 'device-gateway-mock'
    assert g.evidence['c1']['state_confirmed']
    run(g.handle_message(CMD_TOPIC, command()))
    assert len(a.calls) == 1


@pytest.mark.parametrize('case,error', [('disabled', 'control_disabled'), ('offline', 'offline'),
    ('unknown', 'unregistered_device'), ('unsupported', 'unsupported_operation'), ('expired', 'command_expired'),
    ('auto', 'automatic_control_disabled'), ('manual', 'manual_override'), ('retained', 'retained_command_rejected')])
def test_no_writes_for_rejected_commands(case, error):
    g, a, d, t, _ = setup(enabled=case != 'disabled')
    c = command()
    if case == 'offline': d.online = False
    if case == 'unknown': c['data']['target_id'] = 'unknown'
    if case == 'unsupported': c['data']['operation'] = 'invented_operation'
    if case == 'expired': t[0] = 1015
    if case == 'auto': c['data']['gate'] = 'auto'
    if case == 'manual': d.last_manual_at = 999
    result = run(g.handle_message(CMD_TOPIC, c, retained=case == 'retained'))
    assert result['data']['error_code'] == error and a.calls == []


def test_wrong_node_malformed_fields_and_wrong_topic_ignored():
    g, a, *_ = setup()
    for c in (b'{', [], command(expires_ts_ms=True), command(gate='bypass'),
              command(requires_confirmation='no')):
        assert run(g.handle_message(CMD_TOPIC, c)) is None
    c = command(); c['node'] = 'unknown'
    assert run(g.handle_message(CMD_TOPIC, c)) is None
    assert run(g.handle_message('new/topic', command())) is None and not a.calls


def test_changed_duplicate_does_not_replace_original_result():
    g, a, *_ = setup()
    run(g.handle_message(CMD_TOPIC, command())); run(g.poll())
    result = g.results['c1']
    assert run(g.handle_message(CMD_TOPIC, command(value='off'))) is None
    assert g.results['c1'] is result and len(a.calls) == 1


def test_api_acceptance_without_state_change_times_out_no_resend():
    g, a, d, t, sent = setup(apply=False)
    run(g.handle_message(CMD_TOPIC, command()))
    for _ in range(3): run(g.poll())
    assert g.results['c1']['data']['status'] == 'executing'
    t[0] = 1015; run(g.poll())
    assert g.results['c1']['data']['status'] == 'timeout' and len(a.calls) == 1
    assert not g.evidence['c1']['state_confirmed']


def test_noop_state_read_is_distinct_from_request_and_actual_transition():
    g, a, d, t, sent = setup()
    d.state['set_power'] = a.states[d.target_id]['set_power'] = 'on'
    run(g.handle_message(CMD_TOPIC, command()))
    assert not a.calls
    assert g.evidence['c1'] == {'mode': 'mock', 'write_attempted': False,
        'api_accepted': False, 'state_confirmed': True, 'outcome': 'already_satisfied'}


def test_query_errors_never_fabricate_control_success():
    g, a, d, t, sent = setup()
    run(g.handle_message(CMD_TOPIC, command()))
    a.error = 'network_timeout'; run(g.poll())
    assert g.results['c1']['data']['status'] == 'executing'
    t[0] = 1016; run(g.poll())
    assert g.results['c1']['data']['status'] == 'timeout'


def test_manual_change_before_write_and_after_acceptance_are_not_overridden():
    g, a, d, t, sent = setup()
    a.states[d.target_id]['set_power'] = 'on'
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['error_code'] == 'manual_override' and not a.calls
    g, a, d, t, sent = setup(apply=False)
    run(g.handle_message(CMD_TOPIC, command()))
    g.registry.mark_manual(d.target_id, 1001); t[0] = 1001; run(g.poll())
    assert g.results['c1']['data']['status'] == 'cancelled' and len(a.calls) == 1


def test_humidity_opposite_active_or_pending_blocks_execution():
    g, a, d, t, sent = setup(apply=False)
    d.roles = ('humidifying',)
    other = Device('mock_dehumid', 'mock', ('dehumidifying',), d.capabilities,
                   online=True, state={'set_power': 'off'}, observed_at=1000)
    g.registry.register(other); a.states[other.target_id] = {'set_power': 'off'}
    run(g.handle_message(CMD_TOPIC, command()))
    result = run(g.handle_message(CMD_TOPIC, command('c2', target_id=other.target_id)))
    assert result['data']['error_code'] == 'humidity_interlock' and len(a.calls) == 1


def test_existing_ble_target_is_not_claimed_or_sent_conflicting_results():
    g, a, d, t, sent = setup()
    g.registry.register(Device('desk_lamp', 'existing-pi5', ('lighting',), {}, gateway_owned=False))
    assert run(g.handle_message(CMD_TOPIC, command(target_id='desk_lamp'))) is None
    assert not a.calls and not sent


def test_mqtt_callbacks_subscribe_existing_topic_qos1_and_use_existing_envelope():
    g, a, d, t, sent = setup()
    class Client:
        subscriptions = []
        def subscribe(self, topic, qos): self.subscriptions.append((topic, qos))
    client = Client(); g.attach(client)
    client.on_connect(client, None, None, 0)
    assert client.subscriptions == [(CMD_TOPIC, 1)]
    msg = SimpleNamespace(topic=CMD_TOPIC, payload=json.dumps(command()).encode(), retain=False)
    client.on_message(client, None, msg)
    run(g.pump())
    assert sent[-1][1]['data']['status'] == 'succeeded' and len(a.calls) == 1


def test_session_ledger_capacity_fails_closed_without_evicting_dedup_keys():
    g, a, *_ = setup(max_commands=1)
    run(g.handle_message(CMD_TOPIC, command())); run(g.poll())
    assert run(g.handle_message(CMD_TOPIC, command('c2', value='off'))) is None
    run(g.handle_message(CMD_TOPIC, command()))
    assert len(a.calls) == 1


@pytest.mark.parametrize('target', ['desk_lamp', 'vent_fan'])
def test_unregistered_pi5_target_is_ignored_without_competing_failure(target):
    g, a, d, t, sent = setup()
    assert run(g.handle_message(CMD_TOPIC, command(target_id=target))) is None
    assert not sent and not a.calls


def test_dispatcher_to_mqtt_gateway_result_and_undo_roundtrip():
    from deskmate_hub.control import ControlDispatcher
    from deskmate_hub.inference import GateMode
    g, a, d, t, sent = setup()
    commands = []
    ctrl = ControlDispatcher({'result_timeout_sec': 15, 'undo_window_sec': 60, 'actions': {'environment': [
        {'target_id': 'mock_vent', 'operation': 'set_power', 'value': 'on', 'undo_value': 'off'}]}},
        publish_cmd=commands.append, clock=lambda: t[0])
    ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST)
    assert not commands
    ctrl.on_feedback('accept')
    async def deliver(c):
        await g.handle_message(CMD_TOPIC, {'node': 'hub', 'schema_version': '1.0', 'data': c})
        await g.poll()
        for _, envelope in sent:
            ctrl.on_result(envelope['data'], t[0])
        sent.clear()
    run(deliver(commands[0]))
    assert ctrl.action_done() and ctrl.episode.commands[0].status == 'succeeded'
    ctrl.on_feedback('reject')
    assert commands[1]['gate'] == 'undo' and commands[1]['command_id'] != commands[0]['command_id']
    run(deliver(commands[1]))
    assert a.states[d.target_id]['set_power'] == 'off' and ctrl.episode.commands[1].status == 'succeeded'


def test_live_gateway_requires_explicit_permission_and_matching_adapter_mode():
    g, a, d, t, sent = setup()
    g.mode = 'live'; d.mode = 'live'
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['error_code'] == 'control_disabled' and not a.calls
    g, a, d, t, sent = setup()
    d.mode = 'live'
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['error_code'] == 'target_owned_elsewhere_or_mode_mismatch' and not a.calls


def test_unsupported_observed_state_cannot_be_used_as_safe_rollback_baseline():
    g, a, d, t, sent = setup()
    d.state = a.states[d.target_id] = {'set_power': 'unknown'}
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['error_code'] == 'unknown_current_state' and not a.calls


def test_result_after_deadline_does_not_revive_expired_command():
    g, a, d, t, sent = setup()
    run(g.handle_message(CMD_TOPIC, command()))
    async def delayed_read(device):
        t[0] = 1016
        return {'set_power': 'on'}
    a.read_state = delayed_read
    run(g.poll())
    assert g.results['c1']['data']['status'] == 'timeout'
    assert not g.evidence['c1']['state_confirmed']


def test_undo_before_original_state_confirmation_is_sequenced_not_lost():
    g, a, d, t, sent = setup(apply=False)
    run(g.handle_message(CMD_TOPIC, command()))
    result = run(g.handle_message(CMD_TOPIC, command('undo1', gate='undo', value='off')))
    assert result['data']['status'] == 'executing' and len(a.calls) == 1
    assert not g.evidence['undo1']['api_accepted']
    a.states[d.target_id]['set_power'] = 'on'  # Synthetic original operation completes.
    a.apply = True
    run(g.poll())
    assert a.calls == [('mock_vent', 'set_power', 'on'), ('mock_vent', 'set_power', 'off')]
    run(g.poll())
    assert g.results['undo1']['data']['status'] == 'succeeded'
    assert a.states[d.target_id]['set_power'] == 'off'


def test_undo_does_not_claim_success_when_original_state_never_confirmed():
    g, a, d, t, sent = setup(apply=False)
    run(g.handle_message(CMD_TOPIC, command()))
    run(g.handle_message(CMD_TOPIC, command('undo1', gate='undo', value='off', expires_ts_ms=1020000)))
    t[0] = 1016; run(g.poll())
    assert g.results['c1']['data']['status'] == 'timeout'
    assert g.results['undo1']['data']['error_code'] == 'original_state_unconfirmed'
    assert len(a.calls) == 1


def test_air_purifier_auto_requires_air_quality_policy_even_if_generic_auto_enabled():
    g, a, d, t, sent = setup(automatic_control=True)
    d.roles = ('air_cleaning',)
    result = run(g.handle_message(CMD_TOPIC, command(gate='auto')))
    assert result['data']['error_code'] == 'air_quality_policy_unverified' and not a.calls


@pytest.mark.parametrize('kwargs', [{'enabled': 'false'}, {'allow_live': 'false'},
    {'automatic_control': 'false'}, {'manual_override_sec': -1}, {'state_max_age_sec': float('nan')}])
def test_unsafe_gateway_configuration_rejected(kwargs):
    with pytest.raises(ValueError): setup(**kwargs)


def test_nonreversible_operation_requires_confirmed_suggest_contract():
    g, a, d, t, sent = setup()
    d.capabilities['set_power'] = Capability('set_power', ('on', 'off'), verified=True, reversible=False)
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['error_code'] == 'confirmation_required' and not a.calls
