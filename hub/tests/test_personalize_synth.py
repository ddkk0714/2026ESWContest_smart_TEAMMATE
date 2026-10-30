"""합성 생성기 — 스키마 필드, 시뮬레이션 표기, 재현성, 전역 기본 규칙 vs 밴딧 수락률 비교."""
from __future__ import annotations

import numpy as np

from deskmate_hub.personalize import FEATURE_NAMES, InterventionPolicy, Situation, load_personalize_config, vectorize
from deskmate_hub.personalize.features import POSTURE_LABELS
from deskmate_hub.personalize.synth import PERSONAS, SynthGenerator, arm_counts

# 보상 무게: Pi 4 배포판 personalize.yaml 은 효과 0.8·수락 0.2(시뮬 v13). 합성 페르소나(synth.py)는 수락 확률로 사람을 만들어서
# 이 시험의 학습 수치는 이전 기본 0.5·0.5 기준이다 — 밴딧 메커니즘을 보려고 그 값으로 고정한다.
CFG = load_personalize_config()
# 이 파일은 밴딧 수식·날씨·페르소나를 환경 arm(선풍기·환기)으로도 시험한다 — 10-25 기본값(policy.causes 에 environment 없음)은
# test_policy_causes.py 가 따로 본다. 여기서는 모든 원인을 정책에 맡긴다.
CFG["policy"] = {"causes": ["environment", "posture", "cognitive"]}
REQUIRED = {"schema_name", "schema_version", "simulated", "persona", "episode_id", "session_id", "profile_id",
            "ts_ms", "local_time", "features", "fsm", "decision", "feedback", "outcome"}
FEATURE_KEYS = {"posture_dist", "posture_label", "posture_hold_min", "slump_hold_min", "present", "drowsy",
                "drowsy_count_30min", "co2_ppm", "lux", "temp_c", "humidity_pct", "pc_ratio", "typing_active",
                "c_fatigue", "c_focus", "session_elapsed_min", "since_last_intervention_min", "hour_local",
                "weekday", "after_bedtime", "month", "season", "weather", "missing"}


def test_schema_fields_and_simulated_flag():
    records = SynthGenerator(PERSONAS["fresh_air"], seed=1, days=30, cfg=CFG).generate()
    episodes = [r for r in records if r["schema_name"] == "personalize_episode"]
    sessions = [r for r in records if r["schema_name"] == "personalize_session"]
    assert len(episodes) >= 60 and 30 <= len(sessions) <= 60
    for r in episodes[:20]:
        assert REQUIRED <= set(r) and r["simulated"] is True and r["persona"] == "fresh_air"
        assert set(r["features"]) == FEATURE_KEYS
        assert set(r["features"]["posture_dist"]) == set(POSTURE_LABELS)
        assert r["fsm"]["cause"] in ("environment", "posture", "cognitive")
        assert r["decision"]["delivered_gate"] in ("auto", "suggest")
        assert r["feedback"]["verdict"] in ("accept", "reject", "timeout", "undo", "auto_kept")
        assert r["outcome"]["status"] in ("measured", "session_ended")
        assert (r["feedback"]["verdict"] in ("undo", "auto_kept")) == (r["decision"]["delivered_gate"] == "auto")
    for s in sessions[:5]:
        assert s["simulated"] is True and s["length_min"] > 0 and "fatigue_by_hour" in s


def test_generation_is_reproducible_by_seed():
    a = SynthGenerator(PERSONAS["night_owl"], seed=5, days=5, cfg=CFG).generate()
    b = SynthGenerator(PERSONAS["night_owl"], seed=5, days=5, cfg=CFG).generate()
    c = SynthGenerator(PERSONAS["night_owl"], seed=6, days=5, cfg=CFG).generate()
    assert a == b and a != c


def test_feature_vector_matches_names():
    s = Situation(ts=0.0, hour_local=12.0, weekday=0, month=1, posture_dist={"upright": 0.5, "recline": 0.5},
                  co2_ppm=1200, lux=50, temp_c=30, humidity_pct=50, weather={"windy": True, "outdoor_temp_c": 35})
    x = vectorize(s, CFG["features"])
    named = dict(zip(FEATURE_NAMES, x))
    assert len(x) == len(FEATURE_NAMES) and named["bias"] == 1.0
    assert named["posture_upright"] == 0.5 and named["posture_recline"] == 0.5
    assert named["co2"] == (1200 - 400) / 1600 and named["dark"] == 1.0 and named["temp_dev"] == 1.0
    assert named["windy"] == 1.0 and named["outdoor_temp_diff"] == 0.5 and named["weekend"] == 0.0


def test_bandit_learns_each_persona_over_30_days():
    """페르소나가 실제로 더 잘 받아들이는 개입(오라클 수락 확률 − 자동 되돌리기 확률)을 뒤 10일에 더 자주 고른다.
    시드 22(10-25 wake_light 추가로 난수열이 바뀜 — 시드 21~26 에서 개선폭 +0.05~+0.23, 21 의 오후 슬럼프만 +0.01)."""
    for pid, persona in PERSONAS.items():
        policy = InterventionPolicy(CFG, rng=np.random.default_rng(22))
        learned = SynthGenerator(persona, seed=22, days=30, cfg=CFG).generate(policy=policy)
        eps = [r for r in learned if r["schema_name"] == "personalize_episode" and r["decision"]["arm"]]
        dates = sorted({r["local_time"][:10] for r in eps})

        def oracle(rows):
            vals = []
            for r in rows:
                arm, gate = r["decision"]["arm"], r["decision"]["delivered_gate"]
                vals.append(persona.accept.get(arm, 0.3) - (persona.undo.get(arm, 0.1) if gate == "auto" else 0.0))
            return sum(vals) / max(1, len(vals))

        first = oracle([r for r in eps if r["local_time"][:10] in dates[:10]])
        last = oracle([r for r in eps if r["local_time"][:10] in dates[-10:]])
        assert last > first + 0.03, (pid, first, last)
        assert sum(arm_counts(learned).values()) >= 60
