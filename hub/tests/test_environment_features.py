"""환경 측정별 유효성과 세션 기준값을 실제 SensorCache 경로로 검증한다."""
from __future__ import annotations

import pytest

from deskmate_hub.ingest import (
    Sample, SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary,
)
from deskmate_hub.inference import State


@pytest.fixture
def cfg():
    return load_ingest_config()


def reading(now, *, co2=None, temp=None, humidity=None, lux=None,
            co2_valid=None, temp_valid=None, humidity_valid=None, lux_valid=None):
    return Sample(received=now, ts=now, seq=None, data={
        "co2_ppm": co2, "temp_c": temp, "humidity_pct": humidity, "lux": lux,
        "co2_valid": co2 is not None if co2_valid is None else co2_valid,
        "temp_valid": temp is not None if temp_valid is None else temp_valid,
        "humidity_valid": humidity is not None if humidity_valid is None else humidity_valid,
        "lux_valid": lux is not None if lux_valid is None else lux_valid,
    })


def frame(cache, now, cfg, tracker=None):
    return build_frame(cache.snapshot(), now, cfg, tracker or SessionTracker(), fsm_state=State.FOCUS_PC)


def test_other_measurements_work_when_co2_invalid(cfg):
    cache = SensorCache()
    cache.put("env", reading(0, co2=2000, co2_valid=False, temp=30, humidity=45, lux=50))
    signal = frame(cache, 0, cfg).signals["environment"]
    assert signal.available
    assert signal.phi == pytest.approx(1.0)
    assert signal.delta == pytest.approx(1.0)
    summary = sensor_summary(cache.snapshot(), 0, cfg)
    assert "co2_ppm" not in summary
    assert summary["env_flags"] == ["too_hot", "too_dark"]


def test_all_invalid_or_null_is_unavailable(cfg):
    cache = SensorCache()
    cache.put("env", reading(0, co2=2000, temp=30, humidity=20, lux=50,
                             co2_valid=False, temp_valid=False,
                             humidity_valid=False, lux_valid=False))
    assert not frame(cache, 0, cfg).signals["environment"].available
    assert "env_flags" not in sensor_summary(cache.snapshot(), 0, cfg)
    cache.put("env", reading(1, co2_valid=True, temp_valid=True,
                             humidity_valid=True, lux_valid=True))
    assert not frame(cache, 1, cfg).signals["environment"].available


def test_co2_rise_uses_first_valid_measurement_after_session_start(cfg):
    cache, tracker = SensorCache(), SessionTracker()
    cache.put("env", reading(0, co2=300))
    frame(cache, 0, cfg, tracker)
    assert tracker.co2_session_start is None
    tracker.observe_state(State.START, 1)
    frame(cache, 1, cfg, tracker)
    assert tracker.co2_session_start is None  # 시작 전 표본을 새 세션 기준으로 재사용하지 않는다.
    cache.put("env", reading(1, co2=500, co2_valid=False))
    frame(cache, 1, cfg, tracker)
    assert tracker.co2_session_start is None
    cache.put("env", reading(2, co2=500))
    frame(cache, 2, cfg, tracker)
    assert tracker.co2_session_start == 500
    cache.put("env", reading(3, co2=1000))
    signal = frame(cache, 3, cfg, tracker).signals["environment"]
    assert signal.delta == pytest.approx(0.75)
    cache.put("env", reading(4, co2=1100))
    signal = frame(cache, 4, cfg, tracker).signals["environment"]
    assert signal.delta == pytest.approx(1.0)
    assert sensor_summary(cache.snapshot(), 4, cfg, tracker)["env_flags"] == ["co2_rising"]
    assert "env_flags" not in sensor_summary(cache.snapshot(), 4, cfg)


def test_session_end_resets_co2_start_and_outside_session_has_no_rise(cfg):
    cache, tracker = SensorCache(), SessionTracker()
    tracker.observe_state(State.START, 0)
    cache.put("env", reading(0, co2=500))
    frame(cache, 0, cfg, tracker)
    tracker.observe_state(State.IDLE, 1)
    assert tracker.co2_session_start is None
    cache.put("env", reading(2, co2=1000))
    assert frame(cache, 2, cfg, tracker).signals["environment"].delta == pytest.approx(200 / 700)
    assert tracker.co2_session_start is None
    tracker.observe_state(State.START, 3)
    cache.put("env", reading(3, co2=1000))
    frame(cache, 3, cfg, tracker)
    tracker.observe_state(State.END, 4)
    assert tracker.co2_session_start is None


@pytest.mark.parametrize("temp,expected,flag", [
    (30, 1.0, "too_hot"), (18, 0.5, "too_cold"), (23, 0.0, None),
])
def test_temperature_deviation(cfg, temp, expected, flag):
    cache = SensorCache()
    cache.put("env", reading(0, temp=temp))
    assert frame(cache, 0, cfg).signals["environment"].phi == pytest.approx(expected)
    assert sensor_summary(cache.snapshot(), 0, cfg).get("env_flags") == ([flag] if flag else None)


@pytest.mark.parametrize("humidity,expected,flag", [
    (70, 2 / 3, "too_humid"), (20, 2 / 3, "too_dry"), (45, 0.0, None),
])
def test_humidity_deviation(cfg, humidity, expected, flag):
    cache = SensorCache()
    cache.put("env", reading(0, humidity=humidity))
    assert frame(cache, 0, cfg).signals["environment"].phi == pytest.approx(expected)
    assert sensor_summary(cache.snapshot(), 0, cfg).get("env_flags") == ([flag] if flag else None)


@pytest.mark.parametrize("lux,expected", [(50, 1.0), (200, 0.5), (400, 0.0)])
def test_lux_dim(cfg, lux, expected):
    cache = SensorCache()
    cache.put("env", reading(0, lux=lux))
    assert frame(cache, 0, cfg).signals["environment"].delta == pytest.approx(expected)


def test_phi_delta_take_maximum_and_flags_keep_order(cfg):
    cache = SensorCache()
    cache.put("env", reading(0, co2=940, temp=28, humidity=20, lux=140))
    signal = frame(cache, 0, cfg).signals["environment"]
    assert signal.delta == pytest.approx(0.8)  # CO₂ 0.2, 조도 0.8
    assert signal.phi == pytest.approx(2 / 3)  # 온도 0.5, 습도 2/3
    assert sensor_summary(cache.snapshot(), 0, cfg)["env_flags"] == [
        "too_hot", "too_dry", "too_dark",
    ]


def test_stale_environment_has_no_flags(cfg):
    cache = SensorCache()
    cache.put("env", reading(0, co2=2000, temp=30, humidity=20, lux=50))
    now = cfg["freshness_sec"]["env"] + 1
    assert not frame(cache, now, cfg).signals["environment"].available
    assert "env_flags" not in sensor_summary(cache.snapshot(), now, cfg)


def test_co2_absolute_still_uses_tracker_evidence(cfg, monkeypatch):
    cache, tracker = SensorCache(), SessionTracker()
    cache.put("env", reading(0, co2=900))
    calls = []

    def evidence(metric, value, linear, *, now=None):
        calls.append((metric, value, linear, now))
        return 0.7

    monkeypatch.setattr(tracker, "evidence", evidence)
    assert frame(cache, 0, cfg, tracker).signals["environment"].delta == pytest.approx(0.7)
    assert calls == [("co2_ppm", 900.0, pytest.approx(1 / 7), 0)]
