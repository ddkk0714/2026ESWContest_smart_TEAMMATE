"""ACTION_POSTURE → control.yaml `actions.posture`(졸음 → 무드등 깨우기, 2026-10-24).

다섯 경우: 자동 실행 · 제안(수락 뒤 실행) · 되돌리기 창 · 개인화 spec 우선 · 설정에 묶음이 없으면 예전과 같음.
끝에 LiveHub 전체 경로: 자세 서비스 drowsy → FATIGUE → ACTION_POSTURE → mock 플러그 desk_lamp +60 % → MONITOR.
10-25 부터 밝기는 지금 대비 %(adjust_brightness {percent: 60}, 되돌리기는 결과의 previous) — test_adjust_brightness.py 가 그 계약을 따로 본다.
"""
from __future__ import annotations

import copy
import io

import pytest

from deskmate_hub.control import ControlDispatcher, load_control_config
from deskmate_hub.inference import GateMode, State
from deskmate_hub.ingest import SensorCache
from deskmate_hub.live import LiveHub

from test_ingest_mapping import env, mm
from test_tof_service import svc

LAMP = {"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60, "min_raw": 153}, "undo": "previous"}
PCT60 = {"percent": 60, "min_raw": 153}     # 깨우기: 지금의 1.6 배, 최소 153(60 %)
CFG = {"enabled": True, "adapter": "mock", "result_timeout_sec": 15, "suggest_timeout_sec": 180, "undo_window_sec": 60,
       "cooldown_sec": 300, "irreversible_operations": ["set_power_off"],
       "actions": {"environment": [{"target_id": "vent_fan", "operation": "set_power", "value": "on", "undo_value": "off"}],
                   "posture": [dict(LAMP)]}}
CFG_NO_POSTURE = {**CFG, "actions": {"environment": CFG["actions"]["environment"]}}


def make(cfg=CFG, start=1000.0):
    sent = []
    t = {"now": start}
    return ControlDispatcher(cfg, publish_cmd=sent.append, clock=lambda: t["now"]), sent, t


# ── 1. 자동(≥0.75): 즉시 발행, 결과가 오면 완료 ─────────────────────────────

def test_posture_auto_dispatches_lamp_and_completes_on_result():
    d, sent, t = make()
    assert d.has_default_actions("ACTION_POSTURE", "posture")
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    assert ep.executed and ep.outcome == "executed" and len(sent) == 1
    cmd = sent[0]
    assert (cmd["target_id"], cmd["operation"], cmd["value"], cmd["origin_state"], cmd["cause"], cmd["gate"]) == \
        ("desk_lamp", "adjust_brightness", PCT60, "ACTION_POSTURE", "posture", "auto")
    assert cmd["requires_confirmation"] is False
    assert not d.action_done()                                   # 결과 전
    d.on_result({"command_id": cmd["command_id"], "status": "succeeded", "previous": 110, "actual": 176, "actual_value": 176})
    assert d.action_done()
    # 같은 target/operation 은 쿨다운(300 s) 안에 다시 보내지 않는다
    d.close_episode(); t["now"] += 100
    assert d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO).outcome == "skipped" and len(sent) == 1


# ── 2. 제안(0.45~0.75): 카드 수락 뒤 실행, 거절이면 실행 없음 ─────────────────

def test_posture_suggest_waits_for_accept():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.SUGGEST)
    assert ep.awaiting_user and sent == [] and not d.action_done()
    t["now"] += 30
    d.on_feedback("accept")
    assert ep.executed and len(sent) == 1 and sent[0]["value"] == PCT60 and sent[0]["gate"] == "suggest"
    d.close_episode(); d._last_sent.clear()
    ep2 = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.SUGGEST)
    d.on_feedback("reject")
    assert ep2.outcome == "rejected" and len(sent) == 1 and d.action_done()


# ── 3. 자동 실행 뒤 거절 → 되돌리기 창(60 s) 안에서만 undo 110 ─────────────────

