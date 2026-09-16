"""features/baseline: 중앙값·MAD Modified z-score 기준선과 ingest 연동."""
from __future__ import annotations

import io
import json
import os

import pytest

from deskmate_hub.features import BaselineStore, median_mad, modified_z
from deskmate_hub.ingest import Sample, SensorCache, SessionTracker, build_frame, load_ingest_config
from deskmate_hub.inference import State
from deskmate_hub.live import LiveHub

BCFG = {"min_samples": 5, "z_full": 3.5, "default_mad_floor": 1e-3,
        "mad_floor": {"idle_ratio": 0.02}, "bucket_hours": 3}


def test_median_mad_and_modified_z():
    med, mad = median_mad([1, 2, 3, 4, 100])
    assert med == 3 and mad == 1
    # Iglewicz–Hoaglin: |z| > 3.5 가 이상치. 100 은 큰 이상치, 4 는 아님
    assert modified_z(100, med, mad, mad_floor=1e-3) > 3.5
    assert abs(modified_z(4, med, mad, mad_floor=1e-3)) < 3.5
    with pytest.raises(ValueError):
        median_mad([])


def test_store_calibration_and_normalize():
    store = BaselineStore(BCFG)
    assert store.normalize("idle_ratio", 0.5) is None            # 기준선 없음 → 폴백 신호
    store.begin_calibration()
    for x in (0.10, 0.12, 0.11, 0.13, 0.10, 0.12):
        store.observe("idle_ratio", x)
    store.observe("idle_ratio", None)                           # None 은 무시
    result = store.end_calibration(now=1_000_000.0)
    assert result == {"idle_ratio": True}
    b = store.baseline_for("idle_ratio")
    assert b.ready and b.sample_count == 6 and abs(b.median - 0.115) < 1e-9
    # 평소 수준 → 증거 ≈ 0, 크게 벗어나면 1 로 포화, 반대 방향은 0
    assert store.normalize("idle_ratio", 0.115) == pytest.approx(0.0, abs=1e-9)
    assert store.normalize("idle_ratio", 0.90) == 1.0
    assert store.normalize("idle_ratio", 0.01) == 0.0
    assert store.normalize("idle_ratio", 0.01, direction=-1) > 0.5
    # 표본 부족 지표는 확정되지 않는다
    store.begin_calibration()
    store.observe("co2_ppm", 700)
    assert store.end_calibration() == {"co2_ppm": False}
    assert store.normalize("co2_ppm", 1500) is None


def test_mad_floor_prevents_blowup():
    store = BaselineStore(BCFG)
    store.begin_calibration()
    for _ in range(6):
        store.observe("idle_ratio", 0.2)                        # 완전히 일정 → MAD 0
    store.end_calibration()
    assert store.normalize("idle_ratio", 0.2) == 0.0
    assert 0.0 < store.normalize("idle_ratio", 0.25) < 1.0     # floor 0.02 → z=0.6745*0.05/0.02≈1.69


def test_persist_roundtrip_and_forget(tmp_path):
    path = os.path.join(tmp_path, "baseline.json")
    store = BaselineStore(BCFG, persist_path=path)
    store.begin_calibration()
    for x in (300, 310, 305, 295, 300, 302):
        store.observe("co2_ppm", x)
    store.end_calibration(now=1_000_000.0)
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["schema"] == "baseline_record/1.0"
    rec = next(iter(saved["buckets"].values()))["co2_ppm"]
    assert set(rec) >= {"median", "mad", "sample_count"} and "_samples" not in rec   # 원시 표본 미저장
    reloaded = BaselineStore(BCFG, persist_path=path)
    assert reloaded.normalize("co2_ppm", 1500) == 1.0           # 이전 세션 seed 로 즉시 정규화
    reloaded.forget()
    assert not os.path.exists(path) and reloaded.normalize("co2_ppm", 1500) is None


def ks(now, idle):
    return Sample(received=now, ts=now, seq=None, data={
        "window_s": 60, "dwell_mean_ms": 90, "dwell_std_ms": 20, "flight_mean_ms": 150, "flight_std_ms": 45,
        "idle_ratio": idle, "correction_rate": 0.05, "typing_active": True, "mouse_active": True,
        "input_active": True, "flight_cv": 0.3, "mouse_event_rate": 30.0})


def mm(now, level=30):
    return Sample(received=now, ts=now, seq=None, data={
        "present": True, "motion_state": "active", "motion_level": level, "distance_cm": 55, "resp_bpm": 15,
        "resp_valid": True, "heart_bpm": None, "heart_valid": False, "drowsy_state": "AWAKE", "valid": True})


def test_live_hub_calibrates_in_start_then_uses_personal_baseline():
    cfg = load_ingest_config()
    assert cfg["normalization"] == "baseline"
    cfg = dict(cfg, baseline=dict(cfg["baseline"], min_samples=5))
    cache = SensorCache()
    hub = LiveHub(cache, ingest_cfg=cfg, out=io.StringIO())
    store = hub.tracker.baseline
    assert store is not None and not store.calibrating
    period, baseline_sec = hub.period, float(hub.fsm_cfg["timers"]["baseline_sec"])

    # 이 사용자는 평소 idle_ratio 0.5 (느긋한 타이핑) — 선형 램프로는 phi≈0.8 인 값
    t = 0.0
    while t <= baseline_sec + period:
        cache.put("mmwave", mm(t)); cache.put("keystroke", ks(t, 0.5 + 0.01 * ((t // period) % 3)))
        hub.tick_once(t)
        if hub.engine.state is State.START:
            assert store.calibrating
        t += period
    assert hub.engine.state in (State.FOCUS_PC, State.FOCUS_MIXED, State.CONTEXT_DETECT)
    assert not store.calibrating and store.baseline_for("idle_ratio").ready
    # 같은 0.5 는 이제 평소 수준 → 증거 0. 선형 폴백이었다면 (0.5-0.1)/(0.6-0.1)=0.8
    tr = SessionTracker(); tr.baseline = store
    cache.put("mmwave", mm(t)); cache.put("keystroke", ks(t, 0.5))
    f = build_frame(cache.snapshot(), t, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f.signals["keystroke"].phi == 0.0
    cache.put("keystroke", ks(t, 0.9))
    f = build_frame(cache.snapshot(), t, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f.signals["keystroke"].phi == 1.0
