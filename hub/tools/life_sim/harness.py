"""폐루프 본체 — 각본 → 사람 → 두 센서 → 자세·졸음 모델 → hub(10 s tick) → (간이 개인화 정책) → 개입 → 기기·사람 반응 → 각본 변경.

    res = run_scenario(load_scenario(path), out_dir="runs/lifesim", model_path="models/opt_l8v5_c256_best.pt")

출력(out_dir/<persona>_<name>/)
    frames.jsonl        1 초마다: 정답 라벨·구간·모델 판정·0x03 특징·환경·기기·마지막 FSM 상태
    ticks.jsonl         hub tick(10 s)마다: 상태·게이트·원인·C_fatigue·C_focus·actions·요청·명령
    events.jsonl        개입(채널 포함)·정책 결정·사람 반응·피드백·기기 명령·각본 전환
    summary.json / .md  지표 표
    hub.log             LiveHub 의 tick 로그
    esm.jsonl           hub 가 남긴 ESM 라벨

정책 계층(policy.py)은 hub 밖에 있다: hub 의 게이트를 **낮추기만** 하고(자동→제안), 올리지 않는다. hub 자신은 그 사실을
모른다(engine 미수정) — 실제 제품에서 같은 효과를 내려면 게이트 입력 전에 정책이 끼어야 한다(보고서 "바꿔야 할 것").
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import numpy as np

from .devices import DeviceWorld
from .hub_bridge import HubBridge
from .policy import GROUP_OF, SimplePolicy, synth_profile
from .posture_model import OraclePosture, RuleFeatures
from .reaction import Reaction, ReactionModel
from .scenario import fsm_config_path

FOCUS_STATES = {"FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC"}
SLEEPY = ("drowsy", "face_down")
FIXABLE = ("chin_rest", "turtle", "lean_forward", "recline", "elbows")


class _TofsimSource:
    """ScriptedPerson + Scene 을 하네스가 쓰는 한 인터페이스로."""

    def __init__(self, scenario: dict[str, Any], *, backend: str, tofsim_root: str | None):
        from . import tofsim_bridge
        from .person import ScriptedPerson
        from .scene import build_scene
        if tofsim_bridge.load(tofsim_root) is None:
            raise RuntimeError("tofsim 을 찾지 못했다 (TOFSIM_ROOT 또는 --tofsim)")
        self.scene = build_scene(int(scenario["seed"]), backend=backend, placement=scenario["placement"],
                                 clutter=int(scenario["clutter"]), scene=scenario["scene"],
                                 require_ok=bool(scenario["require_ok_placement"]), l8_params=scenario.get("l8_params"))
        self.person = ScriptedPerson(scenario["segments"], seed=int(scenario["seed"]) + 1, fps=float(scenario["fps"]))
        self.person.bind(self.scene.cfg)
        self.meta = dict(self.scene.meta)

    def __getattr__(self, name):           # step/override/current/done/total_seconds/overrides_applied/t
        return getattr(self.person, name)

    def render(self, info):
        return self.scene.render(info.pose, info.walker)


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


def _make_policy(scenario: dict[str, Any], seed: int) -> SimplePolicy | None:
    pcfg = scenario.get("policy_resolved") or {}
    mode = str((scenario.get("policy") or {}).get("profile", "none"))
    if not pcfg.get("enabled") or mode == "none":
        return None
    if mode in ("synth", "A"):
        return SimplePolicy(pcfg, profile=synth_profile(scenario["persona"], seed=seed), seed=seed, name=f"A_synth30d_{scenario['persona']}")
    return SimplePolicy(pcfg, profile=None, seed=seed, name="B_global_default")


def run_scenario(scenario: dict[str, Any], *, out_dir: str, model_path: str | None = None, posture: str = "gru",
                 source: Any = None, backend: str | None = None, tofsim_root: str | None = None,
                 progress: bool = True, label: str | None = None) -> dict[str, Any]:
    """각본 하나를 끝까지 돌린다. posture: gru(torch 체크포인트) | oracle(정답 라벨). source: 사람 소스를 직접 줄 때(시험)."""
    name = label or f"{scenario['persona']}_{scenario['name']}"
    run_dir = os.path.join(out_dir, name)
    os.makedirs(run_dir, exist_ok=True)
    fps = float(scenario["fps"])
    t0 = float(scenario["t0"])
    seed = int(scenario["seed"])
    rnd = random.Random(seed + 7)
    started = time.time()

    src = source if source is not None else _TofsimSource(scenario, backend=backend or scenario["backend"], tofsim_root=tofsim_root)
    rules = RuleFeatures((scenario["devices_resolved"].get("rule_features") or {}), fps=fps)
    _truth: dict[str, str] = {}
    if posture == "oracle" or (source is not None and model_path is None):
        model = OraclePosture(lambda: getattr(src, "last_label", None) or _truth.get("label", "upright"), seed=seed)
    else:
        from .posture_model import GruPosture
        mcfg = scenario["devices_resolved"].get("posture_model") or {}
        model = GruPosture(model_path, vote_frames=int(mcfg.get("vote_frames", 10)), warmup_frames=int(mcfg.get("warmup_frames", 45)),
                           reset_on_away_s=float(mcfg.get("reset_on_away_s", 60)), reset_every_s=mcfg.get("reset_every_s", 900))
    hub = HubBridge(posture_model=model, fsm_config=fsm_config_path(scenario["hub"].get("fsm_config")),
                    esm_log_path=os.path.join(run_dir, "esm.jsonl"), t0=t0)
    period = float(scenario["hub"].get("tick_sec") or hub.period)
    world = DeviceWorld(scenario["devices_resolved"], scenario["env"], scenario["weather"], t0)
    channels_cfg = scenario["devices_resolved"].get("channels") or {}
    reaction = ReactionModel(scenario["reaction_resolved"], channels_cfg, seed=seed + 3)
    policy = _make_policy(scenario, seed)
    card_expires = float(hub.fsm_cfg["timers"].get("focus_break_poll_sec", 180))

    w_frames, w_ticks, w_events = (_Writer(os.path.join(run_dir, f)) for f in ("frames.jsonl", "ticks.jsonl", "events.jsonl"))
    pending: list[Reaction] = []
    last_tick: dict[str, Any] | None = None
    last_verdict: dict[str, Any] | None = None
    interventions: list[dict[str, Any]] = []
    second_rows: list[dict[str, Any]] = []
    tick_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    seg_t0: dict[int, float] = {}
    env_sent = False
    counters = {"fatigue": 0, "escalate": 0, "bedtime_suggested": 0, "vent_cards": 0}
    held_commands: dict[str, list[dict[str, Any]]] = {}       # 정책이 자동→제안으로 내린 env 명령 (카드 수락 시 적용)
    open_decisions: dict[str, Any] = {}                       # 카드 하나(이벤트 kind)당 열린 정책 결정
    cards_sim: list[dict[str, Any]] = []                      # life_sim 계층 카드(환기·취침) 기록

    def emit(ev: dict[str, Any]) -> None:
        event_rows.append(ev)
        w_events.write(ev)

    def person_kind() -> str:
        return src.current.kind if not src.done else "away"

    def in_focus() -> bool:
        return bool(last_tick and last_tick["state"] in FOCUS_STATES)

    def c_fat_now() -> float:
        return float(last_tick["c_fatigue"]) if last_tick else 0.0

    def apply_channels(chans: list[str], now: float, payload: dict[str, Any] | None = None) -> None:
        for ch in chans:
            if ch != "hub_commands":
                world.apply_channel(ch, now, payload)

    def schedule_policy_followups(d, ev_kind: str, now: float) -> None:
        if policy is None or d is None or not d.allow:
            return
        open_decisions[ev_kind] = d
        pending.append(Reaction(now + float((policy.c.get("reward") or {}).get("effect_window_s", 600)), "policy_effect",
                                {"event": ev_kind, "c_fat_start": c_fat_now()}, source="policy"))
        if d.gate == "suggest":
            pending.append(Reaction(now + card_expires + 1.0, "policy_expire", {"event": ev_kind}, source="policy"))

    def policy_outcome(ev_kind: str, outcome: str, now: float) -> None:
        if policy is None:
            return
        d = open_decisions.get(ev_kind)
        if d is not None and d.outcome is None:
            policy.outcome(d, outcome, now)
            emit({"t": now, "kind": "policy_outcome", "event": ev_kind, "outcome": outcome, "reward": d.reward,
                  "policy": policy.snapshot()})

    # ---------------------------------------------------------------- 예약된 반응 실행
    def run_due(now: float) -> None:
        nonlocal pending
        due = [r for r in pending if r.due <= now]
        pending = [r for r in pending if r.due > now]
        for r in due:
            if r.kind == "policy_effect":
                d = open_decisions.get(r.payload["event"])
                if policy is not None and d is not None and d.effect is None:
                    policy.effect(d, r.payload["c_fat_start"], c_fat_now(), now)
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
                    rec = src.override("upright", r.payload["awake_s"], transition_s=r.payload["transition_s"], name="wake",
                                       typing=bool(scenario["reaction_resolved"].get("wake_typing", True)))
                    reaction.wakes += 1
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
                sim_card = bool(p.get("sim_card"))
                if not sim_card:
                    hub.feedback(verdict, now, request_id=p.get("request_id"), response_ms=p.get("response_ms"))
                emit({"t": now, "kind": "feedback", **{k: v for k, v in p.items()}, "source": r.source})
                if verdict == "accept":
                    d_open = open_decisions.get(ev_kind)
                    acc = (d_open.accept_channels if (d_open is not None and d_open.accept_channels)
                           else reaction.channels_for(ev_kind.replace("_suggest", "_accept")))
                    apply_channels(acc, now, {"minutes": world.vent_minutes()})
                    pending.extend(reaction.deliver(acc, now + 1.0, person_kind=person_kind(), focus=in_focus(),
                                                    origin=ev_kind + "/accept"))
                    for cmd in held_commands.pop(ev_kind, []):
                        applied = world.apply_command(cmd, now)
                        emit({"t": now, "kind": "command", "held_until_accept": True,
                              **{k: cmd.get(k) for k in ("target_id", "operation", "value", "gate", "command_id")},
                              "channels": applied["channels"], "devices": world.snapshot()})
                    if ev_kind == "bedtime_suggest":
                        rec = src.override("away", max(60.0, src.remaining_in_segment() + 1.0), name="bedtime", typing=False, resume=False)
                        emit({"t": now, "kind": "reaction", "what": "goes_to_bed", "source": "bedtime_suggest", "override": rec})
                    policy_outcome(ev_kind, "accept", now)
                elif verdict == "reject":
                    if ev_kind in held_commands:
                        held_commands.pop(ev_kind, None)
                        hub.feedback("reject", now)            # hub 는 자동 실행했다고 알고 있으므로 되돌리기로 알린다
                    policy_outcome(ev_kind, "undo" if p.get("why") == "undo" else "reject", now)
                    if p.get("why") == "undo":
                        emit({"t": now, "kind": "undo", "event": ev_kind})
            elif r.kind == "go_rest":
                rec = src.override("away", r.payload["seconds"], name="rest", typing=False)
                emit({"t": now, "kind": "reaction", "what": "go_rest", "source": r.source, "override": rec})

    # ---------------------------------------------------------------- hub tick 처리
    def sim_card(kind: str, now: float, *, why: str, extra: dict[str, Any] | None = None) -> None:
        """hub 밖(life_sim 계층) 카드: 환기 권유·취침 권유. 사람 반응 모델이 hub 카드와 같은 확률 표로 답한다."""
        ev = {"kind": kind, "state": last_tick["state"] if last_tick else None, "gate": "suggest", "cause": why,
              "request_id": None, "expires_in_s": card_expires, "c_fatigue": c_fat_now(), "sim_card": True, **(extra or {})}
        chans = reaction.channels_for(kind)
        apply_channels(chans, now)
        _, reactions = reaction.on_event(ev, now, person_kind=person_kind(), focus=in_focus())
        for rr in reactions:
            if rr.kind == "feedback":
                rr.payload["sim_card"] = True
                rr.payload["event"] = kind
        pending.extend(reactions)
        rec = {"t": now, "kind": "intervention", **ev, "channels": chans, "person_kind": person_kind(),
               "reaction": reaction.history[-1] if reaction.history and reaction.history[-1]["t"] == now else None,
               "policy_why": why, "devices": world.snapshot()}
        interventions.append(rec)
        cards_sim.append(rec)
        emit(rec)
        if policy is not None:
            d = policy.decide(ev, now, chans, drowsy_episodes=counters["fatigue"], failed_interventions=counters["escalate"])
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
            chans = reaction.channels_for(ev["kind"])
            decision = None
            ev2 = dict(ev)
            if policy is not None and ev["kind"] in GROUP_OF:
                decision = policy.decide(ev, now, chans, drowsy_episodes=counters["fatigue"], failed_interventions=counters["escalate"])
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
                if decision.gate == "suggest" and ev["gate"] == "auto":       # 정책이 자동 → 제안으로 내림(hub 밖 카드)
                    ev2 = dict(ev, kind=ev["kind"].replace("_auto", "_suggest"), gate="suggest", request_id=None,
                               expires_in_s=card_expires, policy_downgraded=True)
                    chans = reaction.channels_for(ev2["kind"])
                    if ev["kind"] == "env_auto":
                        hold_env = True
            if ev2["kind"].startswith("env_") and ev2["gate"] == "auto":
                chans = [c for c in chans if c != "hub_commands"]
            apply_channels(chans, now)
            _, reactions = reaction.on_event(ev2, now, person_kind=person_kind(), focus=in_focus(), channels=chans)
            for rr in reactions:
                if rr.kind == "feedback":
                    rr.payload.setdefault("event", ev["kind"])
                    if ev2.get("policy_downgraded"):
                        rr.payload["sim_card"] = True
            pending.extend(reactions)
            vent = None
            if ev["kind"].startswith("env_"):
                vent = world.ventilation_choice(world.env["co2_ppm"], world.env["pm25"])
            rec = {"t": now, "kind": "intervention", **ev2, "hub_kind": ev["kind"], "channels": chans, "person_kind": person_kind(),
                   "reaction": reaction.history[-1] if reaction.history and reaction.history[-1]["t"] == now else None,
                   "policy_why": decision.why if decision else None, "vent_choice": vent, "weather": world.publish_weather(),
                   "devices": world.snapshot()}
            interventions.append(rec)
            emit(rec)
            schedule_policy_followups(decision, ev["kind"], now)
            if vent is not None and vent["choice"] == "window":
                counters["vent_cards"] += 1
                sim_card("vent_suggest", now, why="co2_high+" + "+".join(vent["why"]), extra={"minutes": vent["minutes"]})
            elif vent is not None and vent["choice"] == "air_purifier":
                world.apply_channel("purifier", now, {"level": 2})
                emit({"t": now, "kind": "command", "target_id": "air_purifier", "operation": "set_level", "value": 2, "gate": "sim_policy",
                      "channels": ["purifier"], "why": vent["why"], "devices": world.snapshot()})
        # hub 가 이번 tick 에 발행한 기기 명령 (ACTION_ENV auto, 또는 카드 수락 뒤 dispatch)
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
            for rr in rs:
                if rr.kind == "feedback":
                    rr.payload["event"] = "env_auto" if gate == "auto" else "env_suggest"
            pending.extend(rs)
            if reaction.history and reaction.history[-1]["t"] == now:
                emit({"t": now, "kind": "command_reaction", **reaction.history[-1]})
        # 취침 권유(정책 규칙, life_sim 카드)
        if policy is not None and counters["bedtime_suggested"] == 0 and tk["state"] not in ("IDLE", "END"):
            due, why = policy.bedtime_due(now, tk["c_fatigue"], counters["fatigue"], counters["escalate"])
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
        if k % int(fps) == 0:
            sec = int(round(info.t))
            seg_len = max(1e-6, src.current.seconds if not src.done else 1.0)
            world.step(t, present=bool(feats["present"]), segment_env=info.env,
                       seg_progress=min(1.0, (info.t - seg_t0.get(info.segment_index, info.t)) / seg_len))
            hub.feed_keystroke(info.typing, info.keys, t, rnd)
            if sec % 5 == 0 or not env_sent:
                hub.feed_env(world.env_sample(), t)
                env_sent = True
            run_due(t)
            if sec % int(period) == 0 and sec > 0:
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
                "present": feats["present"], "motion": feats["motion_score"], "nods": feats["nods_in_window"],
                "nod_rate_hz": feats["nod_rate_hz"], "drowsy_flag": feats["drowsy"], "coverage": feats["coverage_ratio"],
                "rule_dbg": dict(rules.debug),
                "env": {k2: round(v2, 1) for k2, v2 in world.env.items()}, "devices": world.snapshot(),
                "state": last_tick["state"] if last_tick else "IDLE",
                "c_fatigue": last_tick["c_fatigue"] if last_tick else 0.0, "c_focus": last_tick["c_focus"] if last_tick else 0.0,
            }
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
    for w in (w_frames, w_ticks, w_events):
        w.close()
    with open(os.path.join(run_dir, "hub.log"), "w", encoding="utf-8") as fh:
        fh.write(hub.hub_log())
    summary = summarize(scenario, second_rows, tick_rows, event_rows, interventions, report, src, model, world, reaction, policy,
                        elapsed=time.time() - started, run_dir=run_dir)
    summary["profile_s"] = {k2: round(v2, 1) for k2, v2 in prof.items()}
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(_jsonable(summary), fh, ensure_ascii=False, indent=1)
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(format_summary(summary))
    try:
        from .plots import plot_timeline
        plot_timeline(second_rows, tick_rows, event_rows, os.path.join(run_dir, "timeline.png"), title=name,
                      thresholds=hub.fsm_cfg["thresholds"])
        summary["plot"] = os.path.join(run_dir, "timeline.png")
    except Exception as exc:  # noqa: BLE001 — matplotlib 이 없으면 PNG 만 건너뛴다
        summary["plot_error"] = str(exc)
    return summary


# ---------------------------------------------------------------- 요약

def summarize(scenario, rows, ticks, events, interventions, report, src, model, world, reaction, policy, *, elapsed, run_dir) -> dict[str, Any]:
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
    return {
        "name": scenario["name"], "persona": scenario["persona"], "seed": scenario["seed"], "run_dir": run_dir,
        "policy_profile": policy.name if policy else None, "simulated": True,
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
        "cards": cards, "accepted": accepted, "rejected": rejected, "undos": len(undos),
        "accept_rate": round(accepted / cards, 3) if cards else None,
        "wakes": [{"t_rel_s": round(e["t"] - t0), "source": e.get("source")} for e in wakes],
        "wake_success": bool(wakes), "after_wake": after_wake,
        "rests": [{"t_rel_s": round(e["t"] - t0), "source": e.get("source"), "what": e.get("what")} for e in rests],
        "bedtime_suggested": any(iv["kind"] == "bedtime_suggest" for iv in interventions),
        "vent_choices": [iv.get("vent_choice") for iv in interventions if iv.get("vent_choice")],
        "feedbacks": [{"t_rel_s": round(e["t"] - t0), "verdict": e.get("verdict"), "event": e.get("event"), "why": e.get("why")}
                      for e in feedbacks],
        "commands": [{"t_rel_s": round(e["t"] - t0), "target": e.get("target_id"), "op": e.get("operation"),
                      "value": e.get("value"), "gate": e.get("gate")} for e in commands],
        "overrides": list(getattr(src, "overrides_applied", [])),
        "devices_final": world.snapshot(), "reaction_log": reaction.history,
        "policy": policy.snapshot() if policy else None, "policy_log": policy.log if policy else [],
        "hub_report": {k: report.get(k) for k in ("duration_s", "focus_time_s", "fatigue_episodes", "interventions", "metrics",
                                                    "esm_labels") if k in report},
    }


def format_summary(s: dict[str, Any]) -> str:
    lines = [f"# {s['persona']} / {s['name']} — 시뮬레이션 요약", "",
             f"> {s['note']}", "",
             f"- 길이 {s['duration_s']} s (벽시계 {s['wall_s']} s), 시드 {s['seed']}, FSM 설정 {s['fsm']['config']}, 정책 {s['policy_profile']}",
             f"- 장면: {json.dumps(s['scene'], ensure_ascii=False)}",
             f"- 모델: {json.dumps({k: v for k, v in s['model'].items() if k != 'labels'}, ensure_ascii=False)}",
             f"- FSM 경로: {' → '.join(s['fsm']['states_seq'])}",
             f"- 피로 에피소드 {s['fsm']['fatigue_episodes']}회, 격상 {s['fsm']['escalations']}회, 최대 C_fatigue {s['fsm']['max_c_fatigue']:.2f}, "
             f"평균 {s['fsm']['mean_c_fatigue']:.2f}",
             f"- 자세 정확도(초 단위, 전이 제외) {s['posture']['acc_sec']:.3f}, misplaced 오탐 {s['posture']['misplaced_fp']:.3%}, "
             f"0x03 drowsy 플래그 재현율 {s['posture']['drowsy_flag_recall_0x03']:.2f} / 오탐 {s['posture']['drowsy_flag_fp_0x03']:.2%}",
             f"- 첫 졸음(관측 가능) {s['timing']['first_drowsy_rel_s']} s → 첫 개입 {s['timing']['first_intervention_rel_s']} s "
             f"(지연 {s['timing']['drowsy_to_intervention_s']} s)",
             f"- 개입 {s['n_interventions']}회(정책 보류 {s['n_blocked_by_policy']}), 카드 {s['cards']}장 수락 {s['accepted']} 거절 {s['rejected']} "
             f"되돌리기 {s['undos']}, 수락률 {s['accept_rate']}",
             f"- 깨어남 {len(s['wakes'])}회 {s['wakes']}, 휴식/취침 이탈 {len(s['rests'])}회 {s['rests']}, 취침 권유 {s['bedtime_suggested']}, "
             f"환기 선택 {[v['choice'] for v in s['vent_choices']]}",
             f"- 깬 뒤: {s['after_wake']}", "",
             "| 라벨 | 초 | 재현율 | 정밀도 |", "|---|---|---|---|"]
    for lab, v in s["posture"]["per_label"].items():
        lines.append(f"| {lab} | {v['n_sec']} | {v['recall']} | {v['precision']} |")
    lines += ["", "| t(s) | 개입 | 게이트 | 원인 | 채널 | 사람 | 반응 | C_fat | 왜(정책) |", "|---|---|---|---|---|---|---|---|---|"]
    for iv in s["interventions"]:
        lines.append(f"| {iv['t_rel_s']} | {iv['kind']}{' (보류)' if iv['blocked'] else ''} | {iv['gate']} | {iv['cause']} | "
                     f"{','.join(iv['channels'] or [])} | {iv['person']} | {iv['reaction']} | {iv['c_fatigue']} | {iv['policy_why'] or ''} |")
    if s["commands"]:
        lines += ["", "기기 명령: " + "; ".join(f"{c['t_rel_s']}s {c['target']}.{c['op']}={c['value']}({c['gate']})" for c in s["commands"])]
    if s["policy"]:
        lines += ["", f"정책 상태: {json.dumps(s['policy'], ensure_ascii=False)}"]
    lines += ["", f"상태 체류(s): {json.dumps(s['fsm']['durations_s'], ensure_ascii=False)}", ""]
    return "\n".join(lines)
