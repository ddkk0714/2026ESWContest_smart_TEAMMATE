"""3층 개입 선택 정책 — 합성 30일로 페르소나별 선택이 갈리는지, 되돌리기 뒤 자동 문턱, 거절 재제안 금지, 게이트 상한."""
from __future__ import annotations

import copy

import numpy as np
import pytest

from deskmate_hub.inference import FSMEngine, GateMode, SensorFrame, Signal, State
from deskmate_hub.personalize import (InterventionPolicy, Situation, SituationExtras, fsm_record,
                                      is_decision_tick, load_personalize_config, situation_from_tick)
from deskmate_hub.personalize.synth import PERSONAS, SynthGenerator, arm_counts

CFG = load_personalize_config()


def sit(**kw) -> Situation:
    base = dict(ts=1_000_000.0, hour_local=15.5, weekday=2, month=10, posture_dist={"upright": 1.0},
                co2_ppm=800, lux=300, temp_c=24.0, humidity_pct=50, pc_ratio=0.9, typing_active=True,
                c_fatigue=0.80, c_focus=0.3, session_elapsed_min=90)
    base.update(kw)
    return Situation(**base)


def policy(seed=0, *, greedy=False, only=None, **cfg_over) -> InterventionPolicy:
    """greedy=True 면 Thompson 샘플 대신 사후 평균(결정적). only 로 후보 arm 을 제한한다."""
    cfg = copy.deepcopy(CFG)
    for k, v in cfg_over.items():
        cfg[k].update(v)
    if greedy:
        cfg["bandit"]["algorithm"] = "greedy"
    if only:
        cfg["arms"] = [a for a in cfg["arms"] if a["id"] in only or a.get("rule_only")]
    return InterventionPolicy(cfg, rng=np.random.default_rng(seed))


def train(persona_id: str, seed: int = 7):
    p = policy(seed)
    gen = SynthGenerator(PERSONAS[persona_id], seed=seed, days=30, cfg=CFG)
    records = gen.generate(policy=p)
    return p, records


def top(records, cause):
    counts = arm_counts(records, cause=cause, last_days=10)
    assert counts, f"{cause} 에피소드가 없다"
    return max(counts, key=counts.get)


# ── 페르소나별로 다른 개입을 고른다 ──────────────────────────
def test_personas_diverge_on_environment():
    _, owl = train("night_owl")
    _, fresh = train("fresh_air")
    assert top(owl, "environment") == "light_cool_bright"
    assert top(fresh, "environment") in ("air_fan_up", "ventilate_suggest")


def test_personas_diverge_on_cognitive():
    _, owl = train("night_owl")
    _, slumper = train("afternoon_slumper")
    assert top(owl, "cognitive") == "break_short"
    assert top(slumper, "cognitive") == "break_long"


def test_personas_diverge_on_posture():
    _, owl = train("night_owl")
    _, slumper = train("afternoon_slumper")
    assert top(slumper, "posture") == "posture_voice"
    assert top(owl, "posture") == "posture_card"      # 밤 무음 시간이라 음성은 후보에서 빠진다


def test_personal_mode_after_enough_feedback():
    p, records = train("fresh_air")
    assert p.policy_mode == "personal"
    assert p.n_personal >= CFG["cold_start"]["personal_min"]
    modes = [r["decision"]["policy_mode"] for r in records if r["schema_name"] == "personalize_episode"]
    assert modes[0] == "global" and modes[-1] == "personal"
    assert all(r["simulated"] for r in records)


def test_explanation_mentions_mode_and_count():
    p, _ = train("fresh_air")
    t = 1_800_000_000.0                                # 합성 30일(2026년) 뒤 시각 — 쿨다운과 겹치지 않게
    d = p.decide(sit(ts=t, co2_ppm=1300), cause="environment", gate="auto", session_id="demo", now=t)
    assert d.arm in ("air_fan_up", "ventilate_suggest", "light_cool_bright")
    assert "개인 정책" in d.explanation and "피드백" in d.explanation
    assert "CO₂ 1300 ppm" in d.explanation


# ── 되돌리기 뒤 자동 실행 문턱이 오른다 ───────────────────────
def test_undo_raises_auto_threshold_and_downgrades_to_suggest():
    p = policy(1, greedy=True, only={"air_fan_up"})
    before = p.auto_threshold("air_fan_up")
    assert before == pytest.approx(CFG["gate"]["conf_auto"])
    s = sit(co2_ppm=1500, c_fatigue=0.78)
    d1 = p.decide(s, cause="environment", gate="auto", session_id="s1", now=s.ts)
    assert d1.arm == "air_fan_up" and d1.delivered_gate == "auto"
    p.feedback(d1, "undo", now=s.ts + 20)
    assert p.auto_threshold("air_fan_up") == pytest.approx(before + CFG["gate"]["undo_penalty_step"])
    s2 = sit(ts=s.ts + 400, co2_ppm=1500, c_fatigue=0.78)
    d2 = p.decide(s2, cause="environment", gate="auto", session_id="s1", now=s2.ts)
    assert d2.arm == "air_fan_up"
    assert d2.delivered_gate == "suggest"            # 0.78 < 0.80
    assert "되돌리기 1회" in d2.explanation


