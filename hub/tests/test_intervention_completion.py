# Deadline and closed-episode regressions for the intervention cycle.
import io
import json
import pytest
from deskmate_hub.control import ControlDispatcher, load_control_config
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.inference import GateMode
from deskmate_hub.live import LiveHub


def dispatcher():
    sent = []
    cfg = load_control_config()
    return ControlDispatcher(cfg, publish_cmd=sent.append), sent


def test_slow_accept_gets_new_command_deadline():
    control, sent = dispatcher()
    episode = control.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 100)
    control.on_feedback('accept', 150)
    assert episode.executed_ts == 150
    assert all(command['expires_ts_ms'] == 165000 for command in sent)
    assert control.on_result({'command_id': sent[0]['command_id'], 'status': 'succeeded'}, 164)


@pytest.mark.parametrize('late', [180, 181])
def test_accept_at_or_after_suggestion_deadline_does_not_execute(late):
    control, sent = dispatcher()
    episode = control.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 100)
    control.on_feedback('accept', 100 + late)
    assert sent == [] and episode.outcome == 'expired'


def test_closed_undo_results_timeout_and_late_success_is_ignored():
    control, sent = dispatcher()
    episode = control.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 100)
    for command in list(sent):
        control.on_result({'command_id': command['command_id'], 'status': 'succeeded'}, 101)
    control.close_episode()
    control.on_feedback('reject', 120)
    control.tick(135)
    assert episode.outcome == 'undone'
    assert all(command.status == 'timeout' for command in episode.commands if command.gate == 'undo')
    assert not control.on_result({'command_id': sent[-1]['command_id'], 'status': 'succeeded'}, 136)


def hub_with_request(tmp_path):
    config = load_control_config()
    config['adapter'] = 'mqtt'
    config['esm_log_path'] = str(tmp_path / 'esm.jsonl')
    sent = []
    hub = LiveHub(SensorCache(), control_cfg=config, publish_control=sent.append, out=io.StringIO(),
                  personalization_cfg={'enabled': False})
    hub.tick_once(100)
    hub.control.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 100)
    hub.pending_request = {'ts':100, 'data':{'request_id':'req', 'kind':'env_suggest',
                                           'expires_in_s':180, 'cause':'environment'}}
    return hub, sent


def test_display_absence_records_timeout_once_and_rejects_stale_accept(tmp_path):
    hub, sent = hub_with_request(tmp_path)
    hub.tick_once(280)
    hub.cache.put_feedback({'request_id':'req', 'verdict':'accept'})
    hub.tick_once(290)
    labels = [record for record in hub.recorder.r.esm_labels if record.get('label_id')]
    assert len(labels) == 1 and labels[0]['verdict'] == 'timeout' and labels[0]['source'] == 'hub'
    assert not sent and hub.pending_request is None
    assert hub.report_envelope(300)['data']['metrics']['timeout_rate'] == 1.0


def test_received_before_deadline_accept_survives_tick_delay(tmp_path):
    hub, sent = hub_with_request(tmp_path)
    route_mqtt_message(hub.cache, 'deskmate/feedback/user',
                       json.dumps({'request_id':'req', 'verdict':'accept'}).encode(), 279)
    hub.tick_once(280)
    assert len(sent) == 2
    assert all(command['expires_ts_ms'] == 295000 for command in sent)
    assert hub.report_envelope(280)['data']['metrics']['suggest_accept_rate'] == 1.0


def test_result_received_on_time_survives_next_tick_and_duplicate_is_ignored(tmp_path):
    hub, sent = hub_with_request(tmp_path)
    hub.cache.put_feedback({'request_id':'req', 'verdict':'accept'})
    hub.tick_once(150)
    for command in sent:
        payload = json.dumps({'command_id':command['command_id'], 'status':'succeeded'}).encode()
        route_mqtt_message(hub.cache, 'deskmate/control/result', payload, 164)
    hub.tick_once(170)
    report = hub.report_envelope(170)['data']['control_results']['forward']
    assert report['succeeded'] == 2 and report['timeout'] == 0
    hub.cache.put_control_result({'command_id':sent[0]['command_id'], 'status':'failed'})
    hub.tick_once(180)
    assert hub.report_envelope(180)['data']['control_results']['forward']['succeeded'] == 2


def test_late_result_cannot_change_timeout_into_success(tmp_path):
    hub, sent = hub_with_request(tmp_path)
    hub.cache.put_feedback({'request_id':'req', 'verdict':'accept'})
    hub.tick_once(150)
    for command in sent:
        payload = json.dumps({'command_id':command['command_id'], 'status':'succeeded'}).encode()
        route_mqtt_message(hub.cache, 'deskmate/control/result', payload, 166)
    hub.tick_once(170)
    result = hub.report_envelope(170)['data']['control_results']['forward']
    assert result['timeout'] == 2 and result['succeeded'] == 0


def test_mismatched_actual_value_is_failed_and_cannot_be_counted_as_undo():
    control, sent = dispatcher()
    episode = control.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 100)
    for command in sent:
        control.on_result({'command_id':command['command_id'], 'status':'succeeded', 'actual_value':'wrong'}, 101)
    assert all(command.status == 'failed' for command in episode.commands)
    control.close_episode()
    control.on_feedback('reject', 110)
    assert len(sent) == 2 and episode.outcome == 'executed'


def test_correction_and_valid_accept_in_same_tick_are_both_processed(tmp_path):
    hub, sent = hub_with_request(tmp_path)
    for payload in ({'verdict':'correct', 'corrected_state':'REST'}, {'verdict':'accept', 'request_id':'req'}):
        route_mqtt_message(hub.cache, 'deskmate/feedback/user', json.dumps(payload).encode(), 279)
    hub.tick_once(280)
    assert len(sent) == 2
    metrics = hub.report_envelope(280)['data']['metrics']
    assert metrics['correction_count'] == 1 and metrics['suggest_accept_rate'] == 1.0
    assert metrics['timeout_rate'] == 0.0


def test_bounded_feedback_queue_keeps_received_times_aligned():
    cache = SensorCache()
    for seq in range(18):
        cache.put_feedback({'seq':seq}, received=100 + seq)
    assert cache.pop_feedback() == {'seq':2}
    events = cache.pop_feedback_events()
    assert events == [({'seq':seq}, 100 + seq) for seq in range(3, 18)]
    assert cache.pop_feedback_event() == (None, None)
