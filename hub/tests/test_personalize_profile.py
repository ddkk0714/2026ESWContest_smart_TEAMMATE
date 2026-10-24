"""2층 프로필 — 합성 30일 로그를 일 단위로 접고 요약. JSONL 왕복. 로거 한 줄 스키마."""
from __future__ import annotations

import json

import numpy as np

from deskmate_hub.personalize import (DailyStats, InterventionPolicy, PersonalizeLogger, Situation, aggregate_daily,
                                      build_profile, load_personalize_config, read_records, summarize)
from deskmate_hub.personalize.profile import is_after_bedtime, read_daily, write_daily
from deskmate_hub.personalize.synth import PERSONAS, SynthGenerator

CFG = load_personalize_config()
# 이 파일은 밴딧 수식·날씨·페르소나를 환경 arm(선풍기·환기)으로도 시험한다 — 10-25 기본값(policy.causes 에 environment 없음)은
# test_policy_causes.py 가 따로 본다. 여기서는 모든 원인을 정책에 맡긴다.
CFG["policy"] = {"causes": ["environment", "posture", "cognitive"]}


def records_for(persona_id: str, seed: int = 11):
    return SynthGenerator(PERSONAS[persona_id], seed=seed, days=30, cfg=CFG).generate()


def test_daily_aggregation_covers_30_days_and_is_marked_simulated():
    records = records_for("night_owl")
    daily = aggregate_daily(records, CFG)
    assert 28 <= len(daily) <= 30
    first = sorted(daily.values(), key=lambda d: d.date)[0]
    assert first.simulated and first.sessions >= 1 and first.work_min > 0
    assert any(d.arm_stats for d in daily.values())


def test_profiles_differ_between_personas():
    owl = build_profile(records_for("night_owl"), CFG)
    slumper = build_profile(records_for("afternoon_slumper"), CFG)
    fresh = build_profile(records_for("fresh_air"), CFG)
    assert owl.days == 30 and owl.simulated
    # 올빼미: 늦은 밤 세션 비율 높고 취침 시각이 자정 넘김
    assert owl.late_night_ratio > 0.8 and slumper.late_night_ratio < 0.2
    assert owl.usual_bedtime is not None and owl.usual_bedtime < "06:00"      # 00:xx~01:xx
    # 오후 슬럼프: 첫 슬럼프가 빠르고 시간당 슬럼프가 많다
    assert slumper.first_slump_min < owl.first_slump_min
    assert slumper.slumps_per_hour > fresh.slumps_per_hour
    # 환기 민감: CO₂ 때문에 피로가 확정된 시점의 CO₂ 문턱이 낮다. 올빼미는 CO₂ 원인 피로가 없어 문턱 없음
    assert fresh.co2_sensitivity_ppm < slumper.co2_sensitivity_ppm
    assert owl.co2_sensitivity_ppm is None
    # 시간대별 피로 곡선은 각자 활동 시간대에만 있다
    assert owl.peak_fatigue_hour() in (0, 1, 2, 20, 21, 22, 23)
    assert 13 <= slumper.peak_fatigue_hour() <= 19


def test_arm_stats_reflect_persona_acceptance():
    fresh = build_profile(records_for("fresh_air"), CFG)
    air = fresh.arm_stats.get("fan_on")
    assert air and air["n"] >= 5
    owl = build_profile(records_for("night_owl"), CFG)
    assert owl.arm_stats["break_short"]["accept_rate"] > owl.arm_stats["break_long"]["accept_rate"]
    assert owl.arm_stats["fan_on"]["undo_rate"] is None or owl.arm_stats["fan_on"]["undo_rate"] > 0.4


def test_daily_jsonl_roundtrip_and_incremental_merge(tmp_path):
    records = records_for("afternoon_slumper")
    path = tmp_path / "daily.jsonl"
    first_half = [r for r in records if r["local_time"][:10] <= "2026-10-05"]
    build_profile(first_half, CFG, daily_path=str(path))
    n1 = len(read_daily(str(path)))
    summary = build_profile(records, CFG, daily_path=str(path))
    rows = read_daily(str(path))
    assert len(rows) > n1 and summary.days == len(rows)
    assert all(isinstance(d, DailyStats) for d in rows)
    line = path.read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(line)["schema_name"] == "personalize_daily"
    write_daily(str(path), rows)
    assert summarize(read_daily(str(path)), CFG).to_dict() == summary.to_dict()


def test_after_bedtime_handles_past_midnight():
    owl = build_profile(records_for("night_owl"), CFG)
    assert is_after_bedtime(2.5, owl, CFG)            # 새벽 2:30 은 전날 밤의 연장
    assert not is_after_bedtime(22.0, owl, CFG)
    assert is_after_bedtime(23.75, None, CFG)         # 프로필 없으면 기본 23:30
    assert not is_after_bedtime(12.0, None, CFG)


def test_logger_writes_one_line_per_episode_with_feedback_and_outcome(tmp_path):
    path = tmp_path / "personalize-test.jsonl"
    logger = PersonalizeLogger(str(path), boot_id="t1", cfg=CFG)
    policy = InterventionPolicy(CFG, rng=np.random.default_rng(0))
    s = Situation(ts=1_000_000.0, hour_local=15.0, weekday=1, month=10, posture_dist={"upright": 1.0},
                  co2_ppm=1400, lux=300, temp_c=24, humidity_pct=50, pc_ratio=0.9, c_fatigue=0.8, c_focus=0.3)
    d = policy.decide(s, cause="environment", gate="suggest", session_id="s1")
    eid = logger.open_episode(session_id="s1", situation=s, fsm={"state": "ACTION_ENV", "cause": "environment",
                              "gate": "suggest", "retry": False, "context": "pc", "guards": []}, decision=d, ts=s.ts)
    assert logger.due(s.ts + 300) == [] and logger.due(s.ts + 601) == [eid]
    r_acc = policy.feedback(d, "accept", now=s.ts + 4)
    logger.feedback(eid, "accept", ts=s.ts + 4, response_ms=4000, request_id="rq1")
    rec = logger.close(eid, status="measured", c_fatigue_after=0.55, reward_accept=r_acc, next_state="RECOVERY")
    rows = read_records(str(path))
    assert len(rows) == 1 and rows[0] == rec
    row = rows[0]
    assert row["schema_name"] == "personalize_episode" and row["simulated"] is False
    assert row["decision"]["arm"] == d.arm and row["feedback"]["verdict"] == "accept"
    assert row["outcome"]["reward_accept"] == 1 and row["outcome"]["reward_effect"] > 0.8
    b = CFG["bandit"]                                  # 보상 = 수락·효과 가중합(무게는 personalize.yaml, 보드 0.2·0.8)
    expect = float(b["weight_accept"]) * 1 + float(b["weight_effect"]) * row["outcome"]["reward_effect"]
    assert abs(row["outcome"]["reward"] - expect) < 1e-3 and row["outcome"]["reward"] > 0.8
    assert row["features"]["co2_ppm"] == 1400
    assert row["local_time"].endswith("+09:00")
    logger.open_episode(session_id="s1", situation=s, fsm={}, decision=d, ts=s.ts + 700)
    assert logger.flush() == 1 and read_records(str(path))[1]["outcome"]["status"] == "missing"
