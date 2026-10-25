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
from deskmate_hub.learning import load_learning_config
from deskmate_hub.personalize import load_personalize_config
from deskmate_hub.live import LiveHub
from deskmate_hub.profile import PROFILE_ENV, apply_profile, apply_section, available, deep_merge, load_profile, profile_name

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "hub/deskmate_hub/config"


def test_deep_merge_overrides_nested_keys_keeps_others_and_leaves_base_alone():
    base = {"timers": {"a": 1, "b": 2}, "gate": {"x": 0.75}, "routing": {"posture": ["posture", "respiration"]}, "flag": False}
    out = deep_merge(base, {"timers": {"b": 20, "c": 3}, "routing": {"posture": ["posture"]}, "flag": True})
    assert out == {"timers": {"a": 1, "b": 20, "c": 3}, "gate": {"x": 0.75}, "routing": {"posture": ["posture"]}, "flag": True}
    assert base["timers"] == {"a": 1, "b": 2} and base["flag"] is False


def test_shipped_profiles_only_touch_keys_the_engine_knows():
    """오타·엔진이 모르는 키(fatigue_hold_min_frac 등)가 섞이면 조용히 무시되므로, 기본 설정에 있는 키만 허용한다."""
    fsm, ingest, personalize, learning = load_config(), load_ingest_config(), load_personalize_config(), load_learning_config()

    def check(overlay, base, path):
        for key, value in overlay.items():
            assert key in base, f"{path}{key}: 기본 설정에 없는 키"
            if isinstance(value, dict) and base[key]:                      # 기본이 빈 매핑({})이면 자유 항목(라벨별 하한 등)
                check(value, base[key], f"{path}{key}.")

    assert available() == ["demo-a", "demo-b", "demo-b-study"]
    for name in available():
        prof = load_profile(name)
        check(prof["fsm"], fsm, f"{name}:fsm.")
        check(prof["ingest"], ingest, f"{name}:ingest.")
        check(prof["personalize"], personalize, f"{name}:personalize.")
        check(prof["learning"], learning, f"{name}:learning.")
        # 학습기 울타리 keep_floor = 그 프로파일의 피로 확정 기준(기본 0.70) — 확정을 넘은 점수를 학습기가 못 내린다(PC 리허설 10-25)
        assert prof["learning"]["keep_floor"] == prof["fsm"].get("thresholds", {}).get("fatigue_confirm", fsm["thresholds"]["fatigue_confirm"])
        # 정책 문턱은 FSM 자동 문턱과 같아야 한다 — 다르면 정책이 FSM 자동을 제안으로 낮춘다(10-24 PC 리허설 결함)
        assert prof["personalize"]["gate"]["conf_auto"] == prof["fsm"]["gate"]["conf_auto"]
    assert learning["keep_floor"] == 0.85                                  # 보드 기본은 그대로
    assert fsm["weights"]["fatigue"]["npc"]["posture"] == 0.40            # 보드 기본 가중치도 그대로(엎드림 가중은 시연 프로파일만)
    for name in available():
        w = load_profile(name)["fsm"]["weights"]["fatigue"]
        assert w["npc"]["posture"] == 0.80 and w["pc"]["posture"] == 0.60 and set(w["pc"]) == set(w["npc"]) == set(fsm["weights"]["fatigue"]["pc"])
        assert abs(sum(w["pc"].values()) - 1.0) < 1e-9 and abs(sum(w["npc"].values()) - 1.0) < 1e-9
    a, b = load_profile("demo-a"), load_profile("demo-b")
    assert a["fsm"]["timers"]["monitor_window_sec"] == 30 and a["ingest"]["elapsed"]["reset_on_rest"] is True
    assert "baseline_sec" not in a["fsm"]["timers"] and b["fsm"]["timers"]["baseline_sec"] == 10
    assert a["fsm"]["timers"]["fatigue_hold_min_frac"] == 0.8 == b["fsm"]["timers"]["fatigue_hold_min_frac"]
    assert fsm["timers"]["fatigue_hold_min_frac"] == 1.0                   # 기본은 지금 동작(연속 유지)
    assert b["ingest"]["normalization"] == "linear" and b["fsm"]["thresholds"]["fatigue_confirm"] == 0.60
    # demo-b-study = demo-b 전부 + 수험생 자세 가중치(10-25)
    study = load_profile("demo-b-study")
    assert study["fsm"] == b["fsm"] and study["personalize"] == b["personalize"]
    assert {k: v for k, v in study["ingest"].items() if k != "tof"} == {k: v for k, v in b["ingest"].items() if k != "tof"}
    tof = study["ingest"]["tof"]
    assert {k: v for k, v in tof.items() if k not in ("posture_phi", "posture_delta")} == b["ingest"]["tof"]   # 규칙 플래그·끄덕임 상한 0.3 은 공통
    assert tof["posture_phi"] == {"lean_forward": 0.0, "forward_head": 0.1, "chin_rest": 0.3}
    assert tof["posture_delta"] == {"lean_forward": 0.0, "forward_head": 0.05, "chin_rest": 0.3}


