"""잠재 피로 모델(tools/life_sim/fatigue.py) — 피로가 원인에서 생기고 행동·정답 라벨로 이어지는지 고정한다."""
from __future__ import annotations

import datetime as dt

import pytest

from tools.life_sim.fatigue import FatigueModel

T0 = dt.datetime(2026, 10, 20, 10, 0).timestamp()        # 오전 10시(점심 뒤·밤 아님)
FIXED = {"enabled": True, "f0": 0.2, "task_half_h": 1.5, "rest_half_min": 15, "co2": {"from_ppm": 800, "per_1000ppm": 0.6},
         "circadian": {"dip_hours": [13, 16], "dip_gain": 0.5, "night_from_h": 23.0, "night_gain": 1.0},
         "alert": {"wake_boost": 0.5, "self_wake_boost": 0.2, "tau_min": 8, "startle_drop": 0.05},
         "drowsy": {"s50": 0.65, "width": 0.05, "max_rate_per_min": 0.5, "typing_factor": 0.5, "episode_mean_min": [3, 8]}}


def _run(m, seconds, *, kind="upright", co2=700.0, t0=T0):
    for i in range(seconds):
        m.step(t0 + i, kind=kind, co2=co2, lux=400.0, felt_c=24.0)


def test_disabled_is_a_no_op():
    m = FatigueModel({}, seed=1, t0=T0)
    f0 = m.F
    _run(m, 3600)
    assert m.F == f0 and m.decide(T0, kind="upright", typing=True) == []
    assert m.keys((0.1, 0.2, 0.03)) == (0.1, 0.2, 0.03)


def test_work_raises_rest_lowers_and_co2_speeds_up():
    lo, hi = FatigueModel(FIXED, seed=1, t0=T0), FatigueModel(FIXED, seed=1, t0=T0)
    _run(lo, 3600, co2=700.0)
    _run(hi, 3600, co2=1500.0)
    assert 0.2 < lo.F < hi.F < 1.0
    before = hi.F
    _run(hi, 900, kind="away")
    assert hi.F < before * 0.6                      # 15 분 휴식이면 절반 아래로
    dozing = FatigueModel(FIXED, seed=1, t0=T0)
    _run(dozing, 1800, kind="drowsy")
    assert dozing.F == pytest.approx(0.2)           # 졸고 있는 동안은 회복도 누적도 없다


def test_night_and_post_lunch_are_faster():
    day = FatigueModel(FIXED, seed=1, t0=T0)
    night_t0 = dt.datetime(2026, 10, 20, 23, 30).timestamp()
    night = FatigueModel(FIXED, seed=1, t0=night_t0)
    _run(day, 1800)
    _run(night, 1800, t0=night_t0)
    assert night.F - 0.35 > day.F - 0.2             # 밤은 시작 +0.15 이고 오르는 속도도 빠르다


def test_high_sleepiness_starts_drowsy_episode_with_nods_and_wake_lowers_s():
    m = FatigueModel(dict(FIXED, f0=0.95), seed=3, t0=T0)
    ov = []
    for i in range(1200):
        ov = m.decide(T0 + i, kind="upright", typing=False)
        if ov:
            break
    assert ov and ov[0]["kind"] in ("drowsy", "face_down") and ov[0]["name"].startswith("fatigue_")
    assert 60.0 <= ov[0]["seconds"] <= 1200.0
    if ov[0]["kind"] == "drowsy":
        assert ov[0]["spec"]["nods"]["amp_deg"][0] >= 12.0
    s_before = m.S
    m.on_wake(T0 + 1300, source="posture_auto")
    assert m.S < s_before * 0.6 and m.n_wakes == 1
    m.decide(T0 + 1301, kind="upright", typing=True)   # 구간이 바뀐 걸 보고 에피소드를 닫는다(개입으로 깸 → 스스로 깸 아님)
    assert m.n_self_wakes == 0 and not m.in_episode


def test_episode_end_without_intervention_counts_as_self_wake():
    m = FatigueModel(dict(FIXED, f0=0.95), seed=3, t0=T0)
    for i in range(1200):
        if m.decide(T0 + i, kind="upright", typing=False):
            break
    a = m.A
    m.decide(T0 + 2000, kind="upright", typing=True)
    assert m.n_self_wakes == 1 and m.A > a


