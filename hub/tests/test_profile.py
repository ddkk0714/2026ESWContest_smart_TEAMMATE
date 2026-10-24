"""시연 프로파일 오버레이(DESKMATE_PROFILE=demo-a|demo-b, deskmate_hub/profile.py, 2026-10-24).

깊은 병합 · 환경변수로 LiveHub 에 얹힘 · 없으면 기본 설정 그대로 · 모르는 이름은 오류 · 보드 payload 에 JSON 으로 동봉.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from deskmate_hub.inference import load_config
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.live import LiveHub
from deskmate_hub.profile import PROFILE_ENV, apply_profile, available, deep_merge, load_profile, profile_name

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "hub/deskmate_hub/config"


def test_deep_merge_overrides_nested_keys_keeps_others_and_leaves_base_alone():
    base = {"timers": {"a": 1, "b": 2}, "gate": {"x": 0.75}, "routing": {"posture": ["posture", "respiration"]}, "flag": False}
    out = deep_merge(base, {"timers": {"b": 20, "c": 3}, "routing": {"posture": ["posture"]}, "flag": True})
    assert out == {"timers": {"a": 1, "b": 20, "c": 3}, "gate": {"x": 0.75}, "routing": {"posture": ["posture"]}, "flag": True}
    assert base["timers"] == {"a": 1, "b": 2} and base["flag"] is False


def test_shipped_profiles_only_touch_keys_the_engine_knows():
    """오타·엔진이 모르는 키(fatigue_hold_min_frac 등)가 섞이면 조용히 무시되므로, 기본 설정에 있는 키만 허용한다."""
    fsm, ingest = load_config(), load_ingest_config()

    def check(overlay, base, path):
        for key, value in overlay.items():
            assert key in base, f"{path}{key}: 기본 설정에 없는 키"
            if isinstance(value, dict):
                check(value, base[key], f"{path}{key}.")

    assert available() == ["demo-a", "demo-b"]
    for name in available():
        prof = load_profile(name)
        check(prof["fsm"], fsm, f"{name}:fsm.")
        check(prof["ingest"], ingest, f"{name}:ingest.")
    a, b = load_profile("demo-a"), load_profile("demo-b")
    assert a["fsm"]["timers"]["monitor_window_sec"] == 30 and a["ingest"]["elapsed"]["reset_on_rest"] is True
    assert "baseline_sec" not in a["fsm"]["timers"] and b["fsm"]["timers"]["baseline_sec"] == 10
    assert a["fsm"]["timers"]["fatigue_hold_min_frac"] == 0.8 == b["fsm"]["timers"]["fatigue_hold_min_frac"]
    assert fsm["timers"]["fatigue_hold_min_frac"] == 1.0                   # 기본은 지금 동작(연속 유지)
    assert b["ingest"]["normalization"] == "linear" and b["fsm"]["thresholds"]["fatigue_confirm"] == 0.60


def test_no_env_means_untouched_configs(monkeypatch):
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    fsm, ingest = load_config(), load_ingest_config()
    out_f, out_i, name = apply_profile(fsm, ingest)
    assert name is None and out_f is fsm and out_i is ingest
    hub = LiveHub(SensorCache(), out=io.StringIO())
    assert hub.profile is None and hub.fsm_cfg == load_config() and hub.engine.tm["monitor_window_sec"] == 0
    assert profile_name({PROFILE_ENV: "  "}) is None


def test_env_profile_overlays_live_hub(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, "demo-a")
    log = io.StringIO()
    hub = LiveHub(SensorCache(), out=log)
    assert hub.profile == "demo-a"
    assert hub.engine.tm["monitor_window_sec"] == 30 and hub.engine.tm["fatigue_confirm_hold_sec"] == 20
    assert hub.engine.tm["baseline_sec"] == 300                       # demo-a 는 보정 창을 안 건드린다
    assert hub.engine.tm["fatigue_hold_min_frac"] == 0.8 and hub.engine._hold_frac == 0.8   # 엔진 옵션까지 닿는다
    assert hub.fsm_cfg["gate"]["conf_auto"] == 0.60 and hub.fsm_cfg["thresholds"]["fatigue_confirm"] == 0.70
    assert hub.ingest_cfg["pc_ratio_window_sec"] == 120 and hub.tracker.elapsed_reset_on_rest is True
    assert hub.tracker.baseline is not None                           # normalization 은 여전히 baseline
    assert "[profile] DESKMATE_PROFILE=demo-a" in log.getvalue()
    # 건드리지 않은 부분은 기본과 같다
    assert hub.fsm_cfg["weights"] == load_config()["weights"] and hub.ingest_cfg["tof"] == load_ingest_config()["tof"]

    monkeypatch.setenv(PROFILE_ENV, "demo-b")
    hub_b = LiveHub(SensorCache(), out=io.StringIO())
    assert hub_b.engine.tm["baseline_sec"] == 10 and hub_b.fsm_cfg["gate"]["conf_auto"] == 0.55
    assert hub_b.fsm_cfg["thresholds"]["fatigue_confirm"] == 0.60 and hub_b.tracker.baseline is None
    # 명시적 설정을 줘도(run_live --config) 그 위에 얹는다. profile="" 은 환경변수를 무시한다(service_bridge 가 이미 얹은 뒤)
    assert LiveHub(SensorCache(), fsm_cfg=load_config(), out=io.StringIO()).engine.tm["baseline_sec"] == 10
    assert LiveHub(SensorCache(), profile="", out=io.StringIO()).engine.tm["baseline_sec"] == 300
    assert LiveHub(SensorCache(), profile="", out=io.StringIO()).engine._hold_frac == 1.0


def test_demo_profile_relaxed_hold_reaches_fatigue_where_default_resets(monkeypatch):
    """1fd856d 의 완화 유지가 프로파일을 통해 실제로 켜지는지 — 6 tick 마다 한 번 임계 아래로 떨어지는 입력에서
    기본(1.0)은 FATIGUE 에 못 가고 demo-a(0.8)는 간다. 엔진 옵션 자체의 시험은 test_fsm_transitions 에 있다."""
    from deskmate_hub.inference import FSMEngine, State
    from test_fsm_transitions import drive_to_focus, frame

    def reaches_fatigue(fsm_cfg) -> bool:
        e = FSMEngine(fsm_cfg)
        t = drive_to_focus(e)
        hot = {"posture": {"delta": 0.95}, "elapsed": {"delta": 0.95}, "keystroke": {"delta": 0.95}}
        mid = {"posture": {"delta": 0.55}, "elapsed": {"delta": 0.55}, "keystroke": {"delta": 0.55}}   # 0.40 위·0.70 아래
        seen = set()
        for i in range(1, 90):
            seen.add(e.tick(frame(t + i * 10, sig=mid if i % 6 == 0 else hot)).state)
        assert State.FATIGUE_SUSPECT in seen
        return State.FATIGUE in seen

    monkeypatch.delenv(PROFILE_ENV, raising=False)
    default_cfg = LiveHub(SensorCache(), out=io.StringIO()).fsm_cfg
    monkeypatch.setenv(PROFILE_ENV, "demo-a")
    demo_cfg = LiveHub(SensorCache(), out=io.StringIO()).fsm_cfg
    # demo-a 는 유지 20 s 라 그것만으로도 빨리 가므로, 비율 옵션만 떼어 보려면 유지 시간은 기본으로 되돌린다
    demo_hold_only = deep_merge(default_cfg, {"timers": {"fatigue_hold_min_frac": demo_cfg["timers"]["fatigue_hold_min_frac"]}})
    assert reaches_fatigue(default_cfg) is False and reaches_fatigue(demo_hold_only) is True and reaches_fatigue(demo_cfg) is True