def test_undo_penalty_capped_and_decays():
    p = policy(2, greedy=True, only={"air_fan_up"})
    s = sit(co2_ppm=1500, c_fatigue=0.95)
    for i in range(10):
        d = p.decide(sit(ts=s.ts + i * 400, co2_ppm=1500, c_fatigue=0.95), cause="environment", gate="auto",
                     session_id=f"s{i}", now=s.ts + i * 400)     # 세션당 개입 상한에 걸리지 않게 세션을 바꾼다
        assert d.arm == "air_fan_up"
        p.feedback(d, "undo")
    assert p.auto_threshold("air_fan_up") == pytest.approx(CFG["gate"]["conf_auto"] + CFG["gate"]["undo_penalty_max"])
    d = p.decide(sit(ts=s.ts + 5000, co2_ppm=1500, c_fatigue=0.95), cause="environment", gate="auto",
                 session_id="s2", now=s.ts + 5000)
    p.feedback(d, "auto_kept")
    assert p.auto_threshold("air_fan_up") < CFG["gate"]["conf_auto"] + CFG["gate"]["undo_penalty_max"]


# ── 거절 재제안 금지 · 쿨다운 · 게이트 상한 ───────────────────
def test_rejected_arm_not_proposed_again_in_session():
    p = policy(3, greedy=True)
    s = sit(co2_ppm=1500)
    d1 = p.decide(s, cause="environment", gate="suggest", session_id="s1", now=s.ts)
    assert d1.arm == "air_fan_up"
    p.feedback(d1, "reject")
    for i in range(1, 4):
        d = p.decide(sit(ts=s.ts + i * 400, co2_ppm=1500), cause="environment", gate="suggest",
                     session_id="s1", now=s.ts + i * 400)
        assert d.arm != "air_fan_up"
        assert d.excluded.get("air_fan_up") == "rejected_in_session"
    d = p.decide(sit(ts=s.ts + 9000, co2_ppm=1500), cause="environment", gate="suggest", session_id="s2", now=s.ts + 9000)
    assert "air_fan_up" not in d.excluded              # 새 세션이면 다시 후보


def test_cooldown_excludes_recently_executed_arm():
    p = policy(4, greedy=True)
    s = sit(co2_ppm=1500, c_fatigue=0.9)
    d1 = p.decide(s, cause="environment", gate="auto", session_id="s1", now=s.ts)
    assert d1.arm == "air_fan_up" and d1.delivered_gate == "auto"
    p.feedback(d1, "auto_kept")
    d2 = p.decide(sit(ts=s.ts + 100, co2_ppm=1500, c_fatigue=0.9), cause="environment", gate="auto",
                  session_id="s1", now=s.ts + 100)
    assert d2.excluded.get("air_fan_up") == "cooldown" and d2.arm != "air_fan_up"
    d3 = p.decide(sit(ts=s.ts + 400, co2_ppm=1500, c_fatigue=0.9), cause="environment", gate="auto",
                  session_id="s1", now=s.ts + 400)
    assert "air_fan_up" not in d3.excluded


def test_gate_is_a_ceiling_never_escalates():
    p = policy(5)
    none = p.decide(sit(), cause="environment", gate=GateMode.NONE, session_id="s1")
    assert none.arm is None and none.delivered_gate == "none" and none.source == "fallback"
    for _ in range(20):
        d = p.decide(sit(ts=1_000_000.0 + _ * 400), cause="cognitive", gate="suggest", session_id=f"s{_}")
        assert d.delivered_gate == "suggest"
    auto = p.decide(sit(session_elapsed_min=200, c_fatigue=0.9), cause="cognitive", gate="auto", session_id="x")
    assert auto.arm in ("break_short", "break_long", "light_cool_bright", "light_warm_dim", "sound_cue")
    if auto.arm in ("break_short", "break_long"):
        assert auto.delivered_gate == "suggest"        # 사용자 행동이 필요한 arm 은 자동 불가


def test_arms_stay_inside_fsm_cause_group():
    p = policy(6)
    for cause, allowed in (("environment", {"light_cool_bright", "air_fan_up", "ventilate_suggest"}),
                           ("posture", {"posture_card", "posture_voice", "sound_cue"}),
                           ("cognitive", {"light_cool_bright", "light_warm_dim", "sound_cue", "break_short", "break_long"})):
        for i in range(10):
            d = p.decide(sit(ts=1_000_000.0 + i * 400), cause=cause, gate="suggest", session_id=f"{cause}{i}")
            assert d.arm in allowed