def test_posture_auto_reject_undoes_within_window_only():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    d.on_result({"command_id": sent[0]["command_id"], "status": "succeeded", "previous": 110, "actual": 176})
    d.close_episode()
    t["now"] += 30
    d.on_feedback("reject")
    assert ep.outcome == "undone" and len(sent) == 2 and sent[1]["gate"] == "undo"
    assert sent[1]["operation"] == "set_brightness" and sent[1]["value"] == 110      # 결과의 previous 로 되돌린다
    d2, sent2, t2 = make()
    ep2 = d2.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO)
    d2.on_result({"command_id": sent2[0]["command_id"], "status": "succeeded", "previous": 110, "actual": 176})
    d2.close_episode()
    t2["now"] += 90                                              # 창이 지남
    d2.on_feedback("reject")
    assert ep2.outcome == "executed" and len(sent2) == 1


# ── 4. 개인화 정책이 기기 arm spec 을 주면 그것이 우선 ─────────────────────────

def test_policy_specs_take_precedence_over_posture_actions():
    d, sent, t = make()
    ep = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO,
                           specs=[{"target_id": "desk_lamp", "operation": "set_brightness", "value": 200, "undo_value": 110}])
    assert ep.executed and [c["value"] for c in sent] == [200]
    d.close_episode(); d._last_sent.clear()
    ep2 = d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO, specs=[])   # 화면·음성 arm: 기기 명령 없음
    assert ep2.outcome == "skipped" and len(sent) == 1


# ── 5. 설정에 actions.posture 가 없으면 예전과 완전히 같다 ─────────────────────

def test_without_posture_actions_nothing_changes():
    d, sent, t = make(CFG_NO_POSTURE)
    assert not d.has_default_actions("ACTION_POSTURE", "posture")
    assert d.on_enter_action("ACTION_POSTURE", "posture", GateMode.AUTO).outcome == "skipped" and sent == []
    d.close_episode()
    assert d.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO).executed and sent[-1]["target_id"] == "vent_fan"
    d.close_episode()
    # ACTION_BREAK 는 묶음이 있어도 기본 명령이 없다(사람이 쉬는 일)
    d3, sent3, _ = make()
    assert not d3.has_default_actions("ACTION_BREAK", "cognitive")
    assert d3.on_enter_action("ACTION_BREAK", "cognitive", GateMode.AUTO).outcome == "skipped" and sent3 == []
    # ACTION_ENV 는 묶음 유무와 무관하게 그대로
    assert d3.has_default_actions("ACTION_ENV", "environment")


def test_shipped_control_yaml_has_reversible_posture_bundle():
    cfg = load_control_config()
    posture = cfg["actions"]["posture"]
    assert posture == [LAMP]
    assert "adjust_brightness" not in cfg["irreversible_operations"] and "set_brightness" not in cfg["irreversible_operations"]


# ── LiveHub 전체 경로: 자세 서비스 drowsy → ACTION_POSTURE → mock 플러그 → MONITOR ──

def _drive_to_monitor(hub: LiveHub, cache: SensorCache, *, co2: int):
    period, baseline = hub.period, float(hub.fsm_cfg["timers"]["baseline_sec"])
    t = 0.0
    while t <= baseline + 2 * period:                             # 착석·활동 → 세션 시작 → FOCUS
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += period
    seen, accepted = [], 0
    for _ in range(int(3600 / period)):
        cache.put("mmwave", mm(t, level=5)); cache.put("env", env(t, co2=co2))
        cache.put("tof_service", svc(t, posture="drowsy", conf=0.92))
        hub.tick_once(t); t += period
        seen.append(hub.engine.state)
        if hub.engine.state is State.ACTION_POSTURE and hub.control.episode and hub.control.episode.awaiting_user:
            cache.put_feedback({"verdict": "accept"}); accepted += 1
        if hub.engine.state is State.MONITOR:
            break
    return seen, accepted


@pytest.fixture
def posture_configs(tmp_path):
    from deskmate_hub.inference import load_config
    from deskmate_hub.ingest import load_ingest_config
    from deskmate_hub.personalize import load_personalize_config

    icfg = dict(load_ingest_config(), normalization="linear")
    fcfg = copy.deepcopy(load_config())
    # 비PC 컨텍스트: 자세 0.40·환경 0.20 이 지배 → 졸음(delta 1.0)+CO₂ 로 피로 확정, dominant 는 posture
    # 정책은 끈다(기본 명령 경로 검사) — 10-26 부터 정책이 켜지면 밴딧이 고른 arm 이 실행된다(test_arm_delivery.py)
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    return fcfg, icfg, pcfg


