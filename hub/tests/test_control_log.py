"""제어 진행 로그(control/control_log.py, 2026-10-31) — 명령 하나마다 원인 → 기기·동작 → 결과·소요·되돌림.

디스패처 단계(제안 대기·쿨다운/비가역/게이트 none 생략·발행·결과·시간 초과·거절·만료·되돌림)와 디스패처 밖 명령
(rest_notice·arm_notice 의 speak/chime)이 같은 레코드 형식으로 남는지, JSONL·sink(CTLLOG)·최근 N건이 맞는지 본다.
"""
from __future__ import annotations

import io
import json

from deskmate_hub.control import ControlDispatcher, ControlLog, load_control_config
from deskmate_hub.inference import GateMode, State
from deskmate_hub.ingest import SensorCache
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import load_personalize_config

from test_ingest_mapping import env, mm
from test_posture_actions import CFG as CONTROL_CFG

LAMP_UP = {"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60}, "undo": "previous"}
FAN = {"target_id": "vent_fan", "operation": "set_power", "value": "on", "undo_value": "off"}


def make(tmp_path, *, cooldown=0, irreversible=(), actions=None, start=1000.0):
    sent, sunk, t = [], [], {"now": start}
    log = ControlLog({"path": str(tmp_path / "control.jsonl")}, boot_id="b", result_timeout_sec=15,
                     sink=sunk.append, clock=lambda: t["now"])
    cfg = {"result_timeout_sec": 15, "suggest_timeout_sec": 180, "undo_window_sec": 60, "cooldown_sec": cooldown,
           "irreversible_operations": list(irreversible),
           "actions": actions or {"posture": [LAMP_UP], "environment": [FAN]}}
    d = ControlDispatcher(cfg, publish_cmd=sent.append, on_record=log.record_command, clock=lambda: t["now"])
    return d, log, sent, sunk, t


def lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "control.jsonl").read_text(encoding="utf-8").splitlines()]


# ── 디스패처 ───────────────────────────────────────────────────────────────────

def test_auto_send_then_result_gives_one_record_with_latency(tmp_path):
    d, log, sent, sunk, t = make(tmp_path)
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO, arm="wake_light")
    cid = sent[0]["command_id"]
    t["now"] += 1.25
    d.on_result({"command_id": cid, "status": "succeeded", "previous": 100, "actual": 160, "actual_value": 160,
                 "node": "pi5"})
    [rec] = log.recent()
    assert rec["command_id"] == cid and rec["source"] == "dispatcher" and rec["event"] == "result"
    assert (rec["state"], rec["cause"], rec["gate"], rec["arm"]) == ("ACTION_POSTURE", "posture", "auto", "wake_light")
    assert (rec["target_id"], rec["operation"], rec["value"]) == ("desk_lamp", "adjust_brightness", {"percent": 60})
    assert rec["status"] == "succeeded" and rec["latency_ms"] == 1250
    assert (rec["previous"], rec["actual"], rec["node"]) == (100, 160, "pi5") and rec["undo"] is False
    # 파일·sink 에는 단계마다 한 줄(sent → result), 마지막 줄이 최종
    assert [r["event"] for r in lines(tmp_path)] == ["sent", "result"] == [r["event"] for r in sunk]
    assert lines(tmp_path)[-1] == rec and "arm" not in sent[0]                 # arm 은 MQTT payload 에 안 싣는다


def test_failed_result_keeps_error_code_and_mismatch_reason(tmp_path):
    d, log, sent, _, t = make(tmp_path, actions={"environment": [FAN]})
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    d.on_result({"command_id": sent[0]["command_id"], "status": "failed", "error_code": "ble_not_connected"})
    [rec] = log.recent()
    assert rec["status"] == "failed" and rec["error_code"] == "ble_not_connected" and "reason" not in rec
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    d.on_result({"command_id": sent[1]["command_id"], "status": "succeeded", "actual_value": "off"})
    assert log.recent()[-1]["status"] == "failed" and log.recent()[-1]["reason"] == "actual_value_mismatch"


