"""5분 시연 시나리오가 기대 동선을 지키는지 — tools/demo_dryrun.py 로 실시간 없이 확인한다.

시나리오(tools/mqtt_scenario_sim.py demo_phases)나 fsm.demo.yaml·ingest.yaml·control.yaml 을 바꿨는데 여기가
깨지면 시연 동선이 바뀐 것이다. docs/demo-scenario.md 의 타임라인도 같이 고친다.
"""
from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from demo_dryrun import DEMO_CONFIG, run_dryrun  # noqa: E402  (hub 경로도 여기서 잡힌다)
from deskmate_hub.inference import load_config  # noqa: E402
from deskmate_hub.replay import iter_frames, replay  # noqa: E402

# 시연에서 보여 줄 순서. 사이에 다른 상태가 끼어도 되지만 이 순서로 모두 나와야 한다.
STORY = ["IDLE", "START", "FOCUS_PC", "FATIGUE_SUSPECT", "FATIGUE", "ACTION_POSTURE",
         "ESCALATE", "ACTION_ENV", "MONITOR", "RECOVERY"]


def _states(res):
    return [r["state"] for r in res["trace"]]


def _in_order(seq, story):
    it = iter(seq)
    return all(any(s == want for s in it) for want in story)


@pytest.mark.parametrize("offset", [0, 3, 7])
def test_demo_follows_the_story_within_five_and_a_half_minutes(offset):
    # 실시간 리허설에서는 hub tick 과 시나리오가 따로 시작하므로 어긋남을 바꿔 가며 본다.
    res = run_dryrun("demo", offset=offset)
    assert res["duration_s"] <= 330
    assert _in_order(_states(res), STORY), _states(res)


def test_posture_is_automatic_and_environment_retry_asks_first():
    # 첫 원인(자세)은 확신도 0.75 이상이라 자동, 재시도 원인(환경)은 gate.retry_max 로 제안 카드
    res = run_dryrun("demo")
    posture = next(r for r in res["trace"] if r["state"] == "ACTION_POSTURE")
    env = next(r for r in res["trace"] if r["state"] == "ACTION_ENV")
    assert posture["gate"] == "auto"
    assert env["gate"] == "suggest" and env["cause"] == "environment"
    # 화면이 이유를 말할 수 있게 환경 플래그가 실려 있다
    assert {"co2_high", "co2_rising"} <= set(env["env_flags"])
    assert [q["kind"] for q in res["requests"]] == ["env_suggest"]
    # 수락하면 팬·조명 명령
    targets = {(c["target_id"], c["gate"]) for c in res["commands"]}
    assert targets == {("vent_fan", "suggest"), ("desk_lamp", "suggest")}


@pytest.mark.parametrize("respond", ["reject", "timeout"])
def test_rejected_or_expired_card_sends_nothing_and_still_recovers(respond):
    res = run_dryrun("demo", respond=respond)
    assert res["commands"] == []
    assert "RECOVERY" in _states(res)


def test_session_recovers_and_report_counts_the_episode():
    res = run_dryrun("demo")
    states = _states(res)
    # 회복 뒤 몰입으로 돌아온다(마지막 이탈 직전)
    after = states[states.index("RECOVERY"):]
    assert any(s.startswith("FOCUS_") for s in after)
    report = res["report"]
    assert len(report["fatigue_episodes"]) == 1
    assert report["fatigue_episodes"][0]["t_resolved"] is not None
    assert report["intervention_counts"]["total"] >= 2


def test_frames_replay_to_the_same_transitions():
    buf = io.StringIO()
    res = run_dryrun("demo", frame_log=buf)
    buf.seek(0)
    replayed = replay(list(iter_frames(buf)), load_config(DEMO_CONFIG))
    assert [e["to"] for e in replayed.trace] == _states(res)[1:]


@pytest.mark.parametrize("seconds_after, undone", [(15, True), (70, False)])
def test_undo_on_the_auto_notice_follows_the_undo_window(seconds_after, undone):
    # 수락해 실행된 제어도 '되돌리기'(request_id 없는 reject)로 되돌린다. hub 가 제어 건을 닫은 뒤에도
    # undo_window_sec(60) 안이면 되돌린다.
    t_env = next(c["t"] for c in run_dryrun("demo")["commands"])
    res = run_dryrun("demo", undo_at=int(t_env) + seconds_after)
    undo = [(c["target_id"], c["value"]) for c in res["commands"] if c["gate"] == "undo"]
    assert undo == ([("vent_fan", "off"), ("desk_lamp", 40)] if undone else [])


