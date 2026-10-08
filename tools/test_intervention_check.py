"""현장 판정기가 과거 리포트·누락된 실행 결과를 성공으로 오인하지 않는지 확인한다."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intervention_check import COMMAND, FEEDBACK, REPORT, REQUEST, RESULT, STATE, check_cycle


def event(topic, payload, *, retain=False):
    return {"topic": topic, "payload": payload, "retain": retain}


def fixture_events(case="accept"):
    events = []
    for seq, state in enumerate(["START", "FOCUS_PC", "FATIGUE", "ACTION_ENV"]):
        events.append(event(STATE, {"ts": 100 + seq * 10, "boot_id": "hub1", "seq": seq,
                                    "data": {"fsm_state": state}}))
    events.append(event(REQUEST, {"data": {"request_id": "r1", "kind": "env_suggest"}}))
    verdict = case if case in ("reject", "timeout") else "accept"
    events.append(event(FEEDBACK, {"request_id": "r1", "verdict": verdict}))
    if verdict == "accept":
        cmds = [{"command_id": "fan", "target_id": "vent_fan", "operation": "set_power", "value": "on"},
                {"command_id": "lamp", "target_id": "desk_lamp", "operation": "set_brightness", "value": 70}]
        for c in cmds:
            events.append(event(COMMAND, {"data": {**c, "gate": "suggest"}}))
            events.append(event(RESULT, {"data": {"command_id": c["command_id"], "status": "succeeded",
                                                  "actual_value": c["value"]}}))
        if case == "undo":
            events.append(event(FEEDBACK, {"request_id": "atlas-display", "verdict": "reject"}))
            for c, value in zip(cmds, ["off", 40]):
                events.append(event(COMMAND, {"data": {**c, "command_id": c["command_id"] + "undo",
                                                        "gate": "undo", "value": value}}))
                events.append(event(RESULT, {"data": {"command_id": c["command_id"] + "undo",
                                                      "status": "succeeded", "actual_value": value}}))
    if case == "correct":
        events.append(event(FEEDBACK, {"verdict": "correct", "corrected_state": "REST"}))
    events.extend(event(STATE, {"boot_id": "hub1", "ts": 150 + i * 10, "data": {"fsm_state": s}})
                  for i, s in enumerate(["MONITOR", "RECOVERY", "FOCUS_PC"]))
    events.append(event(REPORT, {"data": {"t_start": 100, "focus_time_s": 20,
        "fatigue_episodes": [{"t_onset": 120, "t_resolved": 170}], "intervention_counts": {"total": 1},
        "control_results": {kind: {"total": count, "succeeded": count,
            "failed": 0, "timeout": 0, "executing": 0, "cancelled": 0}
            for kind, count in (("forward", 2 if verdict == "accept" else 0), ("undo", 2 if case == "undo" else 0))},
        "metrics": {"suggest_accept_rate": 1.0 if verdict == "accept" else 0.0,
                    "timeout_rate": 1.0 if case == "timeout" else 0.0,
                    "correction_count": int(case == "correct")}}}))
    return events


@pytest.mark.parametrize("case", ["accept", "reject", "timeout", "undo", "correct"])
def test_checks_each_response(case):
    result = check_cycle(fixture_events(case), case)
    assert result["passed"], result


def test_retained_old_session_cannot_pass():
    result = check_cycle([{**e, "retain": True} for e in fixture_events()], "accept")
    assert not result["passed"]
    assert "session_start_observed" in result["missing_or_failed"]


@pytest.mark.parametrize("fault", ["missing_result", "failed_result", "wrong_value", "wrong_request",
                                    "old_report", "report_before_recovery", "hub_restart", "premature_command"])
def test_incomplete_or_mismatched_evidence_fails(fault):
    events = copy.deepcopy(fixture_events())
    if fault == "missing_result":
        events = [e for e in events if not (e["topic"] == RESULT and e["payload"]["data"]["command_id"] == "fan")]
    elif fault in ("failed_result", "wrong_value"):
        result = next(e["payload"]["data"] for e in events if e["topic"] == RESULT)
        result["status" if fault == "failed_result" else "actual_value"] = "failed" if fault == "failed_result" else "off"
    elif fault == "wrong_request":
        next(e for e in events if e["topic"] == FEEDBACK)["payload"]["request_id"] = "stale"
    elif fault == "old_report":
        events[-1]["payload"]["data"]["t_start"] = 10
    elif fault == "report_before_recovery":
        events.insert(0, events.pop())
    elif fault == "hub_restart":
        next(e for e in events if e["topic"] == STATE)["payload"]["boot_id"] = "other"
    elif fault == "premature_command":
        i = next(i for i, e in enumerate(events) if e["topic"] == COMMAND)
        events.insert(4, events.pop(i))
    result = check_cycle(events, "accept")
    assert not result["passed"], (fault, result)


def test_reject_must_not_execute_control():
    events = fixture_events("reject")
    events.insert(-1, event(COMMAND, {"command_id": "unexpected", "gate": "suggest"}))
    assert not check_cycle(events, "reject")["checks"]["no_control_on_rejection_or_timeout"]


def test_report_must_include_correction():
    events = fixture_events("correct")
    events[-1]["payload"]["data"]["metrics"]["correction_count"] = 0
    assert not check_cycle(events, "correct")["checks"]["feedback_reflected_in_report"]


@pytest.mark.parametrize("fault", ["second_session", "conflicting_command", "failed_before_success", "missing_report_results"])
def test_cross_session_and_contradictory_evidence_cannot_pass(fault):
    events = fixture_events()
    if fault == "second_session":
        events.insert(-1, event(STATE, {"boot_id": "hub1", "data": {"fsm_state": "START"}}))
    elif fault == "conflicting_command":
        command = copy.deepcopy(next(e for e in events if e["topic"] == COMMAND))
        command["payload"]["data"]["value"] = "off"
        events.insert(-1, command)
    elif fault == "failed_before_success":
        index = next(i for i, e in enumerate(events) if e["topic"] == RESULT)
        failed = copy.deepcopy(events[index])
        failed["payload"]["data"]["status"] = "failed"
        events.insert(index, failed)
    else:
        events[-1]["payload"]["data"].pop("control_results")
    assert not check_cycle(events, "accept")["passed"]
