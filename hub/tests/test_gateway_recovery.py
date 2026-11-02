"""Crash/restart and uncertain outcomes, with Mock state only and local journals."""
import asyncio
import json

import pytest

from deskmate_hub.control.command_journal import JournalError
from deskmate_hub.control.device_adapters import DeviceIOError
from deskmate_hub.control.device_gateway import CMD_TOPIC, DeviceGateway
from test_device_gateway import command, setup, run


def restart(old, adapter, device, clock, path, **kwargs):
    old.close()
    from deskmate_hub.control.devices import DeviceRegistry
    registry = DeviceRegistry(); registry.register(device)
    return DeviceGateway(registry, {'mock': adapter}, enabled=True, journal_path=path,
                         publish_result=lambda *_: None, clock=lambda: clock[0], **kwargs)


def test_restart_after_send_reconciles_by_get_without_second_control(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    run(g.handle_message(CMD_TOPIC, command()))
    assert len(adapter.calls) == 1
    restored = restart(g, adapter, device, t, path)
    try:
        run(restored.handle_message(CMD_TOPIC, command()))
        run(restored.poll())
        assert len(adapter.calls) == 1
        assert restored.results['c1']['data']['status'] == 'succeeded'
        assert restored.evidence['c1']['recovered']
    finally: restored.close()


def test_crash_before_post_is_not_replayed_as_a_fresh_command(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    g._journal.claim(command()['data'], {'mode': 'mock', 'write_attempted': False,
                                        'api_accepted': False, 'state_confirmed': False})
    restored = restart(g, adapter, device, t, path)
    try:
        run(restored.handle_message(CMD_TOPIC, command())); run(restored.poll())
        assert not adapter.calls
        t[0] = 1016; run(restored.poll())
        assert restored.results['c1']['data']['status'] == 'timeout'
    finally: restored.close()


def test_process_dies_after_physical_mock_change_before_receipt(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    normal_control = adapter.control
    async def crash(*args, **kwargs):
        await normal_control(*args, **kwargs)
        raise asyncio.CancelledError()
    adapter.control = crash
    with pytest.raises(asyncio.CancelledError): run(g.handle_message(CMD_TOPIC, command()))
    restored = restart(g, adapter, device, t, path)
    try:
        run(restored.handle_message(CMD_TOPIC, command())); run(restored.poll())
        assert len(adapter.calls) == 1 and restored.results['c1']['data']['status'] == 'succeeded'
    finally: restored.close()


def test_post_response_lost_is_unknown_not_immediate_failure_or_resend():
    g, adapter, device, t, sent = setup()
    normal_control = adapter.control
    async def lost_response(*args, **kwargs):
        await normal_control(*args, **kwargs)
        raise DeviceIOError('network_timeout')
    adapter.control = lost_response
    response = run(g.handle_message(CMD_TOPIC, command()))
    assert response['data']['status'] == 'executing'
    assert response['data']['error_code'] == 'request_outcome_unknown'
    assert g.evidence['c1']['api_accepted'] is None
    run(g.poll()); run(g.handle_message(CMD_TOPIC, command()))
    assert g.results['c1']['data']['status'] == 'succeeded' and len(adapter.calls) == 1


def test_response_lost_but_state_unchanged_expires_without_success():
    g, adapter, device, t, sent = setup(apply=False)
    async def lost(*args, **kwargs):
        adapter.calls.append(('mock_vent', 'set_power', 'on'))
        raise DeviceIOError('network_timeout')
    adapter.control = lost
    run(g.handle_message(CMD_TOPIC, command())); run(g.poll())
    assert g.results['c1']['data']['status'] == 'executing'
    t[0] = 1016; run(g.poll())
    assert g.results['c1']['data']['status'] == 'timeout' and len(adapter.calls) == 1


def test_completed_command_replays_result_not_action_after_restart(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    run(g.handle_message(CMD_TOPIC, command())); run(g.poll())
    restored = restart(g, adapter, device, t, path)
    try:
        result = run(restored.handle_message(CMD_TOPIC, command()))
        assert result['data']['status'] == 'succeeded' and len(adapter.calls) == 1
        assert run(restored.handle_message(CMD_TOPIC, command(value='off'))) is None
    finally: restored.close()


def test_journal_is_exclusive_and_corruption_fails_closed(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    try:
        with pytest.raises(JournalError): setup(journal_path=path)
        run(g.handle_message(CMD_TOPIC, command()))
        assert len(adapter.calls) == 1
    finally: g.close()
    bad = tmp_path/'bad.db'; bad.write_bytes(b'not sqlite')
    with pytest.raises(JournalError): setup(journal_path=bad)


def test_mock_to_live_cannot_reuse_journal_or_mutate_runtime_authority(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    g.mode = 'live'; g.allow_live = True; device.mode = 'live'; adapter.mode = 'live'
    result = run(g.handle_message(CMD_TOPIC, command()))
    assert result['data']['error_code'] == 'control_disabled' and not adapter.calls
    g.close()
    with pytest.raises(JournalError): setup(mode='live', allow_live=True, journal_path=path)


def test_restart_does_not_execute_previously_queued_undo(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path, apply=False)
    run(g.handle_message(CMD_TOPIC, command()))
    undo = command('undo1', gate='undo', value='off')
    run(g.handle_message(CMD_TOPIC, undo))
    restored = restart(g, adapter, device, t, path)
    try:
        run(restored.poll())
        assert restored.results['undo1']['data']['status'] == 'cancelled' and len(adapter.calls) == 1
    finally: restored.close()


def test_journal_write_failure_cannot_reach_adapter(tmp_path, monkeypatch):
    g, adapter, device, t, sent = setup(journal_path=tmp_path/'commands.db')
    def fail(*args, **kwargs): raise JournalError('disk_unavailable')
    monkeypatch.setattr(g._journal, 'claim', fail)
    try:
        with pytest.raises(JournalError): run(g.handle_message(CMD_TOPIC, command()))
        assert not adapter.calls
    finally: g.close()


def test_fake_official_sdk_post_can_change_state_then_lose_response():
    from test_thinq_adapter import SDK, device
    from deskmate_hub.control.device_adapters import ThinQConnectAdapter
    sdk = SDK()
    async def post_lost(device_id, payload, timeout=15):
        sdk.calls.append((device_id, payload))
        sdk.state = payload  # Synthetic device has acted, but the HTTP response is lost.
        raise asyncio.TimeoutError()
    sdk.async_post_device_control = post_lost
    adapter = ThinQConnectAdapter(sdk, allow_control=True)
    with pytest.raises(DeviceIOError, match='network_timeout'):
        run(adapter.control(device(), 'set_power', 'on'))
    assert run(adapter.read_state(device())) == {'set_power': 'on'} and len(sdk.calls) == 1


def test_unregistered_target_after_restart_never_replays_original_control(tmp_path):
    path = tmp_path/'commands.db'
    g, adapter, device, t, sent = setup(journal_path=path)
    run(g.handle_message(CMD_TOPIC, command())); g.close()
    from deskmate_hub.control.devices import DeviceRegistry
    restored = DeviceGateway(DeviceRegistry(), {'mock': adapter}, enabled=True, journal_path=path,
                             publish_result=lambda *_: None, clock=lambda: t[0])
    try:
        run(restored.poll())
        assert restored.results['c1']['data']['error_code'] == 'recovery_target_unavailable'
        assert len(adapter.calls) == 1
    finally: restored.close()
