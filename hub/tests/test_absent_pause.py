"""부재 중엔 피로도를 쓰지 않고(상태·점수 멈춤), 부재가 이어지면 기기를 끈다 (11-02 사용자 기준).

11-02 실기: 자리를 뜬 뒤(present=0) 사람 신호가 빠진 점수가 환경·경과만으로 재정규화돼 피로도 0.32→0.69 로 튀었고,
2분 만에 FATIGUE → ACTION_ENV 로 빈자리에서 선풍기를 켜고 무드등을 올렸다(13:58:28).
"""
from __future__ import annotations

from deskmate_hub.control import ControlDispatcher
from deskmate_hub.inference import FSMEngine, GateMode, State

from test_fsm_transitions import drive_to_focus, frame

HOT = {"elapsed": {"delta": 0.95}, "environment": {"delta": 0.95}}   # 부재 때 남는 신호만으로 피로가 높게 나오는 경우


def test_absent_pauses_state_and_keeps_last_scores():
    e = FSMEngine()
    t = drive_to_focus(e)
    before = e.scores
    for dt in range(10, 600, 10):                                     # 10분 미만 부재 — 환경·경과만 높다
        r = e.tick(frame(t + dt, present=False, sig=HOT))
        assert r.state is State.FOCUS_PC and r.actions == ["absent_pause"]
    assert e.scores == before                                         # 부재 중 점수는 재실 때 마지막 값 그대로
    r = e.tick(frame(t + 605, present=True))                          # 돌아오면 그 상태에서 이어 간다
    assert r.state is State.FOCUS_PC and "absent_pause" not in r.actions


def test_absent_still_goes_idle_after_absent_idle_sec():
    e = FSMEngine()
    t = drive_to_focus(e)
    for dt in range(10, 600, 10):
        e.tick(frame(t + dt, present=False, sig=HOT))
    r = e.tick(frame(t + 610, present=False, sig=HOT))
    assert r.state is State.IDLE and r.actions == ["absent_timeout"]


def test_without_pause_key_old_behavior():
    e = FSMEngine()
    e.tm.pop("absent_pause_sec")
    t = drive_to_focus(e)
    r = e.tick(frame(t + 10, present=False, sig=HOT))
    assert "absent_pause" not in r.actions                             # 키를 지우면 예전처럼 부재 중에도 판정


def test_absent_off_specs_ignore_cooldown():
    sent = []
    t = {"now": 1000.0}
    cfg = {"result_timeout_sec": 15, "suggest_timeout_sec": 180, "undo_window_sec": 60, "cooldown_sec": 300,
           "irreversible_operations": ["set_power_off"],
           "actions": {"environment": [{"target_id": "vent_fan", "operation": "set_power", "value": "on"}]}}
    d = ControlDispatcher(cfg, publish_cmd=sent.append, clock=lambda: t["now"])
    d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO)    # 13:58 처럼 방금 켰다
    t["now"] += 90
    off = [{"target_id": "vent_fan", "operation": "set_power", "value": "off", "ignore_cooldown": True}]
    d.on_enter_action("ABSENT", "absent", GateMode.AUTO, specs=off)
    assert [(c["target_id"], c["value"]) for c in sent] == [("vent_fan", "on"), ("vent_fan", "off")]
    d.on_enter_action("ABSENT", "absent", GateMode.AUTO,               # 쿨다운 무시 표시가 없으면 예전처럼 생략
                      specs=[{"target_id": "vent_fan", "operation": "set_power", "value": "off"}])
    assert len(sent) == 2


def test_live_absent_off_once_after_after_sec(tmp_path):
    import copy
    import io

    from deskmate_hub.ingest import SensorCache
    from deskmate_hub.live import LiveHub
    from deskmate_hub.personalize import load_personalize_config
    from test_ingest_mapping import env, mm
    from test_posture_actions import CFG as CONTROL_CFG

    cfg = copy.deepcopy(CONTROL_CFG)
    cfg["adapter"] = "mqtt"                                              # mock 어댑터면 publish_control 로 안 나간다
    cfg["absent_off"] = {"enabled": True, "after_sec": 60, "commands": [
        {"target_id": "vent_fan", "operation": "set_power", "value": "off", "ignore_cooldown": True},
        {"target_id": "desk_lamp", "operation": "set_power", "value": "off", "ignore_cooldown": True},
        {"target_id": "music", "operation": "set_music", "value": {"on": False}, "ignore_cooldown": True}]}
    cache, cmds = SensorCache(), []
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub = LiveHub(cache, control_cfg=cfg, publish_control=cmds.append, out=io.StringIO(),
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"))
    t = 0.0
    for _ in range(9):                                                   # 처음부터 재실 없음 → 끄지 않는다
        cache.put("env", env(t, co2=700)); hub.tick_once(t); t += 10
    assert not [c for c in cmds if c.get("cause") == "absent"]
    for _ in range(3):                                                   # 재실
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += 10
    for _ in range(12):                                                  # 2분 부재 — 60 s 뒤 한 번만 끈다
        cache.put("mmwave", mm(t, present=False, level=0)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += 10
    off = [(c["target_id"], c["operation"], c["value"]) for c in cmds if c.get("cause") == "absent"]
    assert off == [("vent_fan", "set_power", "off"), ("desk_lamp", "set_power", "off"), ("music", "set_music", {"on": False})]
    for _ in range(2):                                                   # 돌아왔다 다시 부재 → 다음 부재 때 또
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += 10
    for _ in range(8):
        cache.put("mmwave", mm(t, present=False, level=0)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += 10
    assert len([c for c in cmds if c.get("cause") == "absent"]) == 6
