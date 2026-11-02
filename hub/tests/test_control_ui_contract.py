"""Real FSM + synthetic sensors + loopback MQTT + Mock Gateway -> Flutter fixture.

Set DESKMATE_UI_CONTRACT_OUTPUT to export locally. Contains synthetic values only;
Dart tests consume the exported envelopes without substituting a UI-only plan.
"""
import copy
import json
import os
from pathlib import Path

from test_control_platform_e2e import Simulation


def test_flutter_contract_export_from_real_mock_e2e(tmp_path):
    s = Simulation(tmp_path)
    cases = []
    def capture(name, envelope, request=None):
        cases.append({'name': name, 'state': copy.deepcopy(envelope), 'request': copy.deepcopy(request)})
    try:
        s.hub.boot_id = 'mock-ui-contract'
        s.start_focus()
        state, _ = s.proposal(s.low_light)
        request = copy.deepcopy(s.hub.pending_request)
        assert request['data']['control_plan'] == state['data']['sensor_summary']['control']
        capture('low_light_suggestion', state, request)
        state = s.respond('accept', s.low_light)
        assert s.commands[0]['execution_mode'] == 'mock'
        capture('accepted_mock_success_monitor', state)
        s.recover()
        state, _ = s.proposal(s.co2)
        capture('co2_manual_ventilation', state, s.hub.pending_request)
        capture('ventilation_ack_monitor', s.respond('accept', s.co2))
        s.recover()
        state, _ = s.proposal(s.hot)
        capture('hot_cooling_suggestion', state, s.hub.pending_request)
        capture('rejected_monitor', s.respond('reject', s.hot))
        s.recover()
        s.proposal(s.hot)
        s.adapter.error = 'network_error'
        capture('failed_monitor', s.respond('accept', s.hot))
        assert cases[-1]['state']['data']['sensor_summary']['control']['last_episode']['commands'][0]['status'] == 'failed'
        s.recover()
        # Advice-only proposal expiry has no actuator command.
        s.proposal(s.co2)
        for _ in range(25):
            state = s.step(s.co2)
            if state['data']['fsm_state'] == 'MONITOR': break
        capture('expired_monitor', state)
        assert s.hub.control.history[-1].outcome == 'expired'
    finally:
        s.close()
    # Separate real scenario without a Gateway yields unconfirmed timeout.
    (tmp_path / 'absent').mkdir()
    s = Simulation(tmp_path / 'absent', gateway_running=False)
    try:
        s.hub.boot_id = 'mock-ui-contract'
        s.start_focus(); state, _ = s.proposal(s.low_light)
        s.respond('accept', s.low_light)
        for _ in range(4):
            state = s.step(s.low_light)
            if state['data']['fsm_state'] == 'MONITOR': break
        capture('gateway_missing_timeout', state)
        assert state['data']['sensor_summary']['control']['last_episode']['commands'][0]['status'] == 'timeout'
    finally:
        s.close()
    document = {'provenance': 'synthetic sensors; real Hub/FSM/Dispatcher; loopback MQTT; Mock Gateway; no hardware', 'cases': cases}
    output = Path(os.environ.get('DESKMATE_UI_CONTRACT_OUTPUT', tmp_path / 'control_e2e.json'))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')


def test_old_undo_plan_cannot_undo_a_different_episode(tmp_path):
    s = Simulation(tmp_path)
    try:
        s.start_focus(); s.proposal(s.low_light)
        state = s.respond('accept', s.low_light)
        before = len(s.commands)
        s.cache.put_feedback({'verdict': 'reject', 'request_id': 'atlas-display',
                              'undo_command_ids': ['stale-command']})
        s.step(s.low_light)
        assert len(s.commands) == before
        ids = [c.command_id for c in s.hub.control.history[-1].commands]
        s.cache.put_feedback({'verdict': 'reject', 'request_id': 'atlas-display', 'undo_command_ids': ids})
        s.step(s.low_light)
        assert len(s.commands) == before + 1
        assert s.commands[-1]['undo_of_command_id'] == ids[0]
    finally:
        s.close()
