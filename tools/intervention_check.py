"""개입 한 사이클을 읽기 전용으로 기록하고 응답·제어 결과·리포트를 대조한다.

python tools/intervention_check.py --broker <PI4_IP> --case accept --seconds 1200
python tools/intervention_check.py --events logs/intervention/<run>/events.jsonl --case accept

센서·피드백·제어를 발행하지 않는다. Pi 5 터치와 실제 기기 동작은 현장에서 확인한다.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))
from deskmate_hub.control import load_control_config

STATE = "deskmate/state/phase"
REQUEST = "deskmate/interaction/request"
FEEDBACK = "deskmate/feedback/user"
COMMAND = "deskmate/control/cmd"
RESULT = "deskmate/control/result"
REPORT = "deskmate/session/report"
TOPICS = (STATE, REQUEST, FEEDBACK, COMMAND, RESULT, REPORT)
CASES = ("accept", "reject", "timeout", "undo", "correct")


def data(body: dict) -> dict:
    return body["data"] if isinstance(body.get("data"), dict) else body


def check_cycle(events: list[dict], case: str, control_cfg: dict | None = None) -> dict:
    """새 START부터 관측한 한 세션만 판정한다. 과거 retained 스냅샷은 증거가 아니다."""
    if case not in CASES:
        raise ValueError(f"Unknown case: {case}")
    cfg = control_cfg if control_cfg is not None else load_control_config()
    checks: dict[str, bool] = {}
    fresh = [e for e in events if isinstance(e, dict) and not e.get("retain") and e.get("topic") in TOPICS
             and isinstance(e.get("payload"), dict)]
    start = next((i for i, e in enumerate(fresh)
                  if e["topic"] == STATE and data(e["payload"]).get("fsm_state") == "START"), None)
    checks["session_start_observed"] = start is not None
    run = fresh[start:] if start is not None else []
    states = [data(e["payload"]).get("fsm_state") for e in run if e["topic"] == STATE]
    checks["single_session"] = sum(state == "START" and (i == 0 or states[i - 1] != "START") for i, state in enumerate(states)) == 1
    boot_ids = {e["payload"].get("boot_id") for e in run if e["topic"] == STATE}
    checks["single_hub_run"] = bool(states) and len(boot_ids) == 1 and None not in boot_ids
    story = ("START", "FOCUS", "FATIGUE", "ACTION_ENV", "MONITOR", "RECOVERY")
    cursor = iter(states)
    checks["cycle_in_order"] = all(any(s and (s.startswith("FOCUS_") if want == "FOCUS" else s == want)
                                            for s in cursor) for want in story)
    requests = [(i, data(e["payload"])) for i, e in enumerate(run) if e["topic"] == REQUEST]
    requests = [(i, q) for i, q in requests if q.get("kind") == "env_suggest" and q.get("request_id")]
    checks["environment_suggestion_observed"] = bool(requests)
    response = "accept" if case in ("accept", "undo", "correct") else case
    matching = [(i, data(e["payload"])) for i, e in enumerate(run) if e["topic"] == FEEDBACK
                and data(e["payload"]).get("verdict") == response
                and any(qi < i and q["request_id"] == data(e["payload"]).get("request_id")
                        for qi, q in requests)]
    checks["matching_feedback_observed"] = len(matching) == 1
    command_events = [(i, data(e["payload"])) for i, e in enumerate(run) if e["topic"] == COMMAND]
    ids = [c.get("command_id") for _, c in command_events]
    checks["valid_command_ids"] = all(isinstance(cid, str) and bool(cid) for cid in ids)
    checks["consistent_command_retransmissions"] = all(
        all(other == command for _, other in command_events if other.get("command_id") == command.get("command_id"))
        for _, command in command_events)
    commands = {data(e["payload"]).get("command_id"): (i, data(e["payload"]))
                for i, e in enumerate(run) if e["topic"] == COMMAND}
    forward = [(i, c) for i, c in commands.values() if c.get("gate") != "undo"]
    undo = [(i, c) for i, c in commands.values() if c.get("gate") == "undo"]
    expected = {(a["target_id"], a["operation"], json.dumps(a.get("value"), sort_keys=True))
                for a in cfg.get("actions", {}).get("environment", [])}
    actual = {(c.get("target_id"), c.get("operation"), json.dumps(c.get("value"), sort_keys=True))
              for _, c in forward}
    if case in ("reject", "timeout"):
        checks["no_control_on_rejection_or_timeout"] = not commands
    else:
        checks["configured_commands_after_accept"] = (bool(expected) and actual == expected
            and len(forward) == len(expected) and len(matching) == 1
            and all(i > matching[0][0] and c.get("gate") == "suggest" for i, c in forward))
        results = [(i, data(e["payload"])) for i, e in enumerate(run) if e["topic"] == RESULT]
        def succeeded(ci, command):
            terminal = [(ri, result) for ri, result in results if ri > ci
                        and result.get("command_id") == command.get("command_id")
                        and result.get("status") in ("succeeded", "failed", "cancelled", "timeout")]
            return bool(terminal) and terminal[0][1].get("status") == "succeeded" and terminal[0][1].get("actual_value") == command.get("value")
        checks["all_commands_succeeded_with_actual_value"] = bool(commands) and all(
            c.get("command_id") and succeeded(ci, c) for ci, c in commands.values())
    if case == "undo":
        rejects = [i for i, e in enumerate(run) if e["topic"] == FEEDBACK
                   and data(e["payload"]).get("verdict") == "reject"
                   and data(e["payload"]).get("request_id") in (None, "", "atlas-display")]
        expected_undo = {(a["target_id"], a["operation"], json.dumps(a["undo_value"], sort_keys=True))
                         for a in cfg.get("actions", {}).get("environment", []) if a.get("undo_value") is not None}
        actual_undo = {(c.get("target_id"), c.get("operation"), json.dumps(c.get("value"), sort_keys=True))
                       for _, c in undo}
        checks["undo_after_execution"] = (bool(forward) and bool(rejects) and bool(expected_undo)
            and actual_undo == expected_undo and len(undo) == len(expected_undo)
            and all(any(max(i for i, _ in forward) < ri < ui for ri in rejects) for ui, _ in undo))
    else:
        checks["no_unexpected_undo"] = not undo
    if case == "correct":
        checks["correction_observed"] = any(e["topic"] == FEEDBACK
            and data(e["payload"]).get("verdict") == "correct"
            and data(e["payload"]).get("corrected_state") in ("FOCUS_PC", "FATIGUE", "REST", "IDLE")
            for e in run)
    recovery_i = next((i for i, e in enumerate(run) if e["topic"] == STATE
                       and data(e["payload"]).get("fsm_state") == "RECOVERY"), len(run))
    start_ts = run[0]["payload"].get("ts") if run else None
    reports = [data(e["payload"]) for i, e in enumerate(run) if e["topic"] == REPORT
               and i > recovery_i and start_ts is not None and data(e["payload"]).get("t_start") == start_ts]
    report = reports[-1] if reports else {}
    checks["current_session_report_after_recovery"] = bool(report)
    episodes = report.get("fatigue_episodes") or []
    checks["report_contains_resolved_fatigue"] = any(e.get("t_resolved") is not None for e in episodes)
    checks["report_contains_focus_and_interventions"] = (report.get("focus_time_s", 0) > 0
        and (report.get("intervention_counts") or {}).get("total", 0) > 0)
    control_results = report.get("control_results") or {}
    checks["control_results_reflected_in_report"] = all(
        (control_results.get(kind) or {}).get("total") == len(group)
        and (control_results.get(kind) or {}).get("succeeded") == len(group)
        and all((control_results.get(kind) or {}).get(status) == 0
                for status in ("executing", "failed", "timeout", "cancelled"))
        for kind, group in (("forward", forward), ("undo", undo)))
    metrics = report.get("metrics") or {}
    checks["feedback_reflected_in_report"] = (metrics.get("suggest_accept_rate") ==
        (0.0 if case in ("reject", "timeout") else 1.0)
        and metrics.get("timeout_rate") == (1.0 if case == "timeout" else 0.0)
        and (case != "correct" or metrics.get("correction_count", 0) >= 1))
    return {"case": case, "passed": all(checks.values()), "checks": checks,
            "missing_or_failed": [k for k, ok in checks.items() if not ok],
            "states": states, "commands": len(commands), "report_metrics": metrics,
            "scope": "MQTT evidence; touch rendering and physical actuation require field observation"}


def capture(host: str, port: int, seconds: float, output: Path) -> list[dict]:
    """구독만 한다. 수집 대상을 개입 토픽으로 제한해 raw 센서를 기록하지 않는다."""
    import paho.mqtt.client as mqtt
    ready, disconnected = threading.Event(), threading.Event()
    events: list[dict] = []
    errors: list[str] = []
    with output.open("w", encoding="utf-8") as log:
        def on_connect(client, _u, _f, reason, _p):
            if reason != 0:
                errors.append(f"MQTT connection rejected: {reason}")
                ready.set()
                return
            client.subscribe([(topic, 1) for topic in TOPICS])

        def on_subscribe(_c, _u, _mid, reasons, _p):
            if any(r.is_failure for r in reasons):
                errors.append("MQTT subscription rejected")
            ready.set()

        def on_message(_c, _u, msg):
            try:
                body = json.loads(msg.payload)
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(body, dict):
                return
            record = {"received_ts": time.time(), "topic": msg.topic,
                      "retain": bool(msg.retain), "payload": body}
            events.append(record)
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.flush()

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"intervention-check-{time.time_ns()}")
        client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
        client.on_disconnect = lambda *_: disconnected.set()
        try:
            client.connect(host, port, keepalive=30)
            client.loop_start()
            if not ready.wait(10) or errors:
                raise RuntimeError("; ".join(errors) or "MQTT subscription timed out")
            print("Recording intervention only. Start a new session and respond on Pi 5; Ctrl+C ends capture.", flush=True)
            disconnected.wait(seconds)
        except KeyboardInterrupt:
            pass
        finally:
            client.disconnect()
            client.loop_stop()
    if disconnected.is_set() and len(events) == 0:
        raise RuntimeError("Disconnected before any intervention evidence")
    return events


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--broker")
    source.add_argument("--events", type=Path)
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--case", choices=CASES, required=True)
    ap.add_argument("--seconds", type=float, default=1200)
    ap.add_argument("--control-config", help="현장에서 사용하는 control.yaml")
    args = ap.parse_args(argv)
    if args.seconds <= 0:
        ap.error("--seconds must be positive")
    out = ROOT / "logs" / "intervention" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + args.case)
    out.mkdir(parents=True)
    try:
        if args.events:
            events = [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines() if line.strip()]
        else:
            events = capture(args.broker, args.port, args.seconds, out / "events.jsonl")
        result = check_cycle(events, args.case, load_control_config(args.control_config))
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"case": args.case, "passed": False, "error": str(exc)}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Evidence: {out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
