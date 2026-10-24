"""밝기 명령은 절대값이 아니라 지금 밝기 대비 % (2026-10-25, 사용자 지시 "기존 대비 % 올리기").

계약(docs/data-spec.md §10): operation `adjust_brightness`, value {"percent": ±정수}, 앱이 지금 밝기로 계산해 결과에
`previous`(바꾸기 전 원시 0~255)·`actual`(바꾼 뒤)을 준다. 되돌리기(spec `undo: previous`)는 그 previous 로 `set_brightness`.
결과가 없거나 previous 가 없으면 되돌리기 생략. 기존 set_brightness·set_power 는 undo_value 그대로.
"""
from __future__ import annotations

import io

from deskmate_hub.control import ControlDispatcher, MockPlugAdapter, load_control_config
from deskmate_hub.inference import GateMode, State
from deskmate_hub.ingest import SensorCache
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import load_personalize_config
from deskmate_hub.personalize.adapter import arm_commands

from test_posture_actions import CFG, _drive_to_monitor

LAMP_UP = {"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60}, "undo": "previous"}
FAN = {"target_id": "vent_fan", "operation": "set_power", "value": "on", "undo_value": "off"}


def make(actions=None, start=1000.0):
    sent, t = [], {"now": start}
    cfg = {"result_timeout_sec": 15, "suggest_timeout_sec": 180, "undo_window_sec": 60, "cooldown_sec": 0,
           "irreversible_operations": [], "actions": actions or {"posture": [LAMP_UP], "environment": [FAN, LAMP_UP]}}
    return ControlDispatcher(cfg, publish_cmd=sent.append, clock=lambda: t["now"]), sent, t


# ── dispatcher ──────────────────────────────────────────────────────────────────

def test_adjust_is_published_as_percent_and_result_previous_is_kept():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    cmd = sent[0]
    assert cmd["operation"] == "adjust_brightness" and cmd["value"] == {"percent": 60}
    assert cmd["requires_confirmation"] is False and "undo" not in cmd        # 되돌리기 방식은 hub 내부 사정
    # 앱 결과: 원시 밝기 100 → 160. actual_value 가 value({percent}) 와 달라도 실패로 보지 않는다
    assert d.on_result({"command_id": cmd["command_id"], "status": "succeeded", "previous": 100, "actual": 160, "actual_value": 160})
    c = ep.commands[0]
    assert c.status == "succeeded" and c.result["previous"] == 100 and c.result["actual"] == 160
    assert d.action_done()


def test_undo_of_adjust_sends_set_brightness_previous():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    d.on_result({"command_id": sent[0]["command_id"], "status": "succeeded", "previous": 100, "actual": 160})
    d.close_episode()
    t["now"] += 20
    d.on_feedback("reject")
    assert ep.outcome == "undone" and len(sent) == 2
    undo = sent[1]
    assert (undo["operation"], undo["value"], undo["gate"], undo["target_id"]) == ("set_brightness", 100, "undo", "desk_lamp")
    # 되돌림 명령 자체는 다시 되돌리지 않는다
    d.on_result({"command_id": undo["command_id"], "status": "succeeded"})
    d.on_feedback("reject", t["now"] + 1)
    assert len(sent) == 2


def test_late_duplicate_result_fills_previous_without_changing_status():
    """PC life_sim 모의 어댑터: succeeded(actual_value={percent}) 먼저, 같은 command_id 로 previous·actual 이 한 번 더 온다.
    끝난 명령이라도 previous·actual 은 받아 두고(상태는 그대로) 되돌리기에 쓴다. 꺼져 있던 램프는 previous 0 → set_brightness 0."""
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    cid = sent[0]["command_id"]
    assert d.on_result({"command_id": cid, "status": "succeeded"}, t["now"] + 1)
    c = ep.commands[0]
    assert c.status == "succeeded" and not (c.result or {}).get("previous")
    assert d.on_result({"command_id": cid, "status": "succeeded", "previous": 0, "actual": 60}, t["now"] + 2) is False
    assert c.status == "succeeded" and c.completed_ts == t["now"] + 1 and c.result["previous"] == 0 and c.result["actual"] == 60
    assert d.on_result({"command_id": cid, "status": "failed"}, t["now"] + 3) is False and c.status == "succeeded"   # 상태는 안 바뀐다
    d.close_episode()
    d.on_feedback("reject", t["now"] + 10)
    assert ep.outcome == "undone" and sent[-1]["operation"] == "set_brightness" and sent[-1]["value"] == 0


def test_undo_is_skipped_without_result_or_previous():
    log = []
    d, sent, t = make()
    d._log = log.append
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    d.close_episode()                                             # 결과가 아직 없다(executing)
    t["now"] += 5
    d.on_feedback("reject")
    assert len(sent) == 1 and ep.outcome == "executed" and any("previous 가 없다" in m for m in log)
    d2, sent2, t2 = make()
    d2._log = log.append
    ep2 = d2.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    d2.on_result({"command_id": sent2[0]["command_id"], "status": "succeeded", "actual": 160})   # previous 없음
    d2.close_episode()
    d2.on_feedback("reject", t2["now"] + 5)
    assert len(sent2) == 1 and ep2.outcome == "executed"


def test_mixed_bundle_undoes_fan_by_undo_value_and_lamp_by_previous():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)
    assert [c["operation"] for c in sent] == ["set_power", "adjust_brightness"]
    d.on_result({"command_id": sent[0]["command_id"], "status": "succeeded"})
    d.on_result({"command_id": sent[1]["command_id"], "status": "succeeded", "previous": 70, "actual": 91})
    d.on_feedback("reject", t["now"] + 10)
    assert ep.outcome == "undone"
    undo = {(c["target_id"], c["operation"], c["value"]) for c in sent[2:]}
    assert undo == {("vent_fan", "set_power", "off"), ("desk_lamp", "set_brightness", 70)}


