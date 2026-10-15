"""Feature-only ToF input reaches the same contract used by PC and Pi 4."""
import json
import io

import pytest

from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary
from deskmate_hub.ingest.mqtt_lines import MqttLineSource
from deskmate_hub.ingest.protocol import parse_sensor_message
from deskmate_hub.inference import State
from deskmate_hub.personalization.contract import feature_values
from deskmate_hub.live import LiveHub, frame_to_dict


def features(**changes):
    return {"present": True, "valid": True, "posture": "lean_forward",
            "motion_score": 0.2, "baseline_deviation": 0.6,
            "baseline_ready": True, "posture_confidence": 0.9,
            "grid_width": 8, "grid_height": 8, "valid_zones": 40, **changes}


def feed(cache, now=100, *, ts=None, **changes):
    payload = {"schema_version": "1.0", "ts": now if ts is None else ts,
               "seq": 1, "data": features(**changes)}
    return MqttLineSource(cache).feed_line(
        "MQTT\tdeskmate/sensor/tof/desk\t" + json.dumps(payload), now=now)


def frame(cache, cfg, now=100):
    return build_frame(cache.snapshot(), now, cfg, SessionTracker(), fsm_state=State.IDLE)


@pytest.fixture
def cfg():
    value = load_ingest_config()
    value["tof"]["enabled"] = True
    return value


def test_feature_only_mqtt_reaches_model_contract(cfg):
    cache = SensorCache()
    assert feed(cache, d=[123] * 64, st=[5] * 64) == "tof"
    result = frame(cache, cfg)
    assert result.present and result.touch
    signal = result.signals["posture"]
    assert signal.available and signal.phi == pytest.approx(0.2)
    assert signal.delta == pytest.approx(0.6)
    vector = feature_values(frame_to_dict(result))
    assert vector[3:6] == pytest.approx([0.2, 0.6, 1.0])
    summary = sensor_summary(cache.snapshot(), 100, cfg)
    assert summary["tof"]["posture"] == "lean_forward"
    assert summary["posture_source"] == "tof" and summary["valid"]
    assert "d" not in cache.tof.data and "st" not in cache.tof.data
    assert "d" not in summary["tof"] and "st" not in summary["tof"]


@pytest.mark.parametrize("changes", [
    {"valid": False}, {"baseline_ready": False}, {"posture": "unknown"},
    {"posture_confidence": 0.1}, {"baseline_deviation": None},
])
def test_unusable_tof_does_not_become_fatigue_evidence(cfg, changes):
    cache = SensorCache()
    assert feed(cache, **changes) == "tof"
    signal = frame(cache, cfg).signals["posture"]
    assert not signal.available and signal.phi == signal.delta == 0


def test_stale_measurement_and_stale_receipt_are_not_used(cfg):
    cache = SensorCache()
    feed(cache, ts=10)
    assert not frame(cache, cfg).present
    feed(cache)
    assert not frame(cache, cfg, 100 + cfg["freshness_sec"]["tof"] + 1).present


def test_away_is_absence_not_fatigue(cfg):
    cache = SensorCache()
    feed(cache, present=False, posture="away")
    result = frame(cache, cfg)
    assert not result.present and not result.signals["posture"].available


def test_presence_disagreement_retains_presence_but_blocks_posture(cfg):
    cache = SensorCache()
    feed(cache)
    from deskmate_hub.ingest import Sample
    cache.put("mmwave", Sample(100, 100, 1, {"present": False, "valid": True}))
    result = frame(cache, cfg)
    assert result.present and not result.signals["posture"].available
    assert not result.signals["respiration"].available
    assert sensor_summary(cache.snapshot(), 100, cfg)["tof"]["presence_conflict"]


def test_development_mapping_is_disabled_by_default():
    cache = SensorCache()
    feed(cache)
    cfg = load_ingest_config()
    assert cfg["tof"]["enabled"] is False
    assert not frame(cache, cfg).present
    assert not frame(cache, cfg).signals["posture"].available


def test_live_log_marks_tof_profile_without_raw_arrays(cfg):
    cache, log = SensorCache(), io.StringIO()
    feed(cache, d=[123] * 64, st=[5] * 64)
    hub = LiveHub(cache, ingest_cfg=cfg, frame_log=log, out=io.StringIO(),
                  control_cfg={"enabled": False}, personalization_cfg={"enabled": False})
    hub.tick_once(100)
    record = json.loads(log.getvalue())
    assert record["feature_profile"] == "tof_features_v1"
    assert record["signals"]["posture"]["available"]
    assert "d" not in record and "st" not in record
    assert hub._effective_model_config()["feature_profile"] == "tof_features_v1"


def test_tof_selection_does_not_fall_back_to_mmwave_posture(cfg):
    from deskmate_hub.ingest import Sample
    cache = SensorCache()
    cache.put("mmwave", Sample(100, 100, 1, {"present": True, "valid": True,
              "motion_level": 0, "drowsy_state": "DROWSY", "resp_valid": True}))
    result = frame(cache, cfg)
    assert result.present and not result.signals["posture"].available
    assert result.signals["respiration"].available


@pytest.mark.parametrize("changes", [
    {"motion_score": float("nan")}, {"baseline_deviation": float("inf")},
    {"motion_score": True}, {"posture": "invented"}, {"present": "false"},
    {"posture_confidence": 1.1}, {"grid_width": [8]},
    {"baseline_deviation": 10 ** 1000},
])
def test_invalid_tof_features_are_rejected(changes):
    payload = json.dumps({"ts": 100, "data": features(**changes)}).encode()
    assert parse_sensor_message("deskmate/sensor/tof/desk", payload, 100) is None


@pytest.mark.parametrize("ts", [None, True, "100", float("nan"), 10 ** 1000])
def test_missing_or_invalid_sample_timestamp_is_not_replaced_with_receive_time(ts):
    payload = json.dumps({"ts": ts, "data": features()}).encode()
    assert parse_sensor_message("deskmate/sensor/tof/desk", payload, 100) is None
