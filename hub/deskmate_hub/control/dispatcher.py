"""제어 디스패처 — FSM 의 ACTION_ENV 를 실제(또는 모의) 기기 명령으로 잇는다.

계약: data-spec §10 (`control_command` / `control_result`), mqtt-topics `deskmate/control/cmd` · `deskmate/control/result`.

동작
- ACTION_ENV 진입 + 게이트 auto(≥0.75)  → 즉시 명령 발행 (가역 동작만). 사용자가 나중에 reject 하면 undo 발행.
- ACTION_ENV 진입 + 게이트 suggest      → 대기. `feedback/user` accept 면 발행, reject 면 skipped 로 종료.
- ACTION_ENV 진입 + 게이트 none         → skipped (로그만). FSM 은 다음 tick 에 MONITOR 로 간다.
- 결과(succeeded/failed) 가 오거나 result_timeout_sec 이 지나면 해당 에피소드는 완료 → frame.action_done=True.
- 같은 target/operation 은 cooldown_sec 안에 다시 보내지 않는다. 비가역 동작은 requires_confirmation=True 로만 만들고
  자동 실행하지 않는다.
표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..inference import GateMode

Publish = Callable[[dict[str, Any]], None]


@dataclass
class ControlCommand:
    command_id: str
    target_id: str
    operation: str
    value: Any
    origin_state: str
    cause: str | None
    gate: str
    requires_confirmation: bool
    expires_ts: float
    undo_value: Any = None
    status: str = "accepted"          # accepted | executing | succeeded | failed | timeout | cancelled | skipped
    sent_ts: float | None = None
    completed_ts: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id, "target_id": self.target_id, "operation": self.operation,
            "value": self.value, "origin_state": self.origin_state, "cause": self.cause, "gate": self.gate,
            "requires_confirmation": self.requires_confirmation, "expires_ts_ms": int(self.expires_ts * 1000),
        }

    @property
    def terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "timeout", "cancelled", "skipped")


@dataclass
class Episode:
    """ACTION_* 한 번의 개입. 명령 묶음과 그 상태."""
    state: str
    cause: str | None
    gate: GateMode
    started_ts: float
    commands: list[ControlCommand] = field(default_factory=list)
    awaiting_user: bool = False
    executed: bool = False
    outcome: str | None = None        # executed | skipped | rejected | undone

    @property
    def done(self) -> bool:
        if self.awaiting_user:
            return False
        return self.outcome is not None and all(c.terminal for c in self.commands)


class ControlDispatcher:
    def __init__(self, cfg: dict[str, Any], *, publish_cmd: Publish, on_log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.publish_cmd = publish_cmd
        self._log = on_log or (lambda m: None)
        self._clock = clock
        self.timeout = float(cfg.get("result_timeout_sec", 15))
        self.suggest_timeout = float(cfg.get("suggest_timeout_sec", 180))
        self.cooldown = float(cfg.get("cooldown_sec", 300))
        self.irreversible = set(cfg.get("irreversible_operations") or [])
        self.actions = cfg.get("actions") or {}
        self._last_sent: dict[tuple[str, str], float] = {}
        self.episode: Episode | None = None
        self.history: list[Episode] = []

    # ---------------------------------------------------------------- FSM 훅
    def on_enter_action(self, state: str, cause: str | None, gate: GateMode, now: float | None = None) -> Episode:
        """ACTION_ENV 진입. 게이트에 따라 즉시 실행 / 사용자 대기 / 건너뜀."""
        now = self._clock() if now is None else now
        ep = Episode(state=state, cause=cause, gate=gate, started_ts=now)
        ep.commands = self._plan(state, cause, gate, now)
        self.episode = ep
        if not ep.commands:
            ep.outcome = "skipped"
            self._log(f"[control] {state}/{cause}: 실행할 명령 없음(쿨다운·비가역·미정의) → skipped")
        elif gate is GateMode.AUTO:
            self._dispatch(ep, now)
        elif gate is GateMode.SUGGEST:
            ep.awaiting_user = True
            self._log(f"[control] {state}/{cause}: 제안 대기 ({len(ep.commands)}건)")
        else:
            for c in ep.commands:
                c.status = "skipped"
            ep.outcome = "skipped"
        return ep

    def on_feedback(self, verdict: str, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        ep = self.episode
        if ep is None:
            return
        if ep.awaiting_user:
            ep.awaiting_user = False
            if verdict == "accept":
                self._dispatch(ep, now)
            else:
                for c in ep.commands:
                    c.status = "skipped"
                ep.outcome = "rejected"
                self._log(f"[control] 사용자 거절 → {ep.state} 명령 취소")
        elif ep.executed and verdict == "reject" and ep.outcome == "executed":
            self._undo(ep, now)

    def on_result(self, payload: dict[str, Any], now: float | None = None) -> bool:
        """`deskmate/control/result` 수신. 아는 command_id 면 반영하고 True."""
        now = self._clock() if now is None else now
        cid = payload.get("command_id")
        for ep in ([self.episode] if self.episode else []) + self.history[-5:]:
            for c in ep.commands:
                if c.command_id == cid and not c.terminal:
                    status = str(payload.get("status", "")).lower()
                    c.status = status if status in ("executing", "succeeded", "failed", "cancelled") else "failed"
                    if c.terminal:
                        c.completed_ts = now
                    self._log(f"[control] {c.target_id}.{c.operation}={c.value!r} → {c.status}")
                    return True
        return False

    def tick(self, now: float | None = None) -> None:
        """타임아웃 처리. 매 프레임 호출."""
        now = self._clock() if now is None else now
        ep = self.episode
        if ep is None:
            return
        if ep.awaiting_user and now - ep.started_ts > self.suggest_timeout:
            # 제안에 응답이 없으면 만료 — 실행하지 않고 넘어간다(무응답도 하나의 신호로 기록)
            ep.awaiting_user = False
            for c in ep.commands:
                c.status = "skipped"
            ep.outcome = "expired"
            self._log(f"[control] 제안 무응답 {self.suggest_timeout:.0f}s → expired")
        for c in ep.commands:
            if not c.terminal and c.sent_ts is not None and now - c.sent_ts > self.timeout:
                c.status, c.completed_ts = "timeout", now
                self._log(f"[control] {c.target_id}.{c.operation} 결과 없음 → timeout")

    def action_done(self, now: float | None = None) -> bool:
        """현재 에피소드가 끝났는지(FSM frame.action_done 로 전달)."""
        self.tick(now)
        return self.episode is None or self.episode.done

    def close_episode(self) -> None:
        if self.episode is not None:
            self.history.append(self.episode)
            self.episode = None

    def summary(self) -> dict[str, Any]:
        ep = self.episode
        if ep is None:
            return {"active": False}
        return {"active": True, "state": ep.state, "cause": ep.cause, "gate": ep.gate.value, "outcome": ep.outcome,
                "awaiting_user": ep.awaiting_user,
                "commands": [{"target": c.target_id, "op": c.operation, "value": c.value, "status": c.status} for c in ep.commands]}

    # ---------------------------------------------------------------- 내부
    def _plan(self, state: str, cause: str | None, gate: GateMode, now: float) -> list[ControlCommand]:
        specs = self.actions.get(cause or "", []) if state == "ACTION_ENV" else []
        cmds: list[ControlCommand] = []
        for spec in specs:
            key = (spec["target_id"], spec["operation"])
            last = self._last_sent.get(key)
            if last is not None and now - last < self.cooldown:
                self._log(f"[control] {key[0]}.{key[1]} 쿨다운 중({now - last:.0f}s) → 생략")
                continue
            irreversible = spec["operation"] in self.irreversible
            if irreversible and gate is GateMode.AUTO:
                self._log(f"[control] {key[0]}.{key[1]} 비가역 → 자동 실행 금지, 생략")
                continue
            cmds.append(ControlCommand(
                command_id=uuid.uuid4().hex[:12], target_id=spec["target_id"], operation=spec["operation"],
                value=spec.get("value"), undo_value=spec.get("undo_value"), origin_state=state, cause=cause,
                gate=gate.value, requires_confirmation=irreversible, expires_ts=now + self.timeout,
            ))
        return cmds

    def _dispatch(self, ep: Episode, now: float) -> None:
        for c in ep.commands:
            c.sent_ts, c.status = now, "executing"
            self._last_sent[(c.target_id, c.operation)] = now
            self.publish_cmd(c.to_payload())
        ep.executed = True
        ep.outcome = "executed"
        self._log(f"[control] {ep.state}/{ep.cause}: {len(ep.commands)}건 발행 ({ep.gate.value})")

    def _undo(self, ep: Episode, now: float) -> None:
        for c in ep.commands:
            if c.undo_value is None or c.status not in ("succeeded", "executing"):
                continue
            undo = ControlCommand(
                command_id=uuid.uuid4().hex[:12], target_id=c.target_id, operation=c.operation, value=c.undo_value,
                origin_state=ep.state, cause=ep.cause, gate="undo", requires_confirmation=False,
                expires_ts=now + self.timeout, sent_ts=now, status="executing",
            )
            ep.commands.append(undo)
            self.publish_cmd(undo.to_payload())
        ep.outcome = "undone"
        self._log(f"[control] 사용자 거절 → 자동 실행 되돌림")


class MockPlugAdapter:
    """프로세스 안에서 즉시 성공 결과를 돌려주는 모의 플러그. 상태표를 유지해 UI·로그에서 볼 수 있다."""

    def __init__(self, on_result: Callable[[dict[str, Any]], None], *, delay_sec: float = 0.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.on_result = on_result
        self.delay = delay_sec
        self._clock = clock
        self.state: dict[str, Any] = {}
        self._pending: list[tuple[float, dict[str, Any]]] = []

    def handle_command(self, payload: dict[str, Any]) -> None:
        self.state[payload["target_id"]] = {payload["operation"]: payload["value"]}
        result = {"command_id": payload["command_id"], "status": "succeeded", "actual_value": payload["value"],
                  "completed_ts_ms": int(self._clock() * 1000)}
        if self.delay <= 0:
            self.on_result(result)
        else:
            self._pending.append((self._clock() + self.delay, result))

    def tick(self) -> None:
        now = self._clock()
        due = [r for t, r in self._pending if t <= now]
        self._pending = [(t, r) for t, r in self._pending if t > now]
        for r in due:
            self.on_result(r)
