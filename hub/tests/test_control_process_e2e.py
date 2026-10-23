"""Headless version of the independent Hub/Gateway test; Flutter is explicit."""
import pytest
pytest.importorskip('amqtt')
pytest.importorskip('paho.mqtt.client')
from control_process_e2e import fixture_hub, prewarm, readiness, run_scenario, sensor_tick


def prepared(tmp_path):
    sent = []
    hub, registry, states = fixture_hub(tmp_path, lambda _: None, lambda _: None, sent.append)
    now, _ = prewarm(hub, registry, states, 1000)
    assert hub.environment_policy.rules['lux_low']['minimum_duration_sec'] == 30
    assert readiness(hub, now)['eligible']
    return hub, registry, states, sent, now


@pytest.mark.parametrize('held,expected', [(0, 0), (29.999, 0), (30, 1)])
def test_rewind_requires_full_duration_before_matching_user_acceptance(tmp_path, held, expected):
    hub, registry, states, sent, now = prepared(tmp_path)
    now -= 2.4  # Explicit synthetic clock fault, never a real clock adjustment.
    sensor_tick(hub, registry, states, now, low_light=True)
    assert not readiness(hub, now)['eligible'] and not sent
    cursor, end = now, now + held
    while cursor < end:
        cursor = min(cursor + 10, end)
        sensor_tick(hub, registry, states, cursor, low_light=True)
    assert readiness(hub, end)['eligible'] is bool(expected)
    request_id = hub.pending_request['data']['request_id']
    hub.cache.put_feedback({'verdict': 'accept', 'request_id': request_id}, received=end)
    sensor_tick(hub, registry, states, end, low_light=True)
    assert len(sent) == expected
    # Replaying an acceptance never turns a skipped command into a retry,
    # nor sends the successfully dispatched command a second time.
    hub.cache.put_feedback({'verdict': 'accept', 'request_id': request_id}, received=end)
    sensor_tick(hub, registry, states, end, low_light=True)
    assert len(sent) == expected


@pytest.mark.parametrize('fault', ['wrong_request', 'expired_request', 'external_operation', 'missing', 'offline'])
def test_real_hub_blocks_invalid_acceptance_even_with_prewarmed_evidence(tmp_path, fault):
    from deskmate_hub.ingest import Sample
    hub, registry, states, sent, now = prepared(tmp_path)
    request_id = hub.pending_request['data']['request_id']
    if fault == 'expired_request':
        end = hub.pending_request['ts'] + hub.pending_request['data']['expires_in_s'] + .001
        while now < end:
            now = min(now + 10, end)
            sensor_tick(hub, registry, states, now, low_light=True)
    else:
        now += 1
        hub.cache.put('env', Sample(now, now, None, {'lux': 50, 'lux_valid': True}))
        if fault == 'wrong_request': request_id = 'expired-or-unrelated-request'
        elif fault == 'external_operation':
            registry.observe('mock_lamp', {'set_brightness': 20}, now)
        elif fault == 'missing': hub.cache.put('env', None)
        elif fault == 'offline': registry.devices['mock_lamp'].online = False
    hub.cache.put_feedback({'verdict': 'accept', 'request_id': request_id}, received=now)
    hub.tick_once(now)
    assert not sent


def test_test_readiness_rejects_a_plan_that_does_not_match_dispatcher(tmp_path):
    hub, registry, states, sent, now = prepared(tmp_path)
    hub.pending_request['data']['control_plan']['commands'][0]['value'] = 99
    assert not readiness(hub, now)['eligible'] and not sent


def test_wait_deadline_uses_monotonic_without_reading_wall_clock(monkeypatch):
    import test_mqtt_reconnect as helper
    from types import SimpleNamespace
    readings = iter([10, 10.1, 11.1])
    def forbidden_wall(): raise AssertionError('wall clock used for timeout')
    monkeypatch.setattr(helper, 'time', SimpleNamespace(monotonic=lambda: next(readings),
                                                     sleep=lambda _: None, time=forbidden_wall))
    assert helper._wait(lambda: False, timeout=1) is False


def test_independent_hub_and_gateway_processes_use_real_mqtt(tmp_path):
    result = run_scenario(tmp_path)
    assert result['result'] == 'passed' and result['mode'] == 'mock'
    assert result['hub_pid'] != result['gateway_pid']
    assert result['commands'] == result['adapter_calls'] == 1
    assert result['state_confirmed'] and result['fsm_state'] == 'MONITOR'
    assert not result['flutter_process'] and not result['hardware']
