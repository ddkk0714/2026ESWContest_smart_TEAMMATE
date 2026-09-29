"""control: ACTION_ENV → 명령 발행 → 결과/타임아웃 → 완료, 제안 대기, 거절 되돌리기, 쿨다운, 비가역 금지."""
from __future__ import annotations

import io

import pytest

from deskmate_hub.control import ControlDispatcher, MockPlugAdapter, load_control_config
from deskmate_hub.inference import GateMode, State
from deskmate_hub.ingest import Sample, SensorCache
from deskmate_hub.live import LiveHub

CFG = {"enabled": True, "adapter": "mock", "result_timeout_sec": 15, "cooldown_sec": 300,
       "irreversible_operations": ["set_power_off"],
       "actions": {"environment": [
           {"target_id": "vent_fan", "operation": "set_power", "value": "on", "undo_value": "off"},
           {"target_id": "desk_lamp", "operation": "set_brightness", "value": 70, "undo_value": 40},
       ]}}


def make(clock_start=1000.0):
    sent = []
    t = {"now": clock_start}
    d = ControlDispatcher(CFG, publish_cmd=sent.append, clock=lambda: t["now"])
    return d, sent, t


def test_auto_gate_dispatches_and_completes_on_results():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    assert len(sent) == 2 and {c["target_id"] for c in sent} == {"vent_fan", "desk_lamp"}
    assert sent[0]["gate"] == "auto" and sent[0]["requires_confirmation"] is False
    assert d.action_done() is False                     # 결과 대기 중
    assert d.on_result({"command_id": sent[0]["command_id"], "status": "succeeded"})
    assert d.action_done() is False
    assert d.on_result({"command_id": sent[1]["command_id"], "status": "failed", "error_code": "offline"})
    assert d.action_done() is True and ep.outcome == "executed"
    assert d.on_result({"command_id": "unknown", "status": "succeeded"}) is False


def test_timeout_completes_episode():
    d, sent, t = make()
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    t["now"] += 16
    assert d.action_done() is True
    assert all(c.status == "timeout" for c in d.episode.commands)


def test_suggest_expires_without_answer():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST)
    t["now"] += 181
    assert d.action_done() is True and ep.outcome == "expired" and sent == []


def test_suggest_waits_for_user_then_dispatches_or_skips():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST)
    assert sent == [] and ep.awaiting_user and d.action_done() is False
    d.on_feedback("accept")
    assert len(sent) == 2 and ep.outcome == "executed"
    d.close_episode()
    t["now"] += 1000                                    # 쿨다운 해제
    ep2 = d.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST)
    d.on_feedback("reject")
    assert len(sent) == 2 and ep2.outcome == "rejected" and d.action_done() is True


def test_reject_after_auto_execution_sends_undo():
    d, sent, t = make()
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    for c in list(sent):
        d.on_result({"command_id": c["command_id"], "status": "succeeded"})
    d.on_feedback("reject")
    undo = sent[2:]
    assert {(c["target_id"], c["value"]) for c in undo} == {("vent_fan", "off"), ("desk_lamp", 40)}
    assert all(c["gate"] == "undo" for c in undo) and d.episode.outcome == "undone"


def test_cooldown_and_none_gate_skip():
    d, sent, t = make()
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    d.close_episode()
    t["now"] += 10                                      # 쿨다운(300 s) 안
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    assert len(sent) == 2 and ep.outcome == "skipped" and d.action_done() is True
    d.close_episode()
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.NONE)
    assert ep.outcome == "skipped" and d.action_done() is True
    ep = d.on_enter_action("ACTION_ENV", "posture", GateMode.AUTO)   # 정의 없는 원인
    assert ep.outcome == "skipped"


def test_irreversible_operation_never_auto_executes():
    cfg = dict(CFG, actions={"environment": [{"target_id": "plug_1", "operation": "set_power_off", "value": "off"}]})
    sent = []
    d = ControlDispatcher(cfg, publish_cmd=sent.append, clock=lambda: 0.0)
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    assert sent == [] and ep.outcome == "skipped"
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST)
    assert ep.awaiting_user and ep.commands[0].requires_confirmation is True
    d.on_feedback("accept")
    assert len(sent) == 1 and sent[0]["requires_confirmation"] is True