def test_absolute_set_brightness_still_undoes_with_undo_value():
    old = {"target_id": "desk_lamp", "operation": "set_brightness", "value": 200, "undo_value": 110}
    d, sent, t = make({"posture": [old]})
    d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    d.on_result({"command_id": sent[0]["command_id"], "status": "succeeded", "actual_value": 200})
    d.on_feedback("reject", t["now"] + 1)
    assert sent[1]["operation"] == "set_brightness" and sent[1]["value"] == 110


def test_mock_plug_computes_relative_brightness_and_reports_previous_actual():
    results = []
    plug = MockPlugAdapter(results.append)
    plug.handle_command({"command_id": "c1", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60}})
    assert results[-1]["previous"] == MockPlugAdapter.DEFAULT_BRIGHTNESS == 110 and results[-1]["actual"] == 176
    plug.handle_command({"command_id": "c2", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": -50}})
    assert (results[-1]["previous"], results[-1]["actual"]) == (176, 88)
    plug.handle_command({"command_id": "c3", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 300}})
    assert results[-1]["actual"] == 255                           # 상한
    plug.handle_command({"command_id": "c4", "target_id": "desk_lamp", "operation": "set_brightness", "value": 110})
    assert plug.brightness["desk_lamp"] == 110 and plug.state["desk_lamp"] == {"set_brightness": 110}


def test_mock_plug_applies_min_raw_for_wake_up():
    """min_raw(깨우기만): 결과 = max(계산값, min_raw). 꺼짐 0 → 153, 120 → 192(1.6 배가 더 크다). PC life_sim 과 같은 정의."""
    results = []
    plug = MockPlugAdapter(results.append)
    plug.handle_command({"command_id": "off", "target_id": "desk_lamp", "operation": "set_brightness", "value": 0})
    plug.handle_command({"command_id": "w1", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60, "min_raw": 153}})
    assert (results[-1]["previous"], results[-1]["actual"]) == (0, 153)
    plug.handle_command({"command_id": "set", "target_id": "desk_lamp", "operation": "set_brightness", "value": 120})
    plug.handle_command({"command_id": "w2", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60, "min_raw": 153}})
    assert (results[-1]["previous"], results[-1]["actual"]) == (120, 192)
    # min_raw 없는 묶음(환경 +30 %)은 꺼진 램프를 켜지 않는다
    plug.handle_command({"command_id": "off2", "target_id": "desk_lamp", "operation": "set_brightness", "value": 0})
    plug.handle_command({"command_id": "e", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 30}})
    assert results[-1]["actual"] == 0


