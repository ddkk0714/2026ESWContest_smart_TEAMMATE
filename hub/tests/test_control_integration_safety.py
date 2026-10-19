"""Backend safety review regressions. These tests do not run Flutter."""
import pytest

from deskmate_hub.control import ControlDispatcher
from deskmate_hub.control.environment_policy import MockControlTransport
from deskmate_hub.inference import GateMode
from test_environment_control_policy import ready, dispatch, config, sample
from test_device_gateway import setup, command, run
from deskmate_hub.control.device_gateway import CMD_TOPIC


def test_unsent_suggestion_cannot_accept_an_adapter_result_before_user_acceptance():
    p, registry, device = ready(); sent = []
    ctrl = dispatch(p, sent)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 1010)
    assert not ctrl.on_result({'command_id': ep.commands[0].command_id,
                               'status': 'succeeded', 'actual_value': 'on'}, 1011)
    assert ep.awaiting_user and ep.commands[0].sent_ts is None and not sent


def test_no_device_ventilation_advice_uses_existing_feedback_and_waits():
    p, registry, device = ready(); registry.devices.clear(); sent = []
    ctrl = ControlDispatcher({'suggest_timeout_sec': 60}, publish_cmd=sent.append,
        plan_commands=p.plan, plan_advice=p.advice, dispatch_guard=p.can_dispatch)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 1010)
    assert ep.awaiting_user and not ep.done and not ep.commands
    assert ep.advice[0]['kind'] == 'manual_ventilation'
    ctrl.tick(1020); assert not ctrl.action_done(1020)
    ctrl.on_feedback('accept', 1020)
    assert ep.done and ep.outcome == 'advised' and not sent
    ctrl.close_episode()
    assert ctrl.summary()['last_episode']['advice'][0]['sensor'] == 'co2_ppm'


@pytest.mark.parametrize('verdict,outcome', [('reject', 'rejected'), ('timeout', 'expired')])
def test_advice_only_reject_and_timeout_never_invent_a_device_command(verdict, outcome):
    p, registry, _ = ready(); registry.devices.clear(); sent = []
    ctrl = ControlDispatcher({}, publish_cmd=sent.append, plan_commands=p.plan, plan_advice=p.advice)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 1010)
    ctrl.on_feedback(verdict, 1011)
    assert ep.done and ep.outcome == outcome and not sent


def test_accepted_plan_is_frozen_not_reselected_when_another_device_appears():
    p, registry, device = ready(); sent = []; ctrl = dispatch(p, sent)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 1010)
    from dataclasses import replace
    registry.register(replace(device, target_id='aaa_new_preferred'))
    ctrl.on_feedback('accept', 1011)
    assert sent[0]['target_id'] == device.target_id
    assert sent[0]['operation'] == ep.commands[0].operation and sent[0]['value'] == ep.commands[0].value
    assert len(sent) == 1


def test_manual_operation_just_before_auto_command_blocks_action():
    g, adapter, device, clock, _ = setup(automatic_control=True)
    g.registry.mark_manual(device.target_id, clock[0])
    response = run(g.handle_message(CMD_TOPIC, command(gate='auto')))
    assert response['data']['error_code'] == 'manual_override' and not adapter.calls


def test_no_registered_air_purifier_and_no_thinq_auth_cannot_start_an_api_call():
    g, adapter, device, clock, _ = setup()
    response = run(g.handle_message(CMD_TOPIC, command(target_id='unregistered_air_purifier')))
    assert response['data']['error_code'] == 'unregistered_device' and not adapter.calls
    device.adapter = 'thinq_without_auth'
    response = run(g.handle_message(CMD_TOPIC, command('noauth')))
    assert response['data']['error_code'] == 'adapter_unavailable' and not adapter.calls


def test_new_policy_results_require_matching_confirmed_value():
    p, registry, device = ready(); sent = []; ctrl = dispatch(p, sent)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    assert ctrl.on_result({'command_id': sent[0]['command_id'], 'status': 'succeeded'}, 1011)
    assert ep.commands[0].status == 'failed' and ep.commands[0].error_code == 'actual_value_unconfirmed'
    p, registry, device = ready(); sent = []; ctrl = dispatch(p, sent)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    ctrl.on_result({'command_id': sent[0]['command_id'], 'status': 'succeeded', 'actual_value': 'off'}, 1011)
    assert ep.commands[0].error_code == 'actual_value_mismatch'


@pytest.mark.parametrize('status,actual,confirmed', [
    ('accepted', False, False), ('executing', False, False),
    ('succeeded', True, True), ('succeeded', False, False),
])
def test_summary_distinguishes_request_acceptance_from_confirmed_state(status, actual, confirmed):
    p, registry, device = ready(); sent = []; ctrl = dispatch(p, sent)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    payload = {'command_id': sent[0]['command_id'], 'status': status}
    if actual: payload['actual_value'] = 'on'
    ctrl.on_result(payload, 1011)
    command = ctrl.summary()['commands'][0]
    assert command['state_confirmed'] is confirmed
    if not confirmed: assert command['status'] != 'succeeded'
    assert command['command_id'] == sent[0]['command_id']
