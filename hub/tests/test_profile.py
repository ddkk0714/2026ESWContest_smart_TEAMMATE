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


def test_unknown_or_bad_profile_name_fails_loudly(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, "demo-z")
    with pytest.raises(FileNotFoundError, match="demo-z"):
        LiveHub(SensorCache(), out=io.StringIO())
    with pytest.raises(ValueError):
        load_profile("../fsm")


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
        for name in ("demo-a", "demo-b"):
            packaged = json.loads(archive.read(f"deskmate_hub/config/profiles/{name}.json"))
            assert packaged == load_profile(name)
        assert not any(n.startswith("deskmate_hub/config/profiles/") and n.endswith(".yaml") for n in names)
    # 보드와 같이 zip 에서 import 한 deskmate_hub.profile 이 YAML 없이 JSON 으로 프로파일을 읽는다
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); sys.modules['yaml'] = None\n"
            "from deskmate_hub.profile import load_profile\n"
            "print(json.dumps(load_profile('demo-b')['fsm']['timers']['baseline_sec']))")
    probe = subprocess.run([sys.executable, "-c", code, str(executable)], capture_output=True, text=True, cwd=str(tmp_path))
    assert probe.returncode == 0 and probe.stdout.strip() == "10", probe.stderr[-2000:]
