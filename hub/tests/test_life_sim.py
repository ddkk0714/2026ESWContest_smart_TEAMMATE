"""생활 각본 폐루프 시뮬(tools/life_sim) — 졸다 깨는 한 바퀴가 hub 의 실제 입력 경로로 닫히는지 고정한다.

1) 합성 격자 + 오라클 자세: tofsim·torch 없이 돈다. 꾸벅 졸음 → FATIGUE → ACTION_POSTURE(자세 알림) → 사람이 깸 → 자세 복귀 →
   C_fatigue 하락 → RECOVERY/FOCUS. 0x05 격자는 UART 인코더·TofGridAssembler 를, 0x03 특징은 인코더를 지나 캐시에 들어간다.
2) numpy 백엔드 짧은 각본: tofsim(dataset/tof_sim_v5 사본, TOFSIM_ROOT)이 있을 때만. 렌더·규칙 특징·hub tick 이 끝까지 돈다.
"""
from __future__ import annotations

import os

import pytest

from tools.life_sim.scenario import SCENARIO_DEFAULTS, deep_merge, load_defaults, resolve_reaction, start_epoch

SEGMENTS = [
    {"kind": "away", "seconds": 6},
    {"kind": "upright", "seconds": 320, "typing": True},
    {"kind": "drowsy", "seconds": 1300, "typing": False,
     "nods": {"interval_s": [4, 8], "micro_sleep_p": 0.6, "hold_s": [4, 8], "first_s": 10}},
    {"kind": "upright", "seconds": 60, "typing": True},
]


def _scenario(segments, *, reaction=None, name="t", seed=5):
    reaction_cfg, devices_cfg, policy_cfg = load_defaults()
    sc = deep_merge(SCENARIO_DEFAULTS, {"name": name, "seed": seed, "segments": segments, "env": {"co2_ppm": 1000}})
    sc["reaction_resolved"] = resolve_reaction(reaction_cfg, "default", reaction or {})
    sc["devices_resolved"] = devices_cfg
    sc["policy_resolved"] = policy_cfg
    sc["t0"] = start_epoch("2026-10-22", "14:00")
    sc["path"] = "<test>"
    return sc


def test_synthetic_closed_loop_wakes_and_recovers(tmp_path):
    from tools.life_sim.harness import run_scenario
    from tools.posture_sim.synthetic import SyntheticPerson

    sc = _scenario(SEGMENTS, reaction={"wake": {"screen_notice": 1.0, "lamp_cool": 1.0, "tts": 1.0},
                                       "wake_delay_s": [5, 10], "awake_s": [1200, 1200]})
    src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=10.0)
    res = run_scenario(sc, out_dir=str(tmp_path), source=src, progress=False)

    seq = res["fsm"]["states_seq"]
    for s in ("START", "FOCUS_PC", "FATIGUE_SUSPECT", "FATIGUE", "ACTION_POSTURE"):
        assert s in seq, seq
    assert res["n_interventions"] >= 1
    iv = res["interventions"][0]
    assert iv["kind"].startswith("posture_"), iv          # 졸음은 posture 그룹으로 라우팅된다(fsm.yaml routing)
    assert iv["cause"] == "posture"
    assert res["wake_success"] and res["wakes"], res["reaction_log"]
    assert res["after_wake"]["c_fatigue_min_after"] < 0.45
    assert any(s in seq for s in ("RECOVERY", "FOCUS_MIXED", "FOCUS_PC", "FOCUS_NPC"))
    assert res["posture"]["drowsy_flag_recall_0x03"] > 0.5  # 0x03 규칙 끄덕임 검출기가 drowsy 플래그를 낸다
    assert res["posture"]["acc_sec"] > 0.95                 # 오라클이라 거의 1 (전이 제외)
    for f in ("frames.jsonl", "ticks.jsonl", "events.jsonl", "summary.json", "summary.md", "hub.log"):
        assert os.path.exists(os.path.join(res["run_dir"], f))


