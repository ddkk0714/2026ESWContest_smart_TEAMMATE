"""사람 반응 모델 — 개입(채널 묶음)이 오면 깨어남·자세 복귀·수락·무응답·거절·되돌리기를 확률과 지연으로 뽑는다.

입력은 hub_bridge.tick() 의 events(posture_auto / posture_suggest / env_auto / env_suggest / break_* / rest_forced /
guard_*)와 hub 가 낸 기기 명령. 출력은 시각이 붙은 `Reaction` 목록이고 하네스가 그 시각에 실행한다.
  wake         졸던 사람이 깬다 → person.override("upright", awake_s)
  posture_fix  턱괴기·거북목·숙임을 고친다 → person.override("upright", hold_s)
  feedback     화면 카드 응답(accept/reject) 또는 자동 실행 되돌리기(request_id 없는 reject)
  go_rest      휴식을 받아들여 자리를 비운다 → person.override("away", rest_s)
확률은 config/reaction.yaml(기본) < personas.<이름> < 각본 reaction: 순으로 덮인다. 모든 수치는 설정값이지 측정값이 아니다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

SLEEPY_KINDS = {"drowsy", "face_down"}
BAD_POSTURE_KINDS = {"chin_rest", "turtle", "lean_forward", "recline", "elbows", "face_down"}


@dataclass
class Reaction:
    due: float
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""


class ReactionModel:
    def __init__(self, cfg: dict[str, Any], channels_cfg: dict[str, list[str]], *, seed: int = 0):
        self.c = cfg
        self.channels = channels_cfg
        self.rnd = random.Random(seed)
        self.history: list[dict[str, Any]] = []
        self.wakes = 0
        self.escalation = 0          # 같은 졸음 에피소드 안에서 몇 번째 개입인지

    # ---------------------------------------------------------------- 도우미
    def _u(self, rng_spec, default=(0.0, 0.0)) -> float:
        lo, hi = (rng_spec if isinstance(rng_spec, (list, tuple)) else default)
        return self.rnd.uniform(float(lo), float(hi))

    def _p(self, table: str, key: str, default: float = 0.0) -> float:
        return float((self.c.get(table) or {}).get(key, default))

    def _combined(self, table: str, channels: list[str]) -> float:
        miss = 1.0
        for ch in channels:
            miss *= 1.0 - min(1.0, max(0.0, self._p(table, ch)))
        return 1.0 - miss

    def channels_for(self, event_kind: str) -> list[str]:
        return list(self.channels.get(event_kind) or [])

    # ---------------------------------------------------------------- 이벤트 → 반응
    def on_event(self, ev: dict[str, Any], now: float, *, person_kind: str, focus: bool,
                 channels: list[str] | None = None) -> tuple[list[str], list[Reaction]]:
        """hub 이벤트 하나 → (전달 채널, 예약된 반응). 카드(suggest)는 응답을, 자동(auto)/강제휴식은 즉시 채널을 낸다.
        channels 를 주면(정책이 고른 묶음) 기본 매핑 대신 그것이 사람에게 닿는다."""
        kind = ev["kind"]
        chans = list(channels) if channels is not None else self.channels_for(kind)
        out: list[Reaction] = []
        if kind.endswith("_suggest"):
            out += self._card(ev, now, person_kind=person_kind, focus=focus)
            return chans, out
        if kind.endswith("_none"):
            self._note(now, kind, "skipped(gate none)", [])
            return [], out
        out += self._deliver(chans, now, ev, person_kind=person_kind, focus=focus, origin=kind)
        if kind in ("break_auto", "rest_forced"):
            p = self._p("rest", "go_away_p", 0.0)
            if self.rnd.random() < p:
                out.append(Reaction(now + self._u(self.c.get("rest", {}).get("delay_s"), (20.0, 90.0)), "go_rest",
                                    {"seconds": self._u(self.c.get("rest", {}).get("away_s"), (180.0, 420.0))}, source=kind))
        return chans, out

    def on_commands(self, cmds: list[dict[str, Any]], channels: list[str], now: float, *, person_kind: str,
                    focus: bool, gate: str | None) -> list[Reaction]:
        """hub 가 실행한 기기 명령(ACTION_ENV auto, 또는 카드 수락 뒤) → 깨어남·되돌리기."""
        out = self._deliver(channels, now, {"kind": "env_commands", "gate": gate}, person_kind=person_kind, focus=focus,
                            origin="env_commands")
        if gate == "auto":
            p = self._p("undo", "p", 0.0)
            for ch in channels:
                p += self._p("noise_reject", ch, 0.0)
            if focus:
                p += self._p("undo", "focus_disturb_add", 0.0)
            if self.rnd.random() < min(1.0, p):
                out.append(Reaction(now + self._u((self.c.get("undo") or {}).get("delay_s"), (5.0, 50.0)), "feedback",
                                    {"verdict": "reject", "request_id": None, "why": "undo"}, source="env_auto"))
        return out

    def _card(self, ev: dict[str, Any], now: float, *, person_kind: str, focus: bool) -> list[Reaction]:
        table = (self.c.get("card") or {}).get(ev["kind"]) or {"accept": 0.5, "reject": 0.2, "none": 0.3}
        acc, rej = float(table.get("accept", 0.0)), float(table.get("reject", 0.0))
        if focus:
            shift = self._p("focus", "card_reject_add", 0.0)
            rej, acc = rej + shift, max(0.0, acc - shift)
        if person_kind in SLEEPY_KINDS:
            # 졸고 있으면 카드를 보고 답할 확률 자체가 준다(화면을 안 본다)
            see = self._p("card_attention", "sleepy", 0.5)
            acc, rej = acc * see, rej * see
        r = self.rnd.random()
        verdict = "accept" if r < acc else "reject" if r < acc + rej else "none"
        delay = self._u(self.c.get("card_delay_s"), (5.0, 60.0))
        expires = float(ev.get("expires_in_s") or 180.0)
        out: list[Reaction] = []
        if verdict != "none" and delay < expires:
            out.append(Reaction(now + delay, "feedback", {"verdict": verdict, "request_id": ev.get("request_id"),
                                                          "response_ms": int(delay * 1000), "event": ev["kind"]}, source=ev["kind"]))
            # 수락 뒤 실제 전달 채널(화면 안내·기기 실행)은 하네스가 정책 채널로 deliver() 한다
        self._note(now, ev["kind"], verdict, self.channels_for(ev["kind"]), delay=delay)
        return out

    def _deliver(self, channels: list[str], now: float, ev: dict[str, Any], *, person_kind: str, focus: bool, origin: str) -> list[Reaction]:
        out: list[Reaction] = []
        if not channels:
            return out
        if person_kind in SLEEPY_KINDS:
            p = self._combined("wake", channels)
            p = min(1.0, p * (1.0 + self._p("escalation", "wake_gain", 0.0) * self.escalation))
            self.escalation += 1
            woke = self.rnd.random() < p
            self._note(now, origin, "wake" if woke else "stays_asleep", channels, p=p)
            if woke:
                out.append(Reaction(now + self._u(self.c.get("wake_delay_s"), (3.0, 25.0)), "wake",
                                    {"awake_s": self._u(self.c.get("awake_s"), (300.0, 900.0)),
                                     "transition_s": self._u(self.c.get("wake_transition_s"), (0.6, 1.8)), "channels": channels},
                                    source=origin))
        elif person_kind in BAD_POSTURE_KINDS:
            p = self._combined("posture_fix", channels)
            fixed = self.rnd.random() < p
            self._note(now, origin, "posture_fix" if fixed else "ignores", channels, p=p)
            if fixed:
                out.append(Reaction(now + self._u(self.c.get("posture_fix_delay_s"), (3.0, 30.0)), "posture_fix",
                                    {"hold_s": self._u(self.c.get("posture_hold_s"), (240.0, 900.0)), "channels": channels},
                                    source=origin))
        else:
            self._note(now, origin, "noted", channels)
        return out

    def deliver(self, channels: list[str], now: float, *, person_kind: str, focus: bool, origin: str) -> list[Reaction]:
        """수락된 카드의 채널 묶음이 사람에게 닿는다(깨어남·자세 복귀)."""
        return self._deliver(channels, now, {"kind": origin}, person_kind=person_kind, focus=focus, origin=origin)

    def new_episode(self) -> None:
        self.escalation = 0

    def _note(self, now: float, origin: str, outcome: str, channels: list[str], **extra) -> None:
        self.history.append({"t": now, "origin": origin, "outcome": outcome, "channels": list(channels),
                             **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in extra.items()}})