def test_slumps_need_moderate_sleepiness_and_keys_degrade():
    calm = FatigueModel(dict(FIXED, f0=0.1), seed=2, t0=T0)
    assert all(not calm.decide(T0 + i, kind="upright", typing=True) for i in range(3000))
    tired = FatigueModel(dict(FIXED, f0=0.55, drowsy={"s50": 0.99, "width": 0.01, "max_rate_per_min": 0.0}), seed=2, t0=T0)
    got = [o for i in range(6000) for o in tired.decide(T0 + i, kind="upright", typing=True)]
    assert got and all(o["name"] == "fatigue_slump" for o in got)
    k0, k1 = calm.keys((0.15, 0.30, 0.04)), tired.keys((0.15, 0.30, 0.04))
    assert all(b > a for a, b in zip(k0, k1))


def test_truth_classes_and_seed_reproducible():
    m = FatigueModel(dict(FIXED, f0=0.3), seed=4, t0=T0)
    assert m.truth(kind="upright", absent_s=None) == "focus"
    assert m.truth(kind="drowsy", absent_s=None) == "fatigue"
    assert m.truth(kind="away", absent_s=120.0) == "rest"
    assert m.truth(kind="away", absent_s=900.0) == "idle"
    m.F = 0.7
    assert m.truth(kind="upright", absent_s=None) == "fatigue"
    cfg = {"enabled": True}
    assert FatigueModel(cfg, seed=9, t0=T0).params() == FatigueModel(cfg, seed=9, t0=T0).params()
    assert FatigueModel(cfg, seed=9, t0=T0).params() != FatigueModel(cfg, seed=10, t0=T0).params()


def test_daygen_days_are_free_living_and_reproducible(tmp_path):
    from tools.life_sim.daygen import make_day, write_days
    a, b = make_day(11), make_day(11)
    assert a == b and make_day(12) != a
    kinds = {g["kind"] for g in a["segments"]}
    assert not kinds & {"drowsy", "face_down", "turtle", "chin_rest"}       # 졸음·자세 무너짐은 피로 모델이 만든다
    assert all("co2_to" not in (g.get("env") or {}) for g in a["segments"])
    assert a["reaction"]["fatigue"]["enabled"] and a["devices"]["room"]["randomize"]
    desk = sum(g["seconds"] for g in a["segments"] if g["kind"] != "away")
    assert 2.0 * 3600 - 60 <= desk <= 3.5 * 3600 + 60
    index = write_days(list(range(1, 41)), str(tmp_path))
    assert len({d["date"] for d in index}) == 40 and len({d["season"] for d in index}) == 4
    from tools.life_sim.scenario import load_scenario
    sc = load_scenario(index[0]["path"])
    assert sc["reaction_resolved"]["fatigue"]["enabled"] is True


def test_harness_emits_truth_rows_and_fatigue_summary(tmp_path):
    from tools.life_sim.harness import run_scenario
    from tools.life_sim.scenario import SCENARIO_DEFAULTS, deep_merge, load_defaults, resolve_reaction, start_epoch
    from tools.posture_sim.synthetic import SyntheticPerson

    reaction_cfg, devices_cfg, policy_cfg = load_defaults()
    segs = [{"kind": "away", "seconds": 6}, {"kind": "upright", "seconds": 1500, "typing": True},
            {"kind": "away", "seconds": 30, "no_return": True}]
    sc = deep_merge(SCENARIO_DEFAULTS, {"name": "fat", "seed": 7, "segments": segs, "env": {"co2_ppm": 1200}})
    sc["reaction_resolved"] = resolve_reaction(reaction_cfg, "default", {"fatigue": dict(FIXED, f0=0.9)})
    sc["devices_resolved"], sc["policy_resolved"] = devices_cfg, policy_cfg
    sc["t0"], sc["path"] = start_epoch("2026-10-22", "14:00"), "<test>"
    src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=10.0)
    res = run_scenario(sc, out_dir=str(tmp_path), source=src, progress=False)
    f = res["fatigue"]
    assert f is not None and f["onsets"] >= 1 and f["truth_min"]["fatigue"] > 0
    import json
    import os
    rows = [json.loads(line) for line in open(os.path.join(res["run_dir"], "frames.jsonl"), encoding="utf-8")]
    assert {"fatigue_F", "fatigue_S", "truth_class"} <= set(rows[100])
    # 2단계 학습 로그: hub tick 마다 frames·state·truth 가 한 줄씩(ml/training/build_dataset.py --logs --truth 가 읽는 꼴)
    lines = {n: open(os.path.join(res["run_dir"], f"{n}-hub.jsonl"), encoding="utf-8").read().splitlines()
             for n in ("frames", "state", "truth")}
    assert len(lines["frames"]) == len(lines["state"]) == len(lines["truth"]) > 100
    first = json.loads(lines["truth"][0])
    assert first["truth"] in ("focus", "fatigue", "rest", "idle") and abs(first["now"] - json.loads(lines["frames"][0])["now"]) < 1e-6