def test_synthetic_never_wakes_reaches_rest(tmp_path):
    from tools.life_sim.harness import run_scenario
    from tools.posture_sim.synthetic import SyntheticPerson

    segs = [{"kind": "away", "seconds": 6}, {"kind": "upright", "seconds": 320, "typing": True},
            {"kind": "drowsy", "seconds": 2400, "typing": False,
             "nods": {"interval_s": [4, 8], "micro_sleep_p": 0.7, "hold_s": [6, 12], "first_s": 10}}]
    off = {k: 0.0 for k in ("screen_notice", "screen_card", "lamp_cool", "lamp_bright", "lamp_warm", "fan_on", "fan_high", "tts",
                            "window_open")}
    none = {"accept": 0.0, "reject": 0.0, "none": 1.0}
    sc = _scenario(segs, name="t3", seed=9, reaction={"wake": off, "card": {"posture_suggest": none, "env_suggest": none,
                                                                            "break_suggest": none}, "rest": {"go_away_p": 0.0},
                                                      "undo": {"p": 0.0}})
    src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=10.0)
    res = run_scenario(sc, out_dir=str(tmp_path), source=src, progress=False)
    seq = res["fsm"]["states_seq"]
    assert "ESCALATE" in seq and "REST" in seq, seq
    kinds = [iv["kind"] for iv in res["interventions"]]
    assert kinds[0].startswith("posture_"), kinds
    assert any(k.startswith("env_") for k in kinds) and any(k == "rest_forced" for k in kinds), kinds
    assert not res["wakes"]


def test_hub_personalize_engine_ab_synthetic(tmp_path):
    """policy.engine: hub — hub/deskmate_hub/personalize 의 밴딧이 LiveHub 안에서 arm·게이트를 고르고, A(합성 30 일)·B(전역) 가
    personalize.jsonl 에 에피소드를 남긴다. 날씨 토픽이 캐시에 들어가고, A 는 개인 모드로 시작한다."""
    pytest.importorskip("deskmate_hub.personalize")
    from tools.life_sim.ab import run_ab
    from tools.posture_sim.synthetic import SyntheticPerson

    segs = [{"kind": "away", "seconds": 6}, {"kind": "upright", "seconds": 320, "typing": True, "env": {"co2_to": 1300}},
            {"kind": "drowsy", "seconds": 1500, "typing": False,
             "nods": {"interval_s": [4, 8], "micro_sleep_p": 0.6, "hold_s": [4, 8], "first_s": 10}},
            {"kind": "upright", "seconds": 300, "typing": True}]
    sc = _scenario(segs, name="hub", seed=21)
    sc["persona"] = "student"
    sc["policy"] = {"engine": "hub", "profile": "none", "days": 5}
    sc["weather"] = {"temp_c": 10.0, "humidity": 50, "wind_ms": 6.0, "condition": "clear", "season": "autumn", "source": "test"}
    cmp = run_ab(sc, out_dir=str(tmp_path), model_path=None, posture="oracle", progress=False,
                 source_factory=lambda s: SyntheticPerson(s["segments"], seed=s["seed"], fps=10.0))
    a, b = cmp["rows"]
    assert a["engine"] == "hub" and b["engine"] == "hub"
    for tag in ("A", "B"):
        d = os.path.join(str(tmp_path), f"student_hub_{tag}")
        assert os.path.exists(os.path.join(d, "personalize.jsonl")), tag
    assert os.path.exists(os.path.join(str(tmp_path), "student_hub_A", "policy.json"))
    assert a["checks"]["n_feedback"] > b["checks"]["n_feedback"]          # A 는 합성 학습을 거쳤다
    assert a["checks"]["episodes"] >= 1 and b["checks"]["episodes"] >= 1
    assert any(k.startswith("posture_") for k in a["kinds"]), a["kinds"]


def test_numpy_backend_short_script(tmp_path):
    from tools.posture_sim import tofsim_bridge
    if tofsim_bridge.load() is None:
        pytest.skip("tofsim 없음 (TOFSIM_ROOT 를 dataset/tof_sim_v5 사본으로)")
    from tools.life_sim.harness import run_scenario

    segs = [{"kind": "away", "seconds": 4}, {"kind": "upright", "seconds": 40, "typing": True},
            {"kind": "drowsy", "seconds": 40, "typing": False, "nods": {"interval_s": [3, 5], "first_s": 3}}]
    sc = _scenario(segs, name="np", seed=3)
    sc["backend"] = "numpy"
    sc["placement"], sc["scene"], sc["clutter"] = "desk", "plain", 0
    res = run_scenario(sc, out_dir=str(tmp_path), posture="oracle", progress=False)
    assert res["duration_s"] >= 80
    assert res["scene"]["backend"] == "numpy" and res["scene"]["sensors"] == 2
    assert res["fsm"]["states_seq"][0] == "START"            # 착석 엣지로 세션이 시작됐다(ToF 재실)
    assert res["posture"]["per_label"]["upright"]["n_sec"] > 0