def test_mock_plug_replies_success():
    results = []
    plug = MockPlugAdapter(results.append)
    plug.handle_command({"command_id": "c1", "target_id": "vent_fan", "operation": "set_power", "value": "on"})
    assert results[0]["status"] == "succeeded" and plug.state["vent_fan"] == {"set_power": "on"}


def test_default_config_is_reversible_only():
    cfg = load_control_config()
    ops = {a["operation"] for acts in cfg["actions"].values() for a in acts}
    assert not ops & set(cfg["irreversible_operations"])
    assert all("undo_value" in a for acts in cfg["actions"].values() for a in acts)


# ── LiveHub 연동: ACTION_ENV 진입 → mock 플러그 → 결과 → MONITOR ─────────

def mm(now, level):
    return Sample(received=now, ts=now, seq=None, data={
        "present": True, "motion_state": "still" if level <= 10 else "active", "motion_level": level,
        "distance_cm": 55, "resp_bpm": 15, "resp_valid": True, "heart_bpm": None, "heart_valid": False,
        "drowsy_state": "AWAKE", "valid": True})


def env(now, co2):
    return Sample(received=now, ts=now, seq=None, data={
        "co2_ppm": co2, "temp_c": 25.0, "humidity_pct": 45.0, "lux": 300,
        "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True})


def test_live_hub_routes_action_env_through_control():
    import copy

    from deskmate_hub.inference import load_config
    from deskmate_hub.ingest import load_ingest_config

    icfg = dict(load_ingest_config(), normalization="linear")
    # 시험용 가중치: 비PC 컨텍스트에서 환경 항이 피로를 지배하게 해 CAUSE_ANALYSIS 가 ACTION_ENV 로 라우팅되게 한다
    fcfg = copy.deepcopy(load_config())
    fcfg["weights"]["fatigue"]["npc"] = {"keystroke": 0.0, "posture": 0.15, "respiration": 0.0, "environment": 0.70, "elapsed": 0.15}
    cache = SensorCache()
    states = []
    hub = LiveHub(cache, fsm_cfg=fcfg, ingest_cfg=icfg, control_cfg=CFG, publish=states.append, out=io.StringIO())
    assert hub.control is not None and hub.mock_plug is not None
    period = hub.period
    baseline = float(hub.fsm_cfg["timers"]["baseline_sec"])
    t = 0.0
    # 착석·활동으로 세션 시작 → FOCUS
    while t <= baseline + 2 * period:
        cache.put("mmwave", mm(t, 40)); hub.tick_once(t); t += period
    # 환경 악화 + 정적 → 피로 → CAUSE_ANALYSIS → ACTION_ENV(원인 environment 이려면 CO₂ 항이 지배)
    seen = set()
    for _ in range(int(1500 / period)):
        cache.put("mmwave", mm(t, 40)); cache.put("env", env(t, 2000)); hub.tick_once(t); t += period
        seen.add(hub.engine.state)
        if hub.engine.state is State.ACTION_ENV and hub.control.episode and hub.control.episode.awaiting_user:
            cache.put_feedback({"verdict": "accept"})          # 제안 게이트면 화면에서 수락한 것으로
        if hub.engine.state is State.MONITOR:
            break
    assert State.ACTION_ENV in seen and State.MONITOR in seen
    # mock 플러그가 명령을 받아 상태표를 갱신했고, 에피소드는 닫혔다
    assert hub.mock_plug.state.get("vent_fan") == {"set_power": "on"}
    assert hub.control.episode is None and hub.control.history[-1].outcome == "executed"
    ctrl = [s["data"]["sensor_summary"].get("control") for s in states if s["data"]["fsm_state"] == "ACTION_ENV"]
    assert ctrl and ctrl[0]["active"] is True