def test_no_result_times_out_with_reason(tmp_path):
    d, log, sent, _, t = make(tmp_path)
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    t["now"] += 15
    d.tick()
    [rec] = log.recent()
    assert rec["status"] == "timeout" and rec["reason"] == "no_result" and rec["latency_ms"] == 15000


def test_cooldown_and_irreversible_skips_are_logged_not_sent(tmp_path):
    d, log, sent, _, t = make(tmp_path, cooldown=300, irreversible=["power_cycle"],
                              actions={"environment": [FAN, {"target_id": "plug", "operation": "power_cycle"}]})
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    assert len(sent) == 1
    t["now"] += 40
    d.close_episode()
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    assert len(sent) == 1
    reasons = [(r["target_id"], r["status"], r.get("reason")) for r in log.recent()]
    assert reasons == [("plug", "skipped", "irreversible_auto"), ("vent_fan", "executing", None),
                       ("vent_fan", "skipped", "cooldown 40s/300s"), ("plug", "skipped", "irreversible_auto")]


def test_suggest_waits_then_reject_or_accept(tmp_path):
    d, log, sent, _, t = make(tmp_path)
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.SUGGEST)
    assert sent == [] and log.recent()[0]["event"] == "awaiting_user" and log.recent()[0]["status"] == "accepted"
    d.on_feedback("reject")
    assert log.recent()[0]["status"] == "skipped" and log.recent()[0]["reason"] == "user_rejected"
    d.close_episode()
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.SUGGEST)
    t["now"] += 30
    d.on_feedback("accept")
    rec = log.recent()[-1]
    assert rec["event"] == "sent" and rec["status"] == "executing" and rec["sent_ts"] == t["now"]


def test_expired_suggestion_and_gate_none(tmp_path):
    d, log, sent, _, t = make(tmp_path)
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.SUGGEST)
    t["now"] += 180
    d.tick()
    assert log.recent()[0]["status"] == "skipped" and log.recent()[0]["reason"].startswith("expired")
    d.close_episode()
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.NONE)
    assert log.recent()[-1]["reason"] == "gate_none"


def test_undo_links_both_records(tmp_path):
    d, log, sent, _, t = make(tmp_path)
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    first = sent[0]["command_id"]
    d.on_result({"command_id": first, "status": "succeeded", "previous": 100, "actual": 160})
    d.close_episode()
    t["now"] += 20
    d.on_feedback("reject")
    undo_id = sent[1]["command_id"]
    by_id = {r["command_id"]: r for r in log.recent()}
    assert by_id[first]["undone_by"] == undo_id and by_id[first]["status"] == "succeeded"
    assert by_id[undo_id]["undo"] is True and by_id[undo_id]["undo_of"] == first
    assert (by_id[undo_id]["operation"], by_id[undo_id]["value"]) == ("set_brightness", 100)
    assert list(by_id) == [undo_id, first]           # 순서는 마지막 변경 순 — undone 표시로 원래 명령이 맨 뒤


def test_late_result_fills_previous_actual(tmp_path):
    d, log, sent, _, t = make(tmp_path)
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    cid = sent[0]["command_id"]
    d.on_result({"command_id": cid, "status": "succeeded", "actual_value": 160})
    d.on_result({"command_id": cid, "status": "succeeded", "previous": 100, "actual": 160})
    rec = log.recent()[0]
    assert rec["event"] == "late_result" and rec["previous"] == 100 and rec["status"] == "succeeded"


# ── 디스패처 밖 명령·저장 ─────────────────────────────────────────────────────────

def test_published_speak_result_and_timeout(tmp_path):
    t = {"now": 50.0}
    log = ControlLog({"path": str(tmp_path / "control.jsonl")}, boot_id="b", result_timeout_sec=15, clock=lambda: t["now"])
    speak = {"command_id": "b-3-speak", "target_id": "speaker", "operation": "speak", "value": {"text": "쉬어요"},
             "origin_state": "REST", "cause": "asleep", "gate": "notice"}
    log.record_published(speak, "rest_notice")
    log.record_published(dict(speak, command_id="b-4-chime", operation="chime"), "arm_notice", arm="sound_cue")
    t["now"] += 2
    log.on_result({"command_id": "b-3-speak", "status": "failed", "error_code": "tts_unavailable"})
    log.on_result({"command_id": "unknown", "status": "succeeded"})
    t["now"] += 20
    log.tick()
    speak_rec, chime_rec = log.recent()
    assert (speak_rec["source"], speak_rec["status"], speak_rec["error_code"], speak_rec["latency_ms"]) == \
        ("rest_notice", "failed", "tts_unavailable", 2000)
    assert (chime_rec["source"], chime_rec["arm"], chime_rec["status"], chime_rec["reason"]) == \
        ("arm_notice", "sound_cue", "timeout", "no_result")
    assert log.snapshot()["by_status"] == {"failed": 1, "timeout": 1}


