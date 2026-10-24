"""개인화 정책이 고르는 원인 제한(personalize.yaml policy.causes, 2026-10-25) — 환경 원인은 FSM 고정 명령만.

기본값 [posture, cognitive]: cause=environment 면 밴딧이 arm 을 고르지 않고(source fsm_fixed, FSM 게이트 그대로) control.yaml
actions.environment 가 나간다. 기록(에피소드)은 계속 남는다. causes 에 environment 를 넣으면 예전처럼 환경 arm 을 고른다.
"""
from __future__ import annotations

import copy
import io
import json

from deskmate_hub.inference import GateMode, State, load_config
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import InterventionPolicy, Situation, load_personalize_config
from deskmate_hub.personalize.policy import DEFAULT_POLICY_CAUSES

from test_control import CFG as CONTROL_CFG, env, mm


def sit(**kw) -> Situation:
    base = dict(ts=1_000_000.0, hour_local=15.5, weekday=2, month=10, posture_dist={"upright": 1.0},
                co2_ppm=1400, lux=300, temp_c=24.0, humidity_pct=50, pc_ratio=0.9, typing_active=True,
                c_fatigue=0.80, c_focus=0.3, session_elapsed_min=90)
    base.update(kw)
    return Situation(**base)


def test_default_config_restricts_policy_to_posture_and_cognitive():
    cfg = load_personalize_config()
    assert cfg["policy"]["causes"] == list(DEFAULT_POLICY_CAUSES) == ["posture", "cognitive"]
    p = InterventionPolicy(cfg)
    d = p.decide(sit(), cause="environment", gate="auto", session_id="s1")
    assert d.arm is None and d.source == "fsm_fixed" and d.delivered_gate == "none" and d.candidates == []
    assert "FSM 기본 명령" in d.explanation
    # 자세·인지 원인은 그대로 밴딧이 고른다
    dp = p.decide(sit(), cause="posture", gate="auto", session_id="s1")
    dc = p.decide(sit(), cause="cognitive", gate="auto", session_id="s1")
    assert dp.source == "bandit" and dp.arm in ("posture_card", "posture_voice", "sound_cue", "wake_light")
    assert dc.source == "bandit" and dc.arm in ("light_cool_bright", "light_warm_dim", "sound_cue", "break_short", "break_long")
    # 환경 결정은 세션 개입 횟수를 쓰지 않는다
    assert p._session_count == 2


def test_missing_policy_key_means_default_and_null_means_all_causes():
    cfg = load_personalize_config()
    del cfg["policy"]
    assert InterventionPolicy(cfg).decide(sit(), cause="environment", gate="auto", session_id="s").source == "fsm_fixed"
    cfg["policy"] = {"causes": None}
    d = InterventionPolicy(cfg).decide(sit(), cause="environment", gate="auto", session_id="s")
    assert d.source == "bandit" and d.arm in ("fan_on", "ventilate_suggest", "light_cool_bright", "air_fan_up")
    cfg["policy"] = {"causes": ["environment", "posture", "cognitive"]}
    assert InterventionPolicy(cfg).decide(sit(), cause="environment", gate="auto", session_id="s").source == "bandit"


def _hub_to_action_env(tmp_path, pcfg):
    """test_control 과 같은 길: 비PC·CO₂ 지배 가중치로 FATIGUE → CAUSE_ANALYSIS → ACTION_ENV. 정책은 켠 채."""
    icfg = dict(load_ingest_config(), normalization="linear")
    fcfg = copy.deepcopy(load_config())
    fcfg["weights"]["fatigue"]["npc"] = {"keystroke": 0.0, "posture": 0.15, "respiration": 0.0, "environment": 0.70, "elapsed": 0.15}
    cache, states = SensorCache(), []
    hub = LiveHub(cache, fsm_cfg=fcfg, ingest_cfg=icfg, control_cfg=CONTROL_CFG, publish=states.append, out=io.StringIO(),
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"), profile="")
    period, baseline, t = hub.period, float(hub.fsm_cfg["timers"]["baseline_sec"]), 0.0
    while t <= baseline + 2 * period:
        cache.put("mmwave", mm(t, 40)); hub.tick_once(t); t += period
    seen, accepted = [], 0
    for _ in range(int(1500 / period)):
        cache.put("mmwave", mm(t, 40)); cache.put("env", env(t, 2000)); hub.tick_once(t); t += period
        seen.append(hub.engine.state)
        if hub.engine.state is State.ACTION_ENV and hub.control.episode and hub.control.episode.awaiting_user:
            cache.put_feedback({"verdict": "accept"}); accepted += 1
        if hub.engine.state is State.MONITOR:
            break
    return hub, states, seen, accepted


def test_live_action_env_with_policy_on_sends_fsm_commands_and_logs_fixed_episode(tmp_path):
    pcfg = dict(load_personalize_config(), log_dir=str(tmp_path))
    assert pcfg["enabled"] is True
    hub, states, seen, accepted = _hub_to_action_env(tmp_path, pcfg)
    assert State.ACTION_ENV in seen and State.MONITOR in seen, seen[-10:]
    ep = hub.control.history[-1]
    assert ep.state == "ACTION_ENV" and ep.cause == "environment" and ep.outcome == "executed"
    # control.yaml actions.environment 그대로(선풍기 + 램프 +30 %) — 정책 arm(환기 카드 등)이 아니다
    assert {(c.target_id, c.operation) for c in ep.commands} == {("vent_fan", "set_power"), ("desk_lamp", "adjust_brightness")}
    assert hub.mock_plug.state.get("vent_fan") == {"set_power": "on"}
    env_states = [s["data"] for s in states if s["data"]["fsm_state"] == "ACTION_ENV"]
    assert env_states and "personalize:ask" not in (env_states[0].get("actions") or [])
    view = env_states[0]["sensor_summary"].get("personalize")
    assert view and view["source"] == "fsm_fixed" and view["arm"] is None and view["delivered_gate"] == env_states[0]["gate"]
    # 환경 원인 에피소드도 로그에 남는다(source fsm_fixed), 밴딧 학습 대상은 아니다
    hub.personalize_recorder.close_all(10_000.0)
    lines = [json.loads(l) for l in (tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    eps = [r for r in lines if r.get("type", "episode") == "episode" or "decision" in r]
    fixed = [r for r in eps if (r.get("decision") or {}).get("source") == "fsm_fixed"]
    assert fixed and fixed[0]["fsm"]["cause"] == "environment"
    assert hub.personalize_policy.n_feedback == 0


def test_live_action_env_with_environment_allowed_lets_policy_choose(tmp_path):
    pcfg = dict(load_personalize_config(), log_dir=str(tmp_path), policy={"causes": ["environment", "posture", "cognitive"]})
    hub, states, seen, accepted = _hub_to_action_env(tmp_path, pcfg)
    assert State.ACTION_ENV in seen
    env_states = [s["data"] for s in states if s["data"]["fsm_state"] == "ACTION_ENV"]
    view = env_states[0]["sensor_summary"].get("personalize")
    assert view and view["source"] in ("bandit", "rule_sleep") and view["arm"] is not None