def test_no_env_means_untouched_configs(monkeypatch):
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    fsm, ingest = load_config(), load_ingest_config()
    out_f, out_i, name = apply_profile(fsm, ingest)
    assert name is None and out_f is fsm and out_i is ingest
    hub = LiveHub(SensorCache(), out=io.StringIO())
    assert hub.profile is None and hub.fsm_cfg == load_config() and hub.engine.tm["monitor_window_sec"] == 0
    assert hub.personalize_cfg["gate"]["conf_auto"] == load_personalize_config()["gate"]["conf_auto"] == 0.75
    assert apply_section(fsm, "fsm", "") is fsm
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
    # personalize: 절 — 정책 자동 문턱도 0.60 으로(기본 0.75). 정책 객체가 실제로 그 설정을 들고 있는지까지
    assert hub.personalize_cfg["gate"]["conf_auto"] == 0.60 and hub.personalize_cfg["gate"]["undo_penalty_step"] == 0.05
    assert hub.personalize_policy is not None and hub.personalize_policy.cfg["gate"]["conf_auto"] == 0.60
    # learning: 절 — 학습기가 실제로 그 keep_floor 로 선다
    assert hub.learning_cfg["keep_floor"] == 0.70 and hub.learner is not None and hub.learner.c["keep_floor"] == 0.70
    # 건드리지 않은 부분은 기본과 같다
    # 건드리지 않은 부분은 기본과 같다(focus 가중치·tof). fatigue 가중치는 10-25 엎드림 시연을 위해 프로파일이 얹는다
    assert hub.fsm_cfg["weights"]["focus"] == load_config()["weights"]["focus"]
    capped = {"rule_drowsy_delta", "nod_delta_max", "min_confidence_by_label"}   # 시연: 0x03 플래그·끄덕임 δ 상한 0.3, face_down 신뢰도 하한 0.99 (10-25)
    assert {k: v for k, v in hub.ingest_cfg["tof"].items() if k not in capped} == {k: v for k, v in load_ingest_config()["tof"].items() if k not in capped}
    assert hub.ingest_cfg["tof"]["rule_drowsy_delta"] == 0.3 == hub.ingest_cfg["tof"]["nod_delta_max"]
    assert hub.fsm_cfg["weights"]["fatigue"]["npc"]["posture"] == 0.80

    monkeypatch.setenv(PROFILE_ENV, "demo-b-study")
    hub_s = LiveHub(SensorCache(), out=io.StringIO())
    assert hub_s.ingest_cfg["tof"]["posture_delta"]["forward_head"] == 0.05 and hub_s.ingest_cfg["tof"]["posture_delta"]["upright"] == 0.0
    assert hub_s.engine.tm["baseline_sec"] == 10 and hub_s.ingest_cfg["normalization"] == "linear"
    monkeypatch.setenv(PROFILE_ENV, "demo-b")
    hub_b = LiveHub(SensorCache(), out=io.StringIO())
    assert hub_b.engine.tm["baseline_sec"] == 10 and hub_b.fsm_cfg["gate"]["conf_auto"] == 0.55
    assert hub_b.fsm_cfg["thresholds"]["fatigue_confirm"] == 0.60 and hub_b.tracker.baseline is None
    assert hub_b.personalize_cfg["gate"]["conf_auto"] == 0.55
    assert hub_b.learner.c["keep_floor"] == 0.60 and LiveHub(SensorCache(), profile="", out=io.StringIO()).learner.c["keep_floor"] == 0.85
    # 명시적으로 넘긴 personalize 설정 위에도 얹는다(LiveHub 가 읽는 지점에서 적용)
    assert LiveHub(SensorCache(), personalize_cfg=load_personalize_config(), out=io.StringIO()).personalize_cfg["gate"]["conf_auto"] == 0.55
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


def test_unknown_or_bad_profile_name_fails_loudly(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, "demo-z")
    with pytest.raises(FileNotFoundError, match="demo-z"):
        LiveHub(SensorCache(), out=io.StringIO())
    with pytest.raises(ValueError):
        load_profile("../fsm")
    with pytest.raises(ValueError, match="모르는 절"):                 # control: 같은 다른 설정은 프로파일로 못 얹는다
        _load_inline({"fsm": {}, "control": {"x": 1}})


def _load_inline(data):
    """load_profile 의 절 검증만 따로 — 파일 없이 같은 검사를 돈다."""
    from unittest import mock

    import deskmate_hub.profile as profile_mod
    with mock.patch.object(profile_mod.pkgutil, "get_data", return_value=json.dumps(data).encode("utf-8")):
        return profile_mod.load_profile("inline-test")


def test_payload_bundles_profiles_as_json_readable_by_pkgutil(tmp_path):
    executable = tmp_path / "hub.elf"
    executable.write_bytes(b"ELF fixture")
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    result = subprocess.run([sys.executable, str(ROOT / "hub/atlas/tools/build_payload.py"),
                             "--executable", str(executable), "--hub", str(ROOT / "hub/deskmate_hub"),
                             "--config", str(CONFIG_DIR / "fsm.yaml"), "--ingest-config", str(CONFIG_DIR / "ingest.yaml"),
                             "--stdlib", str(stdlib)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(executable) as archive:
        names = archive.namelist()
        assert "deskmate_hub/profile.py" in names
        for name in ("demo-a", "demo-b", "demo-b-study"):
            packaged = json.loads(archive.read(f"deskmate_hub/config/profiles/{name}.json"))
            assert packaged == load_profile(name)
        assert not any(n.startswith("deskmate_hub/config/profiles/") and n.endswith(".yaml") for n in names)
    # 보드와 같이 zip 에서 import 한 deskmate_hub.profile 이 YAML 없이 JSON 으로 프로파일을 읽는다
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); sys.modules['yaml'] = None\n"
            "from deskmate_hub.profile import load_profile\n"
            "print(json.dumps(load_profile('demo-b')['fsm']['timers']['baseline_sec']))")
    probe = subprocess.run([sys.executable, "-c", code, str(executable)], capture_output=True, text=True, cwd=str(tmp_path))
    assert probe.returncode == 0 and probe.stdout.strip() == "10", probe.stderr[-2000:]
