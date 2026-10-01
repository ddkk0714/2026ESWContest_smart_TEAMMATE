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


def test_environment_action_is_automatic_and_drives_the_devices():
    res = run_dryrun("demo")
    env = next(r for r in res["trace"] if r["state"] == "ACTION_ENV")
    assert env["gate"] == "auto" and env["cause"] == "environment"
    # 화면이 이유를 말할 수 있게 환경 플래그가 실려 있다
    assert {"co2_high", "co2_rising"} <= set(env["env_flags"])
    targets = {(c["target_id"], c["gate"]) for c in res["commands"]}
    assert targets == {("vent_fan", "auto"), ("desk_lamp", "auto")}


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
    # 화면의 '되돌리기'는 request_id 없는 reject. hub 가 제어 건을 닫은 뒤에도 undo_window_sec(60) 안이면 되돌린다.
    t_env = next(c["t"] for c in run_dryrun("demo")["commands"])
    res = run_dryrun("demo", undo_at=int(t_env) + seconds_after)
    undo = [(c["target_id"], c["value"]) for c in res["commands"] if c["gate"] == "undo"]
    assert undo == ([("vent_fan", "off"), ("desk_lamp", 40)] if undone else [])