def test_quiet_hours_exclude_sound_and_voice():
    p = policy(7)
    d = p.decide(sit(hour_local=23.5, slump_hold_min=8, posture_label="recline"), cause="posture", gate="suggest", session_id="n")
    assert d.excluded.get("posture_voice") == "quiet_hours" and d.excluded.get("sound_cue") == "quiet_hours"
    assert d.arm == "posture_card"


def test_session_cap_falls_back():
    p = policy(8, bandit={"max_per_session": 2})
    for i in range(2):
        d = p.decide(sit(ts=1_000_000.0 + i * 400), cause="cognitive", gate="suggest", session_id="cap")
        assert d.arm is not None
    d = p.decide(sit(ts=1_000_000.0 + 800), cause="cognitive", gate="suggest", session_id="cap")
    assert d.arm is None and d.source == "fallback"


# ── 취침 권유 규칙 ────────────────────────────────────────────
def test_sleep_rule_requires_all_conditions_and_once_per_session():
    p = policy(9)
    late = dict(hour_local=23.8, after_bedtime=True, drowsy=True, drowsy_count_30min=2, c_fatigue=0.82, session_elapsed_min=180)
    d0 = p.decide(sit(**late), cause="cognitive", gate="suggest", session_id="night")
    assert d0.arm != "sleep_suggest"                   # 앞 개입 무효 2회가 아직 없다
    p.feedback(d0, "reject")
    d1 = p.decide(sit(ts=1_000_400.0, **late), cause="cognitive", gate="suggest", session_id="night")
    assert d1.arm != "sleep_suggest"
    p.feedback(d1, "accept", now=1_000_405.0)
    p.outcome(d1, c_fatigue_after=0.85)                # 효과 없음 → 2회 연속 무효
    d2 = p.decide(sit(ts=1_000_800.0, **late), cause="cognitive", gate="auto", session_id="night")
    assert d2.arm == "sleep_suggest" and d2.source == "rule_sleep" and d2.delivered_gate == "suggest"
    assert "취침" in d2.explanation
    p.feedback(d2, "reject")
    d3 = p.decide(sit(ts=1_001_200.0, **late), cause="cognitive", gate="suggest", session_id="night")
    assert d3.arm != "sleep_suggest"                   # 세션에 한 번만
    d4 = p.decide(sit(ts=1_001_600.0, **{**late, "after_bedtime": False}), cause="cognitive", gate="suggest", session_id="day")
    assert d4.arm != "sleep_suggest"


# ── 저장·복원 · 어댑터 ──────────────────────────────────────
def test_policy_roundtrip_and_corrupt_file_falls_back(tmp_path):
    p, _ = train("afternoon_slumper", seed=3)
    path = tmp_path / "policy.json"
    p.save(str(path))
    q = InterventionPolicy.load(str(path), CFG, rng=np.random.default_rng(0))
    assert q.n_personal == p.n_personal and q.policy_mode == "personal"
    assert q.auto_threshold("air_fan_up") == pytest.approx(p.auto_threshold("air_fan_up"))
    np.testing.assert_allclose(q.models["break_long"]["accept"].mean(), p.models["break_long"]["accept"].mean())
    path.write_text("{broken", encoding="utf-8")
    fresh = InterventionPolicy.load(str(path), CFG)
    assert fresh.n_personal == 0 and fresh.policy_mode == "global"


def test_adapter_builds_situation_from_engine_tick():
    engine = FSMEngine()
    hot = {k: Signal(delta=0.9) for k in ("posture", "elapsed", "keystroke")}

    def frame(t, **flags):
        return SensorFrame(now=t, present=True, pc_ratio=0.9, signals=dict(hot), **flags)

    engine.tick(frame(0.0, touch=True))
    engine.tick(frame(301.0))
    engine.tick(frame(302.0))
    t = 302.0
    result = None
    while t < 2000:
        t += 10
        result = engine.tick(frame(t))
        if is_decision_tick(result):
            break
    assert result is not None and is_decision_tick(result)
    assert result.state in (State.ACTION_ENV, State.ACTION_POSTURE, State.ACTION_BREAK)
    extras = SituationExtras(posture_dist={"forward_head": 0.6, "upright": 0.4}, posture_label="forward_head",
                             posture_hold_min=7, co2_ppm=1200, lux=120, session_started_ts=0.0)
    s = situation_from_tick(frame(t), result, extras, CFG)
    assert s.c_fatigue == pytest.approx(result.scores.c_fatigue)
    assert s.session_elapsed_min == pytest.approx(t / 60.0)
    rec = fsm_record(result)
    assert rec["cause"] in ("environment", "posture", "cognitive") and rec["gate"] in ("auto", "suggest")
    p = policy(10)
    d = p.decide(s, cause=rec["cause"], gate=rec["gate"], session_id="live", now=t)
    assert d.arm is not None and d.delivered_gate in ("auto", "suggest")
    assert (d.delivered_gate == "auto") <= (rec["gate"] == "auto")
