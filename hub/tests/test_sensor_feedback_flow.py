"""Real sensor payload contracts and production FSM settings, without hardware."""
import io
import json
import pytest

from deskmate_hub.inference import State, load_config
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.live import LiveHub


@pytest.mark.parametrize("verdict", ["accept", "reject", "timeout"])
def test_sensor_changes_wait_for_acceptance_and_recover(tmp_path, verdict):
    cache = SensorCache()
    states, requests = [], []
    hub = LiveHub(cache, publish=states.append, publish_request=requests.append,
                  control_cfg={"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")},
                  out=io.StringIO())
    cfg = load_config()
    period = cfg["timers"]["score_period_sec"]

    def tick(now, fatigued=False, rising=False):
        messages = {
            "mmwave/node": {"present": True, "motion_level": 5 if fatigued else 40,
                            "drowsy_state": "DROWSY" if fatigued else "AWAKE",
                            "resp_bpm": 15, "resp_valid": True, "valid": True},
            "env/node": {"co2_ppm": 850 if rising else 800, "co2_valid": True, "temp_c": 24,
                         "temp_valid": True, "humidity_pct": 45, "humidity_valid": True,
                         "lux": 300, "lux_valid": True},
            "keystroke": {"typing_active": True, "input_active": True,
                          "idle_ratio": .1, "flight_cv": .35 if fatigued else .25,
                          "correction_rate": .01, "dwell_mean_ms": 90,
                          "flight_mean_ms": 140, "window_s": 60},
        }
        for topic, data in messages.items():
            payload = json.dumps({"schema_version": "1.0", "ts": now, "data": data}).encode()
            assert route_mqtt_message(cache, "deskmate/sensor/" + topic, payload, now)
        return hub.tick_once(now)

    now = 1000.0
    while hub.engine.state is not State.FOCUS_PC:
        tick(now)
        now += period
        assert now < 1400
    assert hub.tracker.baseline.snapshot()["session"]  # Real START calibration.

    for _ in range(100):
        tick(now, fatigued=True)
        now += period
        if hub.pending_request is not None:
            break
    assert requests and hub.engine.state is State.ACTION_BREAK
    assert hub.pending_request["data"]["kind"] == "break_suggest"
    assert states[-1]["data"]["gate"] == "suggest"
    summary = states[-1]["data"]["sensor_summary"]
    assert "keystroke" in summary and "mmwave" in summary and "co2_ppm" in summary
    visited = {state["data"]["fsm_state"] for state in states}
    assert {"START", "FOCUS_PC", "FATIGUE_SUSPECT", "FATIGUE", "CAUSE_ANALYSIS"} <= visited

    # An actual person may need more than one 10-second tick to tap the card.
    for _ in range(3):
        waiting = tick(now, fatigued=True, rising=True)
        now += period
        assert hub.engine.state is State.ACTION_BREAK
        assert waiting["data"]["gate"] == "suggest"
        assert waiting["data"]["cause"] == "cognitive"
    request_id = requests[-1]["data"]["request_id"]
    if verdict == "timeout":
        tick(requests[-1]["ts"] + requests[-1]["data"]["expires_in_s"], fatigued=True)
        assert hub.pending_request is None
        assert hub.engine.state is State.MONITOR
        return
    route_mqtt_message(cache, "deskmate/feedback/user", json.dumps({
        "schema_version": "1.0", "data": {
            "request_id": request_id, "verdict": verdict, "response_ms": 30000,
        },
    }).encode(), now)
    tick(now, fatigued=True)
    if verdict == "reject":
        assert hub.pending_request is None
        assert hub.engine.state is State.MONITOR
        return
    assert hub.engine.state is State.REST
    for _ in range(int(cfg["timers"]["rest_min_sec"] / period) + 2):
        now += period
        tick(now)
        if hub.engine.state is State.RECOVERY:
            break
    assert hub.engine.state is State.RECOVERY