def test_max_records_and_unwritable_path_do_not_break(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    msgs = []
    log = ControlLog({"path": str(blocker / "control.jsonl"), "max_records": 2}, boot_id="b", on_log=msgs.append)
    for i in range(3):
        log.record_published({"command_id": f"c{i}", "target_id": "speaker", "operation": "speak"}, "rest_notice")
    assert [r["command_id"] for r in log.recent()] == ["c1", "c2"]
    assert log.counts["write_errors"] == 1 and len(msgs) == 1                 # 한 번만 알리고 파일 기록만 끈다
    assert ControlLog({"enabled": False}, boot_id="b").record_published({"command_id": "x"}, "rest_notice") is None


def test_config_section_defaults():
    cfg = load_control_config()["control_log"]
    assert cfg["enabled"] is True and cfg["max_records"] == 200 and cfg["path"] is None


# ── LiveHub ────────────────────────────────────────────────────────────────────

def _hub(tmp_path):
    cache, sunk = SensorCache(), []
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub = LiveHub(cache, control_cfg=CONTROL_CFG, publish_control_log=sunk.append, out=io.StringIO(),
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"))
    return hub, cache, sunk


def test_live_dispatcher_and_rest_speak_reach_sink_and_file(tmp_path):
    hub, cache, sunk = _hub(tmp_path)
    assert hub.control_log is not None and hub.control_log.path.endswith(f"control-{hub.boot_id}.jsonl")
    hub.control.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO, 10.0)
    cache.put("mmwave", mm(11, level=40)); cache.put("env", env(11, co2=700))
    hub.tick_once(11.0)                                               # mock 어댑터 결과 → 디스패처 → 로그
    hub.rest_notifier.publish_control({"command_id": "x-speak", "target_id": "speaker", "operation": "speak",
                                       "value": {"text": "t"}, "origin_state": "REST", "cause": "accepted", "gate": "notice"})
    cache.put("mmwave", mm(12, level=40)); cache.put("env", env(12, co2=700))
    hub.tick_once(12.0)
    final = {r["command_id"]: r for r in sunk}
    env_rec = next(r for r in final.values() if r["source"] == "dispatcher")
    assert env_rec["status"] == "succeeded" and env_rec["state"] == "ACTION_ENV"
    assert final["x-speak"]["source"] == "rest_notice" and final["x-speak"]["status"] == "succeeded"
    on_disk = [json.loads(x) for x in open(hub.control_log.path, encoding="utf-8")]
    assert on_disk == sunk


def test_live_rest_entry_logs_speaker(tmp_path):
    hub, cache, sunk = _hub(tmp_path)
    t = 0.0
    while t <= float(hub.fsm_cfg["timers"]["baseline_sec"]) + 2 * hub.period:
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += hub.period
    hub.engine.state = State.ACTION_BREAK
    hub.engine._state_since = t
    hub.engine._last_cause = "cognitive"
    hub.pending_request = {"ts": t, "data": {"request_id": "q1", "kind": "break_suggest", "cause": "cognitive", "expires_in_s": 180}}
    cache.put_feedback({"verdict": "accept", "request_id": "q1"})
    cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700))
    hub.tick_once(t)
    cache.put("mmwave", mm(t + 1, level=40)); cache.put("env", env(t + 1, co2=700))
    hub.tick_once(t + 1)
    speaks = [r for r in hub.control_log.recent() if r["operation"] == "speak"]
    assert len(speaks) == 1 and speaks[0]["source"] == "rest_notice" and speaks[0]["status"] == "succeeded"