def test_live_hub_drowsy_turns_desk_lamp_on_then_monitor(posture_configs, tmp_path):
    fcfg, icfg, pcfg = posture_configs
    cache, states = SensorCache(), []
    hub = LiveHub(cache, fsm_cfg=fcfg, ingest_cfg=icfg, control_cfg=CFG, publish=states.append, out=io.StringIO(),
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"), profile="")
    assert hub.control is not None and hub.mock_plug is not None
    seen, _ = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen and State.MONITOR in seen, seen[-10:]
    # 모의 램프 110 → +60 % = 176. 명령은 % 만, 원시 밝기는 램프(앱) 쪽 계산
    assert hub.mock_plug.state.get("desk_lamp") == {"adjust_brightness": PCT60, "brightness": 176}
    assert hub.control.episode is None and hub.control.history[-1].state == "ACTION_POSTURE"
    assert hub.control.history[-1].commands[0].result["previous"] == 110
    assert hub.control.history[-1].outcome == "executed" and hub.control.history[-1].cause == "posture"
    ctrl = [s["data"]["sensor_summary"].get("control") for s in states if s["data"]["fsm_state"] == "ACTION_POSTURE"]
    assert ctrl and ctrl[0]["active"] is True and ctrl[0]["commands"][0]["target"] == "desk_lamp"


def test_demo_b_profile_keeps_policy_gate_auto_so_lamp_goes_on_without_a_card(tmp_path):
    """10-24 PC 리허설 결함: FSM 은 자동인데 개인화 정책이 자기 conf_auto 0.75 로 제안(personalize:ask)으로 낮춰 카드만 떴다.
    demo-b 프로파일이 personalize gate.conf_auto 도 0.55 로 얹으므로, 정책을 켠 채(Pi 4 기본) 자동으로 기본 명령이 나가야 한다."""
    from deskmate_hub.personalize import load_personalize_config

    cache, states = SensorCache(), []
    # Pi 4 기본(정책 켬). 10-26 부터 밴딧이 고른 arm 이 실행되므로 수험생 온보딩(밝은 조명 선호·소리 작게)을 얹고 평균만 쓰게(greedy)
    # 해 wake_light 가 결정되게 한다 — 그래야 램프 경로가 정책을 통해서도 자동으로 나간다
    pcfg = dict(load_personalize_config(), log_dir=str(tmp_path))
    pcfg["bandit"] = dict(pcfg["bandit"], algorithm="greedy")
    assert pcfg["enabled"] is True and pcfg["gate"]["conf_auto"] == 0.75           # Pi 4 배포판 기본
    hub = LiveHub(cache, control_cfg=CFG, publish=states.append, out=io.StringIO(), profile="demo-b",
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"))
    assert hub.personalize_policy is not None and hub.personalize_cfg["gate"]["conf_auto"] == 0.55
    hub.personalize_policy.apply_user_profile({"light_pref": "high", "sound_pref": "low", "wind_pref": "off"})
    assert hub.fsm_cfg["gate"]["conf_auto"] == 0.55 and hub.engine.tm["baseline_sec"] == 10
    seen, accepted = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen and State.MONITOR in seen, seen[-10:]
    assert accepted == 0, "카드(suggest)가 떴다 — 정책이 자동을 낮췄다"
    ep = hub.control.history[-1]
    assert ep.state == "ACTION_POSTURE" and ep.gate is GateMode.AUTO and ep.outcome == "executed"
    assert hub.mock_plug.state.get("desk_lamp") == {"adjust_brightness": PCT60, "brightness": 176}
    posture_states = [s["data"] for s in states if s["data"]["fsm_state"] == "ACTION_POSTURE"]
    assert posture_states and posture_states[0]["sensor_summary"]["control"]["gate"] == "auto"
    assert "personalize:ask" not in (posture_states[0].get("actions") or [])


def test_live_hub_without_posture_bundle_keeps_lamp_off(posture_configs, tmp_path):
    fcfg, icfg, pcfg = posture_configs
    cache = SensorCache()
    hub = LiveHub(cache, fsm_cfg=fcfg, ingest_cfg=icfg, control_cfg=CFG_NO_POSTURE, out=io.StringIO(),
                  personalize_cfg=dict(pcfg, enabled=False), personalize_log_path=str(tmp_path / "p.jsonl"), profile="")
    seen, _ = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen and State.MONITOR in seen
    assert "desk_lamp" not in hub.mock_plug.state
    assert all(ep.state != "ACTION_POSTURE" for ep in hub.control.history) and hub.control.episode is None