def test_display_gone_suggestion_expires_and_session_moves_on():
    # The hub now honors the published card deadline even when the display is gone.
    from mqtt_scenario_sim import Phase, demo_phases

    phases = demo_phases()
    tail = phases[-2]                                    # wake_fresh 를 길게 늘려 만료 뒤까지 본다
    phases[-2] = Phase(**{**tail.__dict__, "seconds": 240.0})
    res = run_dryrun("demo", respond=None, phases=phases)
    trace = res["trace"]
    enter = next(r["t"] for r in trace if r["state"] == "ACTION_ENV")
    leave = next(r["t"] for r in trace if r["t"] > enter)
    timeout = next(q["expires_in_s"] for q in res["requests"] if q["kind"] == "env_suggest")
    assert enter + timeout <= leave <= enter + timeout + 10
    assert res["commands"] == []
    assert res["report"]["metrics"]["timeout_rate"] == 1.0
    assert res["report"]["control_results"]["forward"]["total"] == 0


def test_tick_latency_is_far_inside_the_500ms_budget():
    from measure_tick import measure, percentile

    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    res = measure(["demo"], [1])
    assert res["ticks"] > 20 and res["within_budget"]


# ── 개인화 시연: 되돌리기 한 번에 다음 개입은 자동 대신 묻는다 ───────────────────
def _personalize_run(tmp_path, undo_at=None):
    from deskmate_hub.personalize import load_personalize_config, read_records
    from demo_dryrun import personalize_demo_configs

    pcfg = dict(load_personalize_config(), enabled=True, record=True, profile_id="dryrun")
    pcfg["bandit"] = dict(pcfg["bandit"], algorithm="greedy")
    pcfg, ccfg = personalize_demo_configs(pcfg, load_config(DEMO_CONFIG))
    res = run_dryrun("undo_learn", personalize_cfg=pcfg, control_cfg=ccfg,
                     personalize_policy_path=str(tmp_path / "policy.json"),
                     personalize_log_path=str(tmp_path / "personalize.jsonl"),
                     undo_at_list=[undo_at] if undo_at is not None else None, respond=None)
    return res, read_records(str(tmp_path / "personalize.jsonl"))


def test_undo_learn_baseline_acts_automatically_twice(tmp_path):
    res, records = _personalize_run(tmp_path)
    lamp = [c for c in res["commands"] if c["target_id"] == "desk_lamp" and c["gate"] == "auto"]
    assert len(lamp) == 2 and all(c["value"] == 200 for c in lamp)
    breaks = [d for d in res["decisions"] if d["state"] == "ACTION_BREAK"]
    assert [(d["arm"], d["delivered_gate"]) for d in breaks] == [("light_cool_bright", "auto")] * 2
    assert res["duration_s"] <= 420
    # 에피소드 로그: 정책이 고른 arm·설명이 그대로 남는다
    eps = [r for r in records if r["schema_name"] == "personalize_episode"]
    assert eps and eps[0]["decision"]["arm"] == "light_cool_bright" and eps[0]["decision"]["source"] == "bandit"
    assert "어두움" in eps[0]["decision"]["explanation"]


def test_undo_learn_asks_instead_of_acting_after_one_undo(tmp_path):
    first_lamp = next(c["t"] for c in _personalize_run(tmp_path / "base")[0]["commands"] if c["target_id"] == "desk_lamp")
    res, records = _personalize_run(tmp_path / "undo", undo_at=int(first_lamp) + 5)
    undo = [c for c in res["commands"] if c["gate"] == "undo"]
    assert [(c["target_id"], c["value"]) for c in undo] == [("desk_lamp", 110)]   # arm 의 undo 값으로 되돌린다
    breaks = [d for d in res["decisions"] if d["state"] == "ACTION_BREAK"]
    assert len(breaks) == 2
    assert breaks[0]["delivered_gate"] == "auto"
    assert breaks[1]["fsm_gate"] == "auto" and breaks[1]["delivered_gate"] == "suggest"   # FSM 은 자동, 정책이 낮춘다
    assert "되돌리셔서 이번엔 여쭙니다" in breaks[1]["explanation"]
    # 두 번째는 자동 조명이 아니라 질문 카드이고, 카드에 설명 한 줄이 실린다
    assert len([c for c in res["commands"] if c["gate"] == "auto"]) == 1
    card = next(q for q in res["requests"] if q["kind"] == "break_suggest")
    assert card["explanation"] == breaks[1]["explanation"] and card["arm"] == breaks[1]["arm"]
    # 되돌리기는 에피소드 로그에 undo 로, 수용도 보상 −2
    ep = next(r for r in records if r["schema_name"] == "personalize_episode" and r["feedback"])
    assert ep["feedback"]["verdict"] == "undo" and ep["outcome"]["reward_accept"] == -2.0