# ── adapter · shipped config ───────────────────────────────────────────────────

def test_personalize_lamp_arms_are_relative_percent():
    cfg = load_personalize_config()
    arms = {a["id"]: a for a in cfg["arms"]}
    assert arm_commands(arms["light_cool_bright"]["device"]) == \
        [{"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 50}, "undo": "previous"}]
    assert arm_commands(arms["light_warm_dim"]["device"])[0]["value"] == {"percent": -50}
    assert arm_commands(arms["fan_on"]["device"]) == [dict(FAN)]  # 다른 기기는 그대로


def test_shipped_control_yaml_lamp_commands_are_relative():
    cfg = load_control_config()
    env_lamp = [a for a in cfg["actions"]["environment"] if a["target_id"] == "desk_lamp"]
    assert env_lamp == [{"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 30}, "undo": "previous"}]
    assert cfg["actions"]["posture"] == [dict(LAMP_UP, value={"percent": 60, "min_raw": 153})]   # 깨우기만 min_raw
    assert "adjust_brightness" not in cfg["irreversible_operations"]


# ── LiveHub ────────────────────────────────────────────────────────────────────

def test_live_posture_default_command_is_relative_and_undo_uses_previous(tmp_path):
    from deskmate_hub.inference import load_config
    from deskmate_hub.ingest import load_ingest_config

    cache, states = SensorCache(), []
    icfg = dict(load_ingest_config(), normalization="linear")
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub = LiveHub(cache, fsm_cfg=load_config(), ingest_cfg=icfg, control_cfg=CFG, publish=states.append, out=io.StringIO(),
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"), profile="")
    seen, _ = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen and State.MONITOR in seen
    ep = hub.control.history[-1]
    cmd = ep.commands[0]
    assert cmd.operation == "adjust_brightness" and cmd.value == {"percent": 60} and cmd.result["previous"] == 110
    assert hub.mock_plug.brightness["desk_lamp"] == 176
    # 자동 실행 뒤 거절(되돌리기 창 안) → set_brightness previous → 모의 램프가 110 으로 돌아간다
    cache.put_feedback({"verdict": "reject"})
    hub.tick_once(hub.engine._state_since + hub.period)
    assert ep.outcome == "undone" and hub.mock_plug.brightness["desk_lamp"] == 110
    assert ep.commands[-1].operation == "set_brightness" and ep.commands[-1].value == 110


def test_live_personalize_lamp_arm_goes_out_as_relative(tmp_path):
    """개인화 정책이 조명 arm 을 고르면(cause environment/cognitive) specs 가 adjust_brightness 로 디스패처에 간다."""
    from deskmate_hub.personalize import InterventionPolicy, EpisodeRecorder

    pcfg = load_personalize_config()
    policy = InterventionPolicy(pcfg)
    from deskmate_hub.ingest import load_ingest_config
    rec = EpisodeRecorder(pcfg, str(tmp_path / "p.jsonl"), boot_id="t", ingest_cfg=load_ingest_config(), policy=policy)

    class D:  # 정책 결정 중 arm 만 중요하다
        arm = "light_cool_bright"

    specs = rec.commands_for(D())
    assert specs == [{"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 50}, "undo": "previous"}]
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_BREAK", "cognitive", GateMode.AUTO, specs=specs)
    assert ep.executed and sent[0]["value"] == {"percent": 50}
    d.on_result({"command_id": sent[0]["command_id"], "status": "succeeded", "previous": 120, "actual": 180})
    d.on_feedback("reject", t["now"] + 1)
    assert sent[-1]["operation"] == "set_brightness" and sent[-1]["value"] == 120
