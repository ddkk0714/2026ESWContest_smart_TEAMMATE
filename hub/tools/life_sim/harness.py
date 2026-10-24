"""폐루프 본체 — 각본 → 사람 → 두 센서 → 자세·졸음 모델 → hub(10 s tick, personalize 정책 포함) → 개입 → 기기·사람 반응 → 각본 변경.

    res = run_scenario(load_scenario(path), out_dir="runs/lifesim", model_path="models/opt_l8v5_c256_best.pt")

출력(out_dir/<persona>_<name>/)
    frames.jsonl        1 초마다: 정답 라벨·구간·모델 판정·0x03 특징·환경·기기·마지막 FSM 상태
    ticks.jsonl         hub tick(10 s)마다: 상태·게이트·원인·C_fatigue·C_focus·actions·요청·명령·정책 결정
    events.jsonl        개입(채널·arm·설명)·사람 반응·피드백·기기 명령·구간 전환
    personalize.jsonl   hub 의 personalize 에피소드 로그(결정·피드백·10 분 뒤 결과) — policy.engine: hub 일 때
    policy.json         계정 A 의 학습된 정책(합성 30 일) / B 는 없음(전역 prior)
    summary.json / .md  지표 표
    hub.log, esm.jsonl

정책 두 가지
    hub    (기본) hub/deskmate_hub/personalize — LiveHub 안에서 결정 tick 마다 InterventionPolicy.decide: arm·전달 게이트(자동→제안만)·
           기기 spec(조명·선풍기)·설명. 피드백·되돌리기·10 분 뒤 효과는 EpisodeRecorder 가 정책에 되돌린다. life_sim 은 arm → 채널만 잇는다.
    simple life_sim 안의 간이 정책(policy.py) — personalize 패키지가 오기 전 대역. 비교·회귀용으로 남긴다.
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import numpy as np
import yaml

from .devices import DeviceWorld
from .hub_bridge import HubBridge
from .policy import GROUP_OF, SimplePolicy, synth_profile
from tools.posture_sim.mmwave import MmwaveSensor, presence_decision
from tools.posture_sim.posture_model import OraclePosture, RuleFeatures
from tools.posture_sim.source import TofsimSource, where_now as _where_now
from .env_sensors import EnvSensors
from .fatigue import FatigueModel
from .purifier import PurifierController, purifier_metrics
from .ventilation import VentRule, vent_metrics
from .reaction import Reaction, ReactionModel
from .scenario import fsm_config_path

FOCUS_STATES = {"FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC"}
SLEEPY = ("drowsy", "face_down")
FIXABLE = ("chin_rest", "turtle", "lean_forward", "recline", "elbows")
WEATHER_PERIOD_S = 1800.0


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if hasattr(x, "__dict__") and not isinstance(x, type):
        return _jsonable(vars(x))
    return x


class _Writer:
    def __init__(self, path: str):
        self.fh = open(path, "w", encoding="utf-8")

    def write(self, rec: dict[str, Any]) -> None:
        self.fh.write(json.dumps(_jsonable(rec), ensure_ascii=False) + "\n")

    def close(self) -> None:
        self.fh.close()


# ---------------------------------------------------------------- 정책 준비

def _policy_engine(scenario: dict[str, Any]) -> tuple[str, str]:
    pol = scenario.get("policy") or {}
    engine = str(pol.get("engine", "hub"))
    profile = str(pol.get("profile") or "none")
    if profile == "A":
        profile = "synth"
    elif profile == "B":
        profile = "global"
    if engine == "hub":
        try:
            import deskmate_hub.personalize  # noqa: F401
        except ImportError:
            engine = "simple"
    return engine, profile


def _make_simple_policy(scenario: dict[str, Any], profile: str, seed: int) -> SimplePolicy | None:
    pcfg = scenario.get("policy_resolved") or {}
    if not pcfg.get("enabled") or profile == "none":
        return None
    if profile == "synth":
        return SimplePolicy(pcfg, profile=synth_profile(scenario["persona"], seed=seed), seed=seed, name=f"A_synth30d_{scenario['persona']}")
    return SimplePolicy(pcfg, profile=None, seed=seed, name="B_global_default")


def _setup_hub_policy(scenario: dict[str, Any], profile: str, run_dir: str, seed: int) -> tuple[dict | None, str | None, str | None, dict]:
    """policy.engine: hub — personalize.yaml 을 켜고, 계정 A 면 합성 30 일 로그로 정책을 먼저 학습해 policy.json 에 둔다."""
    if profile == "none":
        return None, None, None, {"engine": "hub", "profile": "none"}
    from deskmate_hub.personalize import InterventionPolicy, load_personalize_config
    pol = scenario.get("policy") or {}
    cfg = dict(load_personalize_config(), enabled=True, record=True, profile_id=f"{scenario['persona']}_{profile}", log_dir=run_dir)
    if pol.get("algorithm"):
        cfg["bandit"] = dict(cfg["bandit"], algorithm=str(pol["algorithm"]))
    policy_path = os.path.join(run_dir, "policy.json")
    log_path = os.path.join(run_dir, "personalize.jsonl")
    for p in (policy_path, log_path):
        if os.path.exists(p):
            os.remove(p)
    info: dict[str, Any] = {"engine": "hub", "profile": profile, "profile_id": cfg["profile_id"], "algorithm": cfg["bandit"].get("algorithm")}
    if profile == "synth":
        from deskmate_hub.personalize.synth import PERSONAS, SynthGenerator
        pid = pol.get("synth_persona") or (scenario["devices_resolved"].get("synth_persona") or {}).get(scenario["persona"]) \
            or (scenario["devices_resolved"].get("synth_persona") or {}).get("default", "afternoon_slumper")
        days = int(pol.get("days", 30))
        policy = InterventionPolicy(cfg, rng=np.random.default_rng(seed))
        gen = SynthGenerator(PERSONAS[pid], seed=seed, days=days, cfg=cfg)
        records = gen.generate(policy=policy)
        policy.save(policy_path)
        info.update(synth_persona=pid, synth_days=days, synth_records=len(records), n_feedback=policy.n_feedback,
                    policy_mode=policy.policy_mode, auto_threshold={a: round(policy.auto_threshold(a), 3) for a in policy.arms},
                    arm_stats={a: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in st.items()
                                   if k in ("n", "accept", "reject", "undo", "accept_rate", "effect_mean")}
                               for a, st in policy.arm_stats().items()})
    return cfg, policy_path, log_path, info


def _parse_personalize_log(path: str | None) -> dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    from deskmate_hub.personalize import read_records
    recs = read_records(path)
    eps = [r for r in recs if r.get("schema_name") == "personalize_episode"]
    sessions = [r for r in recs if r.get("schema_name") == "personalize_session"]
    by_arm: dict[str, int] = {}
    verdicts: dict[str, int] = {}
    gates: dict[str, int] = {}
    excluded: dict[str, int] = {}
    asked = 0
    rewards: list[float] = []
    rows = []
    for r in eps:
        d, fb, oc, fsm = r.get("decision") or {}, r.get("feedback") or {}, r.get("outcome") or {}, r.get("fsm") or {}
        arm = d.get("arm") or "fallback"
        by_arm[arm] = by_arm.get(arm, 0) + 1
        v = fb.get("verdict") or "none"
        verdicts[v] = verdicts.get(v, 0) + 1
        g = f"{fsm.get('gate')}→{d.get('delivered_gate')}"
        gates[g] = gates.get(g, 0) + 1
        if fsm.get("gate") == "auto" and d.get("delivered_gate") == "suggest":
            asked += 1
        for why in (d.get("excluded") or {}).values():
            excluded[why] = excluded.get(why, 0) + 1
        if oc.get("reward") is not None:
            rewards.append(float(oc["reward"]))
        rows.append({"t_ms": r.get("ts_ms"), "cause": fsm.get("cause"), "fsm_gate": fsm.get("gate"), "arm": d.get("arm"),
                     "delivered": d.get("delivered_gate"), "source": d.get("source"), "verdict": fb.get("verdict"),
                     "outcome": oc.get("status"), "reward": oc.get("reward"), "trend": oc.get("trend"),
                     "excluded": d.get("excluded"), "explanation": d.get("explanation"), "mode": d.get("policy_mode")})
    return {"episodes": len(eps), "sessions": len(sessions), "by_arm": by_arm, "verdicts": verdicts, "gates": gates,
            "asked_by_policy": asked, "excluded": excluded, "mean_reward": round(sum(rewards) / len(rewards), 3) if rewards else None,
            "rows": rows}


# ---------------------------------------------------------------- 본체

def run_scenario(scenario: dict[str, Any], *, out_dir: str, model_path: str | None = None, posture: str = "gru",
                 source: Any = None, backend: str | None = None, tofsim_root: str | None = None,
                 progress: bool = True, label: str | None = None, replay: bool = False) -> dict[str, Any]:
    """각본 하나를 끝까지 돌린다. posture: gru(torch 체크포인트) | oracle(정답 라벨). source: 사람 소스를 직접 줄 때(시험).
    replay=True 면 프레임마다 Pose·두 센서 격자·판정을 replay.jsonl 에 남긴다(video 명령이 3D 로 다시 렌더한다)."""
    name = label or f"{scenario['persona']}_{scenario['name']}"
    run_dir = os.path.join(out_dir, name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "scenario.json"), "w", encoding="utf-8") as fh:
        json.dump(_jsonable({k: v for k, v in scenario.items() if not k.endswith("_resolved")}), fh, ensure_ascii=False, indent=1)
    fps = float(scenario["fps"])
    t0 = float(scenario["t0"])
    seed = int(scenario["seed"])
    rnd = random.Random(seed + 7)
    started = time.time()
    dev = scenario["devices_resolved"]

    src = source if source is not None else TofsimSource(scenario, backend=backend or scenario["backend"], tofsim_root=tofsim_root)
    rules = RuleFeatures((dev.get("rule_features") or {}), fps=fps)
    _truth: dict[str, str] = {}
    if posture == "oracle" or (source is not None and model_path is None):
        model = OraclePosture(lambda: getattr(src, "last_label", None) or _truth.get("label", "upright"), seed=seed)
    else:
        from tools.posture_sim.posture_model import make_gru
        model = make_gru(model_path, dev.get("posture_model") or {}, fps=fps)
    engine, profile = _policy_engine(scenario)
    pcfg, policy_path, plog_path, policy_info = (None, None, None, {"engine": engine, "profile": profile})
    if engine == "hub":
        pcfg, policy_path, plog_path, policy_info = _setup_hub_policy(scenario, profile, run_dir, seed)
    from deskmate_hub.ingest import load_ingest_config
    ingest_cfg = load_ingest_config()
    if scenario["hub"].get("presence_priority"):
        ingest_cfg["presence_priority"] = scenario["hub"]["presence_priority"]     # mmwave | any (비교 실험)
    if scenario["hub"].get("desk_max_cm") is not None:
        ingest_cfg["desk_max_cm"] = float(scenario["hub"]["desk_max_cm"])        # 책상 재실 거리 민감도 실험
    for item in scenario["hub"].get("ingest_set") or []:                         # --ingest-set a.b=값 (실험)
        key, _, raw = str(item).partition("=")
        *path, leaf = key.strip().split(".")
        node = ingest_cfg
        for k in path:
            node = node.setdefault(k, {})
        node[leaf] = yaml.safe_load(raw)
    hub = HubBridge(posture_model=model, fsm_config=fsm_config_path(scenario["hub"].get("fsm_config")), ingest_cfg=ingest_cfg,
                    esm_log_path=os.path.join(run_dir, "esm.jsonl"), t0=t0,
                    personalize_cfg=pcfg, personalize_policy_path=policy_path, personalize_log_path=plog_path)
    hub_policy = getattr(hub.hub, "personalize_policy", None)
    if hub_policy is not None:
        hub_policy.rng = np.random.default_rng(seed + 11)       # Thompson 샘플도 시드로 고정(재현)
        policy_info["loaded_n_feedback"] = hub_policy.n_feedback
    period = float(scenario["hub"].get("tick_sec") or hub.period)
    world = DeviceWorld(dev, scenario["env"], scenario["weather"], t0, seed=seed)
    purifier = PurifierController(dev.get("purifier_control") or {}, seed=seed, t0=t0)
    vent_rule = VentRule(dev.get("vent_control") or {}, t0=t0)
    mmw = MmwaveSensor(dev.get("mmwave") or {}, seed=seed)
    envs = EnvSensors(dev.get("env_sensors") or {}, seed=seed)
    env_meas: dict[str, Any] = {}
    channels_cfg = dev.get("channels") or {}
    arm_channels = dev.get("arm_channels") or {}
    arm_card_table = dev.get("arm_card_table") or {}
    reaction = ReactionModel(scenario["reaction_resolved"], channels_cfg, seed=seed + 3)
    fat = FatigueModel(scenario["reaction_resolved"].get("fatigue") or {}, seed=seed, t0=t0)   # 잠재 피로(기본 꺼짐)
    absent_since: float | None = None
    # 2단계 학습용 로그(ml/training/build_dataset.py --logs --truth): hub tick 마다 SensorFrame·state·정답 한 줄씩, 줄 수가 같다
    truth_log = None
    stage2 = scenario["hub"].get("stage2")        # 2단계 피로 모델(portable 폴더) — 관찰 모드, FSM 게이트는 안 바꾼다
    if stage2:
        from deskmate_hub.personalization import PersonalizationRuntime, load_personalization_config
        hub.hub.personalization_cfg = dict(load_personalization_config(), enabled=True, mode="observe", backend="portable_cnn",
                                           model_path=os.path.join(stage2, "common.portable.json"),
                                           metadata_path=os.path.join(stage2, "common.metadata.json"))
        hub.hub.personalization = PersonalizationRuntime(hub.hub._effective_model_config(), period=hub.hub.period,
                                                         normalization=hub.hub.ingest_cfg.get("normalization", "linear"))
    if fat.enabled or scenario["hub"].get("logs") or stage2:
        hub.hub.frame_log = open(os.path.join(run_dir, "frames-hub.jsonl"), "w", encoding="utf-8")
        hub.hub.state_log = open(os.path.join(run_dir, "state-hub.jsonl"), "w", encoding="utf-8")
        truth_log = open(os.path.join(run_dir, "truth-hub.jsonl"), "w", encoding="utf-8")
    simple = _make_simple_policy(scenario, profile, seed) if engine == "simple" else None
    card_expires = float(hub.fsm_cfg["timers"].get("focus_break_poll_sec", 180))

    w_frames, w_ticks, w_events = (_Writer(os.path.join(run_dir, f)) for f in ("frames.jsonl", "ticks.jsonl", "events.jsonl"))
    w_replay = _Writer(os.path.join(run_dir, "replay.jsonl")) if replay else None
    from dataclasses import asdict as _asdict, is_dataclass as _is_dc
    pending: list[Reaction] = []
    last_tick: dict[str, Any] | None = None
    last_verdict: dict[str, Any] | None = None
    interventions: list[dict[str, Any]] = []
    second_rows: list[dict[str, Any]] = []
    tick_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    seg_t0: dict[int, float] = {}
    env_sent = False
    next_weather = t0
    counters = {"fatigue": 0, "escalate": 0, "bedtime_suggested": 0, "vent_cards": 0}
    held_commands: dict[str, list[dict[str, Any]]] = {}       # simple: 정책이 자동→제안으로 내린 env 명령
    open_decisions: dict[str, Any] = {}                       # simple: 카드 하나당 열린 정책 결정

    def emit(ev: dict[str, Any]) -> None:
        event_rows.append(ev)
        w_events.write(ev)

    def person_kind() -> str:
        return src.current.kind if not src.done else "away"

    def where_now() -> str:
        """desk | room(방 안 다른 곳 — 침대·주방) | out. 각본 구간의 where, 없으면 취침=room, 그 밖은 각본 away_where(기본 out)."""
        return _where_now(src, default=str(scenario.get("away_where", "out")))

    def in_focus() -> bool:
        return bool(last_tick and last_tick["state"] in FOCUS_STATES)

    def c_fat_now() -> float:
        return float(last_tick["c_fatigue"]) if last_tick else 0.0

    def apply_channels(chans: list[str], now: float, payload: dict[str, Any] | None = None) -> None:
        for ch in chans:
            if ch != "hub_commands":
                world.apply_channel(ch, now, payload)

    def arm_chans(arm: str | None, gate: str, fallback_kind: str) -> list[str]:
        if arm and arm in arm_channels:
            return list((arm_channels[arm] or {}).get(gate) or [])
        return reaction.channels_for(fallback_kind)

    # ---- simple 엔진 전용 도우미
    def schedule_policy_followups(d, ev_kind: str, now: float) -> None:
        if simple is None or d is None or not d.allow:
            return
        open_decisions[ev_kind] = d
        pending.append(Reaction(now + float((simple.c.get("reward") or {}).get("effect_window_s", 600)), "policy_effect",
                                {"event": ev_kind, "c_fat_start": c_fat_now()}, source="policy"))
        if d.gate == "suggest":
            pending.append(Reaction(now + card_expires + 1.0, "policy_expire", {"event": ev_kind}, source="policy"))

    def policy_outcome(ev_kind: str, outcome: str, now: float) -> None:
        if simple is None:
            return
        d = open_decisions.get(ev_kind)
        if d is not None and d.outcome is None:
            simple.outcome(d, outcome, now)
            emit({"t": now, "kind": "policy_outcome", "event": ev_kind, "outcome": outcome, "reward": d.reward, "policy": simple.snapshot()})

    def maybe_go_rest(now: float, source: str) -> None:
        rest = scenario["reaction_resolved"].get("rest") or {}
        if reaction.rnd.random() < float(rest.get("go_away_p", 0.0)):
            pending.append(Reaction(now + reaction._u(rest.get("delay_s"), (20.0, 90.0)), "go_rest",
                                    {"seconds": reaction._u(rest.get("away_s"), (180.0, 420.0))}, source=source))

    # ---------------------------------------------------------------- 예약된 반응 실행
    def run_due(now: float) -> None:
        nonlocal pending
        due = [r for r in pending if r.due <= now]
        pending = [r for r in pending if r.due > now]
        for r in due:
            if r.kind == "policy_effect":
                d = open_decisions.get(r.payload["event"])
                if simple is not None and d is not None and d.effect is None:
                    simple.effect(d, r.payload["c_fat_start"], c_fat_now(), now)
                    emit({"t": now, "kind": "policy_effect", "event": r.payload["event"], "effect": d.effect, "reward": d.reward})
                continue
            if r.kind == "policy_expire":
                d = open_decisions.get(r.payload["event"])
                if d is not None and d.outcome is None:
                    policy_outcome(r.payload["event"], "none", now)
                    held_commands.pop(r.payload["event"], None)
                continue
            if src.done:
                continue
            if r.kind == "wake":
                if person_kind() in SLEEPY:
                    # 잠재 피로가 만든 졸음이면 깬 뒤 남은 졸음을 잇지 않는다(다시 조는 건 피로 모델이 정한다)
                    fat_episode = fat.enabled and str(src.current.override or "").startswith("fatigue_")
                    rec = src.override("upright", r.payload["awake_s"], transition_s=r.payload["transition_s"], name="wake",
                                       typing=bool(scenario["reaction_resolved"].get("wake_typing", True)), resume=not fat_episode)
                    reaction.wakes += 1
                    fat.on_wake(now, source=r.source)
                    emit({"t": now, "kind": "reaction", "what": "wake", "source": r.source, "override": rec})
                else:
                    emit({"t": now, "kind": "reaction", "what": "wake_moot(already_awake)", "source": r.source})
            elif r.kind == "posture_fix":
                if person_kind() in FIXABLE:
                    rec = src.override("upright", r.payload["hold_s"], name="posture_fix")
                    emit({"t": now, "kind": "reaction", "what": "posture_fix", "source": r.source, "override": rec})
            elif r.kind == "feedback":
                p = r.payload
                ev_kind = p.get("event") or ""
                verdict = p["verdict"]
                arm = p.get("arm")
                sim_card = bool(p.get("sim_card"))
                if not sim_card:
                    hub.feedback(verdict, now, request_id=p.get("request_id"), response_ms=p.get("response_ms"))
                emit({"t": now, "kind": "feedback", **{k: v for k, v in p.items()}, "source": r.source})
                if ev_kind == "vent_suggest":
                    vent_rule.on_feedback(verdict, now)
                if verdict == "accept":
                    if engine == "hub":
                        acc = arm_chans(arm, "accept", ev_kind.replace("_suggest", "_accept"))
                    else:
                        d_open = open_decisions.get(ev_kind)
                        acc = (d_open.accept_channels if (d_open is not None and d_open.accept_channels)
                               else reaction.channels_for(ev_kind.replace("_suggest", "_accept")))
                    apply_channels(acc, now, {"minutes": p.get("minutes") or world.vent_minutes()})
                    pending.extend(reaction.deliver([c for c in acc if c != "hub_commands"], now + 1.0,
                                                    person_kind=person_kind(), focus=in_focus(), origin=ev_kind + "/accept"))
                    for cmd in held_commands.pop(ev_kind, []):
                        applied = world.apply_command(cmd, now)
                        emit({"t": now, "kind": "command", "held_until_accept": True,
                              **{k: cmd.get(k) for k in ("target_id", "operation", "value", "gate", "command_id")},
                              "channels": applied["channels"], "devices": world.snapshot()})
                    if ev_kind == "bedtime_suggest" or arm == "sleep_suggest":
                        rec = src.override("away", max(60.0, src.remaining_in_segment() + 1.0), name="bedtime", typing=False, resume=False)
                        emit({"t": now, "kind": "reaction", "what": "goes_to_bed", "source": ev_kind, "override": rec})
                    elif ev_kind == "break_suggest" or (arm or "").startswith("break_"):
                        maybe_go_rest(now, ev_kind)
                    policy_outcome(ev_kind, "accept", now)
                elif verdict == "reject":
                    if ev_kind in held_commands:
                        held_commands.pop(ev_kind, None)
                        hub.feedback("reject", now)            # hub 는 자동 실행했다고 알고 있으므로 되돌리기로 알린다
                    policy_outcome(ev_kind, "undo" if p.get("why") == "undo" else "reject", now)
                    if p.get("why") == "undo":
                        emit({"t": now, "kind": "undo", "event": ev_kind, "arm": arm})
            elif r.kind == "go_rest":
                rec = src.override("away", r.payload["seconds"], name="rest", typing=False)
                emit({"t": now, "kind": "reaction", "what": "go_rest", "source": r.source, "override": rec})

    # ---------------------------------------------------------------- hub tick 처리
    def sim_card(kind: str, now: float, *, why: str, extra: dict[str, Any] | None = None) -> None:
        """simple 엔진의 life_sim 계층 카드: 환기 권유·취침 권유. (hub 엔진은 personalize arm 이 같은 일을 한다.)"""
        ev = {"kind": kind, "state": last_tick["state"] if last_tick else None, "gate": "suggest", "cause": why,
              "request_id": None, "expires_in_s": card_expires, "c_fatigue": c_fat_now(), "sim_card": True, **(extra or {})}
        chans = reaction.channels_for(kind)
        apply_channels(chans, now)
        _, reactions = reaction.on_event(ev, now, person_kind=person_kind(), focus=in_focus())
        for rr in reactions:
            if rr.kind == "feedback":
                rr.payload["sim_card"] = True
                rr.payload["event"] = kind
                if extra and "minutes" in extra:
                    rr.payload["minutes"] = extra["minutes"]          # 수락하면 이 시간만큼 창문을 연다
        pending.extend(reactions)
        rec = {"t": now, "kind": "intervention", **ev, "channels": chans, "person_kind": person_kind(),
               "reaction": reaction.history[-1] if reaction.history and reaction.history[-1]["t"] == now else None,
               "policy_why": why, "devices": world.snapshot()}
        interventions.append(rec)
        emit(rec)
        if simple is not None:
            d = simple.decide(ev, now, chans, drowsy_episodes=counters["fatigue"], failed_interventions=counters["escalate"])
            schedule_policy_followups(d, kind, now)

    def handle_tick(tk: dict[str, Any], now: float) -> None:
        if tk["entered"] and tk["state"] == "FATIGUE":
            counters["fatigue"] += 1
            reaction.new_episode()
        if tk["entered"] and tk["state"] == "ESCALATE":
            counters["escalate"] += 1
        tick_rows.append(tk)
        w_ticks.write(tk)
        hold_env = False
        for ev in tk["events"]:
            arm = ev.get("arm")
            decision = None
            ev2 = dict(ev)
            if engine == "hub":
                chans = arm_chans(arm, ev["gate"], ev["kind"])
                if ev["gate"] == "suggest" and arm in arm_card_table:
                    ev2["kind"] = arm_card_table[arm]            # 카드 응답 확률 표(환기·취침·휴식)
            else:
                chans = reaction.channels_for(ev["kind"])
                if simple is not None and ev["kind"] in GROUP_OF:
                    decision = simple.decide(ev, now, chans, drowsy_episodes=counters["fatigue"], failed_interventions=counters["escalate"])
                    emit({"t": now, "kind": "policy", "event": ev["kind"], "allow": decision.allow, "gate": decision.gate, "why": decision.why,
                          "codes": decision.reason_codes, "channels": decision.channels})
                    if not decision.allow:
                        rec = {"t": now, "kind": "intervention", **ev, "channels": [], "person_kind": person_kind(), "reaction": None,
                               "policy_why": decision.why, "blocked_by_policy": True, "devices": world.snapshot()}
                        interventions.append(rec)
                        emit(rec)
                        if ev["kind"].startswith("env_"):
                            hold_env = True
                            held_commands.pop(ev["kind"], None)
                        continue
                    chans = decision.channels
                    if decision.gate == "suggest" and ev["gate"] == "auto":
                        ev2 = dict(ev, kind=ev["kind"].replace("_auto", "_suggest"), gate="suggest", request_id=None,
                                   expires_in_s=card_expires, policy_downgraded=True)
                        chans = reaction.channels_for(ev2["kind"])
                        if ev["kind"] == "env_auto":
                            hold_env = True
            chans = [c for c in chans if c != "hub_commands"]
            apply_channels(chans, now)
            _, reactions = reaction.on_event(ev2, now, person_kind=person_kind(), focus=in_focus(), channels=chans)
            for rr in reactions:
                if rr.kind == "feedback":
                    rr.payload.setdefault("event", ev["kind"])
                    rr.payload["arm"] = arm
                    if ev2.get("policy_downgraded"):
                        rr.payload["sim_card"] = True
            pending.extend(reactions)
            vent = world.ventilation_choice(world.env["co2_ppm"], world.env["pm25"]) if ev["kind"].startswith("env_") else None
            rec = {"t": now, "kind": "intervention", **ev2, "hub_kind": ev["kind"], "channels": chans, "person_kind": person_kind(),
                   "reaction": reaction.history[-1] if reaction.history and reaction.history[-1]["t"] == now else None,
                   "policy_why": (decision.why if decision else None) if engine == "simple" else ev.get("explanation"),
                   "vent_choice": vent, "weather": world.publish_weather(), "devices": world.snapshot()}
            interventions.append(rec)
            emit(rec)
            if engine == "simple":
                schedule_policy_followups(decision, ev["kind"], now)
                if vent is not None and vent["choice"] == "window":
                    counters["vent_cards"] += 1
                    sim_card("vent_suggest", now, why="co2_high+" + "+".join(vent["why"]), extra={"minutes": vent["minutes"]})
        # hub 가 이번 tick 에 발행한 기기 명령 (ACTION_ENV auto, 정책 arm 의 조명/선풍기, 카드 수락 뒤 dispatch)
        cmd_channels: list[str] = []
        live_cmds: list[dict[str, Any]] = []
        for cmd in tk["commands"]:
            if hold_env and cmd.get("gate") == "auto":
                held_commands.setdefault("env_auto", []).append(cmd)
                emit({"t": now, "kind": "command_held", **{k: cmd.get(k) for k in ("target_id", "operation", "value", "gate", "command_id")}})
                continue
            applied = world.apply_command(cmd, now)
            emit({"t": now, "kind": "command", **{k: cmd.get(k) for k in ("target_id", "operation", "value", "gate", "command_id")},
                  "channels": applied["channels"], "devices": world.snapshot()})
            if cmd.get("gate") != "undo":
                cmd_channels += applied["channels"]
                live_cmds.append(cmd)
        if live_cmds:
            gate = live_cmds[0].get("gate")
            rs = reaction.on_commands(live_cmds, cmd_channels, now, person_kind=person_kind(), focus=in_focus(), gate=gate)
            arm_now = (tk.get("personalize") or {}).get("arm")
            for rr in rs:
                if rr.kind == "feedback":
                    rr.payload["event"] = "env_auto" if gate == "auto" else "env_suggest"
                    rr.payload["arm"] = arm_now
            pending.extend(rs)
            if reaction.history and reaction.history[-1]["t"] == now:
                emit({"t": now, "kind": "command_reaction", **reaction.history[-1]})
        # simple 엔진: 취침 권유(정책 규칙, life_sim 카드). hub 엔진은 sleep_suggest arm 이 한다.
        if simple is not None and counters["bedtime_suggested"] == 0 and tk["state"] not in ("IDLE", "END"):
            due, why = simple.bedtime_due(now, tk["c_fatigue"], counters["fatigue"], counters["escalate"])
            if due:
                counters["bedtime_suggested"] += 1
                sim_card("bedtime_suggest", now, why=why)

    # ---------------------------------------------------------------- 메인 루프
    total = src.total_seconds()
    k = 0
    next_report = time.time() + 15.0
    prev_seg = -1
    prof = {"render": 0.0, "model": 0.0, "hub": 0.0}
    emit({"t": t0, "kind": "weather", **world.publish_weather()})
    while True:
        info = src.step()
        if info is None:
            break
        _truth["label"] = info.label
        t = t0 + info.t
        if info.segment_index != prev_seg:
            seg_t0[info.segment_index] = info.t
            prev_seg = info.segment_index
            emit({"t": t, "kind": "segment", "index": info.segment_index, "segment": info.kind, "override": info.override})
            world.new_segment()
            if info.env.get("co2_to") is not None and "co2_from" not in info.env:
                info.env["co2_from"] = world.env["co2_ppm"]
        _p0 = time.perf_counter()
        frames = src.render(info)
        _p1 = time.perf_counter()
        feats = rules.update(frames, t)
        v = hub.feed_tof(frames, feats, t)
        _p2 = time.perf_counter()
        prof["render"] += _p1 - _p0
        prof["model"] += _p2 - _p1
        if v is not None:
            last_verdict = v
        if w_replay is not None:
            pose = info.pose if _is_dc(info.pose) else None
            w_replay.write({"t": round(info.t, 2), "kind": info.kind, "label": info.label, "flags": info.flags,
                            "pose": None if pose is None else {k2: (float(v2) if isinstance(v2, (int, float, np.floating)) else v2)
                                                                for k2, v2 in _asdict(pose).items()},
                            "walker": None if info.walker is None else [float(info.walker[0]), float(info.walker[1])],
                            "dist": [np.asarray(frames[i][0]).reshape(-1).astype(int).tolist() for i in sorted(frames)],
                            "status": [np.asarray(frames[i][1]).reshape(-1).astype(int).tolist() for i in sorted(frames)],
                            "pred": (last_verdict or {}).get("posture"), "conf": (last_verdict or {}).get("posture_confidence"),
                            "drowsy_flag": bool(feats["drowsy"]), "nods": feats["nods_in_window"]})
        if k % int(fps) == 0:
            sec = int(round(info.t))
            seg_len = max(1e-6, src.current.seconds if not src.done else 1.0)
            truth_present = person_kind() != "away"                          # 정답 = 책상 재실
            where = where_now()
            world.step(t, present=where != "out", segment_env=info.env,      # CO₂·PM·열·수분은 방 안에 있는지로(센서 판정 아님)
                       seg_progress=min(1.0, (info.t - seg_t0.get(info.segment_index, info.t)) / seg_len),
                       person_kind=person_kind())
            env_meas = envs.step(t, world.env)                                 # SCD41·DHT22·BH1750 가 읽은 값
            mm = mmw.step(t, kind=person_kind(), typing=bool(info.typing), where=where)
            if mm is not None:
                hub.feed_mmwave(mm, t)
            present_dec, present_src = presence_decision(mm, bool(feats["present"]), desk_max_cm=float(ingest_cfg.get("desk_max_cm", 130)),
                                                         priority=str(ingest_cfg.get("presence_priority", "mmwave")))   # hub 와 같은 규칙
            keys = info.keys
            if fat.enabled:                                                    # 잠재 피로: 1 초 굴리고, 졸음·자세 무너짐·키 입력
                snap = world.snapshot()
                fat.step(t, kind=person_kind(), co2=world.env["co2_ppm"], lux=float(snap["lux"]), felt_c=float(snap["felt"]))
                for ov in fat.decide(t, kind=person_kind(), typing=bool(info.typing)):
                    rec = src.override(ov["kind"], ov["seconds"], transition_s=ov["transition_s"], name=ov["name"],
                                       typing=ov["typing"], spec=ov.get("spec"))
                    emit({"t": t, "kind": "reaction", "what": ov["name"], "source": "fatigue", "S": round(fat.S, 3), "override": rec})
                keys = fat.keys(info.keys)
            hub.feed_keystroke(info.typing, keys, t, rnd)
            if sec % int(dev.get("env_sensors", {}).get("uart_period_s", 5)) == 0 or not env_sent:
                hub.feed_env_uart(env_meas, t)                                 # ESP32 → UART 0x10 (0.2 Hz)
                env_sent = True
            if t >= next_weather:
                hub.feed_weather(dict(world.publish_weather(), fetched_at=int(t)), t)
                next_weather = t + WEATHER_PERIOD_S
            run_due(t)
            # 공기청정기 타이밍 규칙(FSM 밖, PM2.5 만) — purifier.py
            pcmd = purifier.observe(t, world.env["pm25"], present=present_dec,
                                    state=last_tick["state"] if last_tick else None)
            if pcmd is not None:
                applied = world.apply_command(pcmd, t)
                emit({"t": t, "kind": "command", **{k2: pcmd[k2] for k2 in ("target_id", "operation", "value", "gate", "why")},
                      "pm_avg": round(purifier.avg or 0.0, 1), "channels": applied["channels"], "devices": world.snapshot()})
            # CO₂ 환기 타이밍 규칙(FSM 밖, 기본 꺼짐) — ventilation.py
            if vent_rule.enabled:
                vreq = vent_rule.observe(t, env_meas.get("co2_ppm") if env_meas.get("co2_valid") else None, present=present_dec,
                                         posture=(last_verdict or {}).get("posture"),
                                         state=last_tick["state"] if last_tick else None,
                                         window_open=bool(world.snapshot().get("window")),
                                         vent=world.ventilation_choice(world.env["co2_ppm"], world.env["pm25"]))
                if vreq is not None:
                    counters["vent_cards"] += 1
                    sim_card("vent_suggest", t, why="+".join(vreq["why"]), extra={"minutes": vreq["minutes"], "rule": "co2_rule"})
            ticked = sec % int(period) == 0 and sec > 0
            if ticked:
                _p3 = time.perf_counter()
                tk = hub.tick(t)
                last_tick = tk
                handle_tick(tk, t)
                run_due(t)
                prof["hub"] += time.perf_counter() - _p3
            row = {
                "t": t, "sec": sec, "segment": info.kind, "label": info.label, "override": info.override,
                "transition": bool(info.flags & 1), "nodding": bool(info.flags & 2), "typing": info.typing,
                "pred": (last_verdict or {}).get("posture"), "conf": (last_verdict or {}).get("posture_confidence"),
                "raw": (last_verdict or {}).get("raw_posture"),
                "present": present_dec, "present_src": present_src, "truth_present": truth_present, "where": where,
                "tof_present": bool(feats["present"]), "mm_present": None if mm is None else mm["present"],
                "mm_level": None if mm is None else mm["motion_level"], "mm_drowsy": None if mm is None else mm["drowsy_state"],
                "motion": feats["motion_score"], "nods": feats["nods_in_window"],
                "nod_rate_hz": feats["nod_rate_hz"], "drowsy_flag": feats["drowsy"], "coverage": feats["coverage_ratio"],
                "rule_dbg": dict(rules.debug),
                "env": {k2: round(v2, 1) for k2, v2 in world.env.items()}, "devices": world.snapshot(),
                "env_meas": {k2: env_meas.get(k2) for k2 in ("co2_ppm", "temp_c", "humidity_pct", "lux")},
                "state": last_tick["state"] if last_tick else "IDLE",
                "c_fatigue": last_tick["c_fatigue"] if last_tick else 0.0, "c_focus": last_tick["c_focus"] if last_tick else 0.0,
            }
            if fat.enabled:
                absent_since = None if truth_present else (absent_since if absent_since is not None else t)
                row["fatigue_F"], row["fatigue_S"] = round(fat.F, 3), round(fat.S, 3)
                row["truth_class"] = fat.truth(kind=person_kind(), absent_s=None if absent_since is None else t - absent_since)
            if truth_log is not None and ticked:
                truth_log.write(json.dumps({"now": t, "truth": row.get("truth_class"), "F": row.get("fatigue_F"),
                                            "S": row.get("fatigue_S"), "segment": info.kind}) + "\n")
            second_rows.append(row)
            w_frames.write(row)
            if progress and time.time() >= next_report:
                print(f"[life_sim] {name}: {sec:.0f}/{total:.0f} s  state={row['state']} pred={row['pred']} "
                      f"({time.time() - started:.0f} s elapsed; render {prof['render']:.0f} s, model+ingest {prof['model']:.0f} s, "
                      f"hub {prof['hub']:.0f} s)", flush=True)
                next_report = time.time() + 15.0
        k += 1

    t_end = t0 + float(getattr(src, "t", total))
    report = hub.report(t_end)
    if hub.personalize is not None:
        hub.personalize.close_all(t_end)
    for w in (w_frames, w_ticks, w_events) + ((w_replay,) if w_replay is not None else ()):
        w.close()
    if truth_log is not None:
        for fh in (hub.hub.frame_log, hub.hub.state_log, truth_log):
            fh.close()
        hub.hub.frame_log = hub.hub.state_log = None
    with open(os.path.join(run_dir, "hub.log"), "w", encoding="utf-8") as fh:
        fh.write(hub.hub_log())
    policy_info["log"] = _parse_personalize_log(plog_path)
    if hub_policy is not None:
        policy_info["final"] = {"n_feedback": hub_policy.n_feedback, "policy_mode": hub_policy.policy_mode,
                                "auto_threshold": {a: round(hub_policy.auto_threshold(a), 3) for a in hub_policy.arms},
                                "arm_stats": {a: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in st.items()
                                                  if k in ("n", "accept", "reject", "timeout", "undo", "auto_kept", "accept_rate", "effect_mean")}
                                              for a, st in hub_policy.arm_stats().items()}}
    summary = summarize(scenario, second_rows, tick_rows, event_rows, interventions, report, src, model, world, reaction, simple,
                        policy_info, elapsed=time.time() - started, run_dir=run_dir)
    summary["profile_s"] = {k2: round(v2, 1) for k2, v2 in prof.items()}
    summary["env_sensors"] = envs.error_stats()
    summary["scene_room"] = world.room_drawn or None
    summary["fatigue"] = fat.summary(second_rows) if fat.enabled else None
    summary["stage2"] = _stage2_metrics(run_dir) if stage2 else None
    vcfg = dev.get("vent_control") or {}
    summary["vent_rule"] = vent_metrics(second_rows, vent_rule.log, [e for e in event_rows if e.get("kind") == "feedback"],
                                        on_ppm=float(vcfg.get("on_ppm", 1000))) if vent_rule.enabled else None
    try:
        from .plots import plot_timeline
        plot_timeline(second_rows, tick_rows, event_rows, os.path.join(run_dir, "timeline.png"), title=name,
                      thresholds=hub.fsm_cfg["thresholds"])
        summary["plot"] = os.path.join(run_dir, "timeline.png")
    except Exception as exc:  # noqa: BLE001 — matplotlib 이 없으면 PNG 만 건너뛴다
        summary["plot_error"] = f"{type(exc).__name__}: {exc}"
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(_jsonable(summary), fh, ensure_ascii=False, indent=1)
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(format_summary(summary))
    return summary


# ---------------------------------------------------------------- 요약

def _stage2_metrics(run_dir: str) -> dict[str, Any]:
    """state-hub.jsonl 의 sensor_summary.personalization(관찰 모드 판정)을 모아 상태·추론 시간·정답 일치를 낸다."""
    import statistics
    states = [json.loads(line) for line in open(os.path.join(run_dir, "state-hub.jsonl"), encoding="utf-8")]
    obs = [((s.get("data") or {}).get("sensor_summary") or {}).get("personalization") or {} for s in states]
    truth_path = os.path.join(run_dir, "truth-hub.jsonl")
    truth = ([json.loads(line).get("truth") for line in open(truth_path, encoding="utf-8")]
             if os.path.exists(truth_path) else [None] * len(obs))
    ms = [o["inference_ms"] for o in obs if o.get("inference_ms") is not None]
    pairs = [(t, o.get("label")) for t, o in zip(truth, obs) if t and o.get("label")]
    status: dict[str, int] = {}
    for o in obs:
        status[str(o.get("status"))] = status.get(str(o.get("status")), 0) + 1
    out: dict[str, Any] = {"ticks": len(obs), "status": status,
                           "inference_ms": {"p50": round(statistics.median(ms), 2), "max": round(max(ms), 2)} if ms else None,
                           "labels": {k: sum(1 for _, p in pairs if p == k) for k in ("focus", "fatigue", "rest", "idle")}}
    if pairs:
        out["truth_agreement"] = round(sum(1 for t, p in pairs if t == p) / len(pairs), 3)
        f1 = []
        for k in ("focus", "fatigue", "rest", "idle"):
            tp = sum(1 for t, p in pairs if t == k and p == k)
            n_t, n_p = sum(1 for t, _ in pairs if t == k), sum(1 for _, p in pairs if p == k)
            if n_t + n_p:
                f1.append(2 * tp / (n_t + n_p))
        out["truth_macro_f1"] = round(sum(f1) / len(f1), 3) if f1 else None
    return out


def summarize(scenario, rows, ticks, events, interventions, report, src, model, world, reaction, simple, policy_info, *,
              elapsed, run_dir) -> dict[str, Any]:
    t0 = scenario["t0"]
    labels = [r["label"] for r in rows]
    preds = [r["pred"] for r in rows]
    scored = [(l, p) for r, l, p in zip(rows, labels, preds) if p is not None and not r["transition"]]
    acc = sum(1 for l, p in scored if l == p) / max(1, len(scored))
    per_label: dict[str, dict[str, Any]] = {}
    for lab in sorted(set(labels)):
        tp = sum(1 for l, p in scored if l == lab and p == lab)
        n = sum(1 for l, _ in scored if l == lab)
        pp = sum(1 for _, p in scored if p == lab)
        per_label[lab] = {"n_sec": n, "recall": round(tp / n, 3) if n else None, "precision": round(tp / pp, 3) if pp else None}
    drowsy_secs = [r for r in rows if r["label"] == "drowsy"]
    flag_recall = sum(1 for r in drowsy_secs if r["drowsy_flag"]) / max(1, len(drowsy_secs))
    flag_fp = sum(1 for r in rows if r["drowsy_flag"] and r["segment"] != "drowsy") / max(1, len(rows))
    misplaced_fp = sum(1 for _, p in scored if p == "misplaced") / max(1, len(scored))
    first_drowsy = next((r["t"] for r in rows if r["label"] == "drowsy"), None)
    first_iv = next((iv["t"] for iv in interventions if not iv.get("blocked_by_policy")), None)
    wakes = [e for e in events if e.get("kind") == "reaction" and e.get("what") == "wake"]
    rests = [e for e in events if e.get("kind") == "reaction" and e.get("what") in ("go_rest", "goes_to_bed")]
    states_seq: list[str] = []
    for tk in ticks:
        if not states_seq or states_seq[-1] != tk["state"]:
            states_seq.append(tk["state"])
    durations: dict[str, float] = {}
    for tk in ticks:
        durations[tk["state"]] = durations.get(tk["state"], 0.0) + 10.0
    after_wake = None
    if wakes:
        tw = wakes[0]["t"]
        later = [tk for tk in ticks if tk["t"] > tw + 60]
        after_wake = {"states_after_60s": sorted({tk["state"] for tk in later[:12]}),
                      "c_fatigue_min_after": min((tk["c_fatigue"] for tk in later[:30]), default=None),
                      "returned_to_focus": any(tk["state"] in FOCUS_STATES for tk in later)}
    iv_table = [{"t_rel_s": round(iv["t"] - t0), "kind": iv["kind"], "hub_kind": iv.get("hub_kind", iv["kind"]), "gate": iv.get("gate"),
                 "cause": iv.get("cause"), "channels": iv.get("channels"), "person": iv.get("person_kind"),
                 "reaction": (iv.get("reaction") or {}).get("outcome"), "c_fatigue": iv.get("c_fatigue"),
                 "arm": iv.get("arm"), "asked_by_policy": iv.get("asked_by_policy"),
                 "policy_why": iv.get("policy_why"), "blocked": bool(iv.get("blocked_by_policy")),
                 "vent_choice": (iv.get("vent_choice") or {}).get("choice")} for iv in interventions]
    feedbacks = [e for e in events if e.get("kind") == "feedback"]
    commands = [e for e in events if e.get("kind") == "command"]
    undos = [e for e in events if e.get("kind") == "undo"]
    curve: dict[int, list[float]] = {}
    for tk in ticks:
        curve.setdefault(int((tk["t"] - t0) // 60), []).append(tk["c_fatigue"])
    fatigue_curve = [[m, round(sum(v) / len(v), 3)] for m, v in sorted(curve.items())]
    accepted = sum(1 for f in feedbacks if f.get("verdict") == "accept")
    rejected = sum(1 for f in feedbacks if f.get("verdict") == "reject" and f.get("why") != "undo")
    cards = sum(1 for iv in interventions if str(iv.get("gate")) == "suggest" and not iv.get("blocked_by_policy"))
    asked = sum(1 for iv in interventions if iv.get("asked_by_policy"))
    profile_name = policy_info.get("profile_id") or (simple.name if simple else None)
    return {
        "name": scenario["name"], "persona": scenario["persona"], "seed": scenario["seed"], "run_dir": run_dir,
        "policy_engine": policy_info.get("engine"), "policy_profile": profile_name, "simulated": True,
        "note": "시뮬레이션 결과 — 실측 증거가 아니다. 실측 10-19 녹화에서는 모든 모델의 drowsy 재현율이 0 이었다.",
        "duration_s": rows[-1]["sec"] + 1 if rows else 0, "wall_s": round(elapsed, 1),
        "scene": getattr(src, "meta", {}), "model": getattr(model, "info", {}),
        "fsm": {"config": scenario["hub"].get("fsm_config") or "fsm.yaml", "states_seq": states_seq, "durations_s": durations,
                "fatigue_episodes": sum(1 for tk in ticks if tk["entered"] and tk["state"] == "FATIGUE"),
                "escalations": sum(1 for tk in ticks if tk["entered"] and tk["state"] == "ESCALATE"),
                "max_c_fatigue": max((tk["c_fatigue"] for tk in ticks), default=0.0),
                "mean_c_fatigue": round(sum(tk["c_fatigue"] for tk in ticks) / max(1, len(ticks)), 4),
                "fatigue_curve_min": fatigue_curve},
        "posture": {"acc_sec": round(acc, 3), "per_label": per_label, "misplaced_fp": round(misplaced_fp, 4),
                    "drowsy_flag_recall_0x03": round(flag_recall, 3), "drowsy_flag_fp_0x03": round(flag_fp, 4)},
        "timing": {"first_drowsy_rel_s": None if first_drowsy is None else round(first_drowsy - t0),
                   "first_intervention_rel_s": None if first_iv is None else round(first_iv - t0),
                   "drowsy_to_intervention_s": None if first_drowsy is None or first_iv is None else round(first_iv - first_drowsy)},
        "interventions": iv_table, "n_interventions": sum(1 for iv in interventions if not iv.get("blocked_by_policy")),
        "n_blocked_by_policy": sum(1 for iv in interventions if iv.get("blocked_by_policy")),
        "cards": cards, "accepted": accepted, "rejected": rejected, "undos": len(undos), "asked_by_policy": asked,
        "accept_rate": round(accepted / cards, 3) if cards else None,
        "wakes": [{"t_rel_s": round(e["t"] - t0), "source": e.get("source")} for e in wakes],
        "wake_success": bool(wakes), "after_wake": after_wake,
        "rests": [{"t_rel_s": round(e["t"] - t0), "source": e.get("source"), "what": e.get("what")} for e in rests],
        "bedtime_suggested": any(iv["kind"] == "bedtime_suggest" or iv.get("arm") == "sleep_suggest" for iv in interventions),
        "vent_choices": [iv.get("vent_choice") for iv in interventions if iv.get("vent_choice")],
        "arms": sorted({iv.get("arm") for iv in interventions if iv.get("arm")}),
        "feedbacks": [{"t_rel_s": round(e["t"] - t0), "verdict": e.get("verdict"), "event": e.get("event"), "arm": e.get("arm"),
                       "why": e.get("why")} for e in feedbacks],
        "commands": [{"t_rel_s": round(e["t"] - t0), "target": e.get("target_id"), "op": e.get("operation"),
                      "value": e.get("value"), "gate": e.get("gate")} for e in commands],
        "overrides": list(getattr(src, "overrides_applied", [])),
        "presence": _presence_metrics(rows),
        "env": _env_metrics(rows, scenario["devices_resolved"]),
        "purifier": purifier_metrics(rows, commands, t0, (scenario["devices_resolved"] or {}).get("purifier_control") or {}),
        "co2": {"max": round(max((r["env"]["co2_ppm"] for r in rows), default=0)),
                "over1000_min": round(sum(1 for r in rows if r["env"]["co2_ppm"] >= 1000) / 60, 1),
                "over1400_min": round(sum(1 for r in rows if r["env"]["co2_ppm"] >= 1400) / 60, 1),
                "window_min": round(sum(1 for r in rows if r["devices"].get("window")) / 60, 1)},
        "devices_final": world.snapshot(), "reaction_log": reaction.history,
        "policy": simple.snapshot() if simple else None, "policy_log": simple.log if simple else [],
        "personalize": policy_info,
        "hub_report": {k: report.get(k) for k in ("duration_s", "focus_time_s", "fatigue_episodes", "interventions", "metrics",
                                                    "esm_labels") if k in report},
    }


def _env_metrics(rows: list[dict[str, Any]], dev: dict[str, Any]) -> dict[str, Any]:
    """재실 중 쾌적 범위 밖 시간(hub ingest.yaml 의 쾌적 범위와 같은 경계를 쓴다) + 참값 범위."""
    try:
        from deskmate_hub.ingest import load_ingest_config
        ec = load_ingest_config()["environment"]
    except Exception:  # noqa: BLE001
        ec = {"temp_comfort_min": 20.0, "temp_comfort_max": 26.0, "humidity_comfort_min": 30.0, "humidity_comfort_max": 60.0,
              "lux_dim_low": 150.0, "lux_dim_high": 300.0}
    pres = [r for r in rows if r.get("truth_present")]
    def mins(pred):
        return round(sum(1 for r in pres if pred(r["env"])) / 60, 1)
    def rng(key):
        v = [r["env"][key] for r in rows]
        return [round(min(v), 1), round(max(v), 1)] if v else None
    return {"present_min": round(len(pres) / 60, 1),
            "temp_out_min": mins(lambda e: not (ec["temp_comfort_min"] <= e["temp_c"] <= ec["temp_comfort_max"])),
            "rh_out_min": mins(lambda e: not (ec["humidity_comfort_min"] <= e["humidity_pct"] <= ec["humidity_comfort_max"])),
            "dim_min": mins(lambda e: e["lux"] < float(ec.get("lux_dim_high", 300))),
            "co2_over1000_present_min": mins(lambda e: e["co2_ppm"] >= 1000),
            "range": {k: rng(k) for k in ("co2_ppm", "temp_c", "humidity_pct", "lux", "pm25")}}


def _presence_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """재실 판정 정확도(정답 = 사람이 실제로 앉아 있는지): mmWave · ToF(0x03) · 결정(mmWave 우선) · hub 식(OR)."""
    def score(get):
        n = len(rows)
        miss = sum(1 for r in rows if r["truth_present"] and not get(r))
        false = sum(1 for r in rows if not r["truth_present"] and get(r))
        return {"acc": round(1 - (miss + false) / max(1, n), 4), "miss_s": miss, "false_s": false}
    out = {"tof": score(lambda r: r["tof_present"]), "decision": score(lambda r: r["present"]),
           "hub_or": score(lambda r: bool(r["tof_present"]) or bool(r.get("mm_present")))}
    if any(r.get("mm_present") is not None for r in rows):
        out["mmwave"] = score(lambda r: bool(r.get("mm_present")))
    by_seg: dict[str, list[int]] = {}
    for r in rows:
        if r["truth_present"] and not r["tof_present"]:
            by_seg.setdefault(r["label"], [0])[0] += 1
    # 자리비움 구간(정답 부재 ≥ 60 s)마다 hub 가 IDLE 로 갔는지(몇 초 만에), 돌아온 뒤 몇 초 만에 다시 START 했는지
    eps, cur = [], None
    for i, r in enumerate(rows):
        if not r["truth_present"] and cur is None:
            cur = i
        elif r["truth_present"] and cur is not None:
            eps.append((cur, i))
            cur = None
    if cur is not None:
        eps.append((cur, len(rows)))
    out["away_episodes"] = []
    for a, b in eps:
        if b - a < 60 or a < 30:
            continue
        idle_at = next((k - a for k in range(a, b) if rows[k]["state"] == "IDLE"), None)
        back = None
        if b < len(rows) and idle_at is not None:
            back = next((k - b for k in range(b, min(len(rows), b + 900)) if rows[k]["state"] in ("START", "CONTEXT_DETECT")), None)
        out["away_episodes"].append({"t_rel_s": a, "away_s": b - a, "idle_after_s": idle_at, "restart_after_return_s": back,
                                     "state_at_end": rows[b - 1]["state"]})
    out["tof_miss_by_label_s"] = {k: v[0] for k, v in sorted(by_seg.items(), key=lambda kv: -kv[1][0])}
    out["drowsy_state_s"] = {}
    for r in rows:
        if r.get("mm_drowsy"):
            out["drowsy_state_s"][r["mm_drowsy"]] = out["drowsy_state_s"].get(r["mm_drowsy"], 0) + 1
    return out


def format_summary(s: dict[str, Any]) -> str:
    pz = s.get("personalize") or {}
    lines = [f"# {s['persona']} / {s['name']} — 시뮬레이션 요약", "",
             f"> {s['note']}", "",
             f"- 길이 {s['duration_s']} s (벽시계 {s['wall_s']} s), 시드 {s['seed']}, FSM 설정 {s['fsm']['config']}, "
             f"정책 {s['policy_engine']}/{s['policy_profile']}",
             f"- 장면: {json.dumps(s['scene'], ensure_ascii=False)}",
             f"- 모델: {json.dumps({k: v for k, v in s['model'].items() if k != 'labels'}, ensure_ascii=False)}",
             f"- FSM 경로: {' → '.join(s['fsm']['states_seq'])}",
             f"- 피로 에피소드 {s['fsm']['fatigue_episodes']}회, 격상 {s['fsm']['escalations']}회, 최대 C_fatigue {s['fsm']['max_c_fatigue']:.2f}, "
             f"평균 {s['fsm']['mean_c_fatigue']:.2f}",
             f"- 자세 정확도(초 단위, 전이 제외) {s['posture']['acc_sec']:.3f}, misplaced 오탐 {s['posture']['misplaced_fp']:.3%}, "
             f"0x03 drowsy 플래그 재현율 {s['posture']['drowsy_flag_recall_0x03']:.2f} / 오탐 {s['posture']['drowsy_flag_fp_0x03']:.2%}",
             f"- 첫 졸음(관측 가능) {s['timing']['first_drowsy_rel_s']} s → 첫 개입 {s['timing']['first_intervention_rel_s']} s "
             f"(지연 {s['timing']['drowsy_to_intervention_s']} s)",
             f"- 개입 {s['n_interventions']}회(정책 보류 {s['n_blocked_by_policy']}, 정책이 자동→제안 {s['asked_by_policy']}), 카드 {s['cards']}장 "
             f"수락 {s['accepted']} 거절 {s['rejected']} 되돌리기 {s['undos']}, 수락률 {s['accept_rate']}, arms {s['arms']}",
             f"- 깨어남 {len(s['wakes'])}회 {s['wakes']}, 휴식/취침 이탈 {len(s['rests'])}회 {s['rests']}, 취침 권유 {s['bedtime_suggested']}, "
             f"환기 선택 {[v['choice'] for v in s['vent_choices']]}",
             f"- 깬 뒤: {s['after_wake']}",
             f"- CO₂: 최대 {s['co2']['max']} ppm, ≥1000 {s['co2']['over1000_min']} 분, ≥1400 {s['co2']['over1400_min']} 분, 창문 열림 {s['co2']['window_min']} 분",
             (f"- CO₂ 환기 규칙: 카드 {s['vent_rule']['cards']}회(수락 {s['vent_rule']['accepted']}·거절 {s['vent_rule']['rejected']}), "
              f"재실 중 CO₂≥1000 {s['vent_rule']['co2_high_present_min']} 분, 재실 평균 {s['vent_rule']['co2_mean_present']} ppm, "
              f"카드별 {s['vent_rule']['card_log']}" if s.get('vent_rule') else "- CO₂ 환기 규칙: 꺼짐"),
             f"- 환경(참값 범위) {s['env']['range']}; 재실 {s['env']['present_min']} 분 중 온도 쾌적 밖 {s['env']['temp_out_min']} 분, "
             f"습도 밖 {s['env']['rh_out_min']} 분, 어두움(<300 lx) {s['env']['dim_min']} 분, CO₂≥1000 {s['env']['co2_over1000_present_min']} 분",
             f"- 환경 센서 오차(측정−참): {json.dumps(s['env_sensors'], ensure_ascii=False)}",
             f"- 재실 판정(mmWave 우선): " + ", ".join(f"{k} 정확도 {v['acc']} (놓침 {v['miss_s']} s·헛재실 {v['false_s']} s)"
                                                      for k, v in s['presence'].items() if isinstance(v, dict) and 'acc' in v)
             + f"; ToF 가 놓친 자세 {s['presence']['tof_miss_by_label_s']}; mmWave drowsy_state {s['presence']['drowsy_state_s']}",
             f"- 공기청정기: PM 최대 {s['purifier']['pm_max']} / 평균 {s['purifier']['pm_mean']}, 나쁨(≥on_pm) {s['purifier']['bad_min']} 분"
             f"(재실 중 {s['purifier']['bad_present_min']} 분), 켜짐 {s['purifier']['on_min']} 분, 명령 {s['purifier']['commands']}회, "
             f"켜기 지연 {s['purifier']['on_latency_s']} s, 몰입 중 강풍 {s['purifier']['loud_in_focus_min']} 분, 단계별 분 {s['purifier']['level_min']}", "",
             "| 라벨 | 초 | 재현율 | 정밀도 |", "|---|---|---|---|"]
    for lab, v in s["posture"]["per_label"].items():
        lines.append(f"| {lab} | {v['n_sec']} | {v['recall']} | {v['precision']} |")
    lines += ["", "| t(s) | 개입 | 게이트 | 원인 | arm | 채널 | 사람 | 반응 | C_fat | 왜(정책) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for iv in s["interventions"]:
        lines.append(f"| {iv['t_rel_s']} | {iv['kind']}{' (보류)' if iv['blocked'] else ''}{' (정책이 물음)' if iv.get('asked_by_policy') else ''} | "
                     f"{iv['gate']} | {iv['cause']} | {iv.get('arm') or ''} | {','.join(iv['channels'] or [])} | {iv['person']} | "
                     f"{iv['reaction']} | {iv['c_fatigue']} | {iv['policy_why'] or ''} |")
    if s["commands"]:
        lines += ["", "기기 명령: " + "; ".join(f"{c['t_rel_s']}s {c['target']}.{c['op']}={c['value']}({c['gate']})" for c in s["commands"])]
    if pz.get("log"):
        lg = pz["log"]
        lines += ["", f"personalize 로그: 에피소드 {lg['episodes']}, arm {json.dumps(lg['by_arm'], ensure_ascii=False)}, "
                      f"응답 {json.dumps(lg['verdicts'], ensure_ascii=False)}, 게이트 {json.dumps(lg['gates'], ensure_ascii=False)}, "
                      f"제외 {json.dumps(lg['excluded'], ensure_ascii=False)}, 평균 보상 {lg['mean_reward']}"]
        if pz.get("final"):
            lines.append(f"정책 최종: {json.dumps({k: v for k, v in pz['final'].items() if k != 'arm_stats'}, ensure_ascii=False)}")
    if s["policy"]:
        lines += ["", f"간이 정책 상태: {json.dumps(s['policy'], ensure_ascii=False)}"]
    lines += ["", f"상태 체류(s): {json.dumps(s['fsm']['durations_s'], ensure_ascii=False)}", ""]
    return "\n".join(lines)
