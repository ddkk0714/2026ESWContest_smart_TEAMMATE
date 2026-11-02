"""제어 디스패처 — FSM 의 ACTION_ENV 를 실제(또는 모의) 기기 명령으로 잇는다.

계약: data-spec §10 (`control_command` / `control_result`), mqtt-topics `deskmate/control/cmd` · `deskmate/control/result`.

동작
- ACTION_ENV 진입 + 게이트 auto(≥0.75)  → 즉시 명령 발행 (가역 동작만). 사용자가 나중에 reject 하면 undo 발행.
- ACTION_ENV 진입 + 게이트 suggest      → 대기. `feedback/user` accept 면 발행, reject 면 skipped 로 종료.
- ACTION_ENV 진입 + 게이트 none         → skipped (로그만). FSM 은 다음 tick 에 MONITOR 로 간다.
- ACTION_POSTURE 도 control.yaml `actions.posture` 가 있으면 같은 규칙으로 명령을 낸다(2026-10-24, 졸음 → 무드등 깨우기).
  개인화 정책이 기기 arm 의 spec 을 준 경우(specs)는 그것이 우선이고, `actions.posture` 가 없으면 예전처럼 명령 없음.
  ACTION_BREAK 는 specs 가 있을 때만(기본 명령 없음).
- 결과(succeeded/failed) 가 오거나 result_timeout_sec 이 지나면 해당 에피소드는 완료 → frame.action_done=True.
- 같은 target/operation 은 cooldown_sec 안에 다시 보내지 않는다. 비가역 동작은 requires_confirmation=True 로만 만들고
  자동 실행하지 않는다.
- 밝기는 절대값이 아니라 지금 밝기 대비 % 다(2026-10-25): operation `adjust_brightness`, value {"percent": ±정수}. 지금 밝기는
  램프를 쓰는 Pi 5 앱만 알므로 hub 는 % 만 보내고 앱이 계산해 결과에 `previous`(바꾸기 전 원시 0~255)·`actual`(바꾼 뒤)을 준다.
  value 에 선택 키 `min_raw` 가 있으면 앱은 결과를 max(계산값, min_raw) 로 한다(깨우기 묶음만 — 꺼진 램프도 켜지게).
  되돌리기(spec `undo: previous`)는 그 결과의 previous 로 `set_brightness <previous>` 를 보낸다 — 결과가 없거나 previous 가 없으면
  되돌리기 생략(로그만). 그 밖(set_brightness·set_power·set_mode)은 지금처럼 undo_value.
표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..inference import GateMode
from .devices import same_value

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
    undo_mode: str | None = None      # "previous" = 결과의 previous 로 set_brightness (adjust_brightness 용). None = undo_value
    result: dict[str, Any] | None = None   # 마지막 control/result payload(previous·actual 등)
    status: str = "accepted"          # accepted | executing | succeeded | failed | timeout | cancelled | skipped
    context: dict[str, Any] = field(default_factory=dict)  # Internal planner data, never MQTT.
    sent_ts: float | None = None
    completed_ts: float | None = None
    error_code: str | None = None
    state_confirmed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id, "target_id": self.target_id, "operation": self.operation,
            "value": self.value, "origin_state": self.origin_state, "cause": self.cause, "gate": self.gate,
            "requires_confirmation": self.requires_confirmation, "expires_ts_ms": int(self.expires_ts * 1000),
            **({"undo_of_command_id": self.context["undo_of_command_id"]} if "undo_of_command_id" in self.context else {}),
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
    advice: list[dict[str, Any]] = field(default_factory=list)
    awaiting_user: bool = False
    executed: bool = False
    executed_ts: float | None = None
    outcome: str | None = None        # executed | skipped | rejected | undone

    @property
    def done(self) -> bool:
        if self.awaiting_user:
            return False
        return self.outcome is not None and all(c.terminal for c in self.commands)


class ControlDispatcher:
    def __init__(self, cfg: dict[str, Any], *, publish_cmd: Publish, on_log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time,
                 plan_commands: Callable | None = None,
                 plan_advice: Callable | None = None,
                 dispatch_guard: Callable | None = None,
                 on_dispatched: Callable | None = None) -> None:
        self.cfg = cfg
        self._plan_commands = plan_commands
        self._plan_advice = plan_advice
        self._dispatch_guard = dispatch_guard
        self._on_dispatched = on_dispatched
        self.publish_cmd = publish_cmd
        self._log = on_log or (lambda m: None)
        self._clock = clock
        self.timeout = float(cfg.get("result_timeout_sec", 15))
        self.suggest_timeout = float(cfg.get("suggest_timeout_sec", 180))
        self.undo_window = float(cfg.get("undo_window_sec", 60))
        self.cooldown = float(cfg.get("cooldown_sec", 300))
        self.irreversible = set(cfg.get("irreversible_operations") or [])
        self.actions = cfg.get("actions") or {}
        # control.yaml `actions.<cause>` 를 기본 명령으로 쓰는 상태. ENV 는 처음부터, POSTURE 는 2026-10-24 부터(졸음 → 무드등).
        # ACTION_BREAK 는 여기 없다 — 휴식은 기기가 아니라 사람이 하는 일이라 개인화 arm(specs)이 있을 때만 명령이 나간다.
        self.default_action_states = ("ACTION_ENV", "ACTION_POSTURE")
        self._last_sent: dict[tuple[str, str], float] = {}
        self.episode: Episode | None = None
        self.history: list[Episode] = []

    # ---------------------------------------------------------------- FSM 훅
    def has_default_actions(self, state: str, cause: str | None) -> bool:
        """이 상태·원인에 control.yaml `actions.<cause>` 기본 명령이 정의돼 있는지(live.py 가 에피소드를 열지 정할 때 본다)."""
        return state in self.default_action_states and bool(self.actions.get(cause or ""))

    def on_enter_action(self, state: str, cause: str | None, gate: GateMode, now: float | None = None,
                        *, specs: list[dict[str, Any]] | None = None) -> Episode:
        """ACTION_ENV·ACTION_POSTURE 진입. 게이트에 따라 즉시 실행 / 사용자 대기 / 건너뜀.

        specs 를 주면 control.yaml `actions[cause]` 대신 그 명령 spec 목록을 쓴다(개인화 정책이 고른 arm 의 기기 값).
        """
        now = self._clock() if now is None else now
        ep = Episode(state=state, cause=cause, gate=gate, started_ts=now)
        ep.commands = self._plan(state, cause, gate, now, specs=specs)
        if self._plan_advice is not None and state == "ACTION_ENV" and cause == "environment":
            ep.advice = [dict(item) for item in self._plan_advice()]
        self.episode = ep
        if not ep.commands and ep.advice and gate is GateMode.SUGGEST:
            ep.awaiting_user = True  # A manual ventilation suggestion needs the same feedback path.
        elif not ep.commands:
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

    def on_feedback(self, verdict: str, now: float | None = None, *, received_at: float | None = None) -> None:
        now = self._clock() if now is None else now
        ep = self.episode
        if ep is not None and ep.awaiting_user:
            if (now if received_at is None else received_at) - ep.started_ts >= self.suggest_timeout:
                self._expire(ep, "suggestion deadline reached")
                return
            if verdict == "timeout":
                # 화면의 카드가 만료됐다(expires_in_s) — 사람이 볼 수 없는 제안을 suggest_timeout 까지 붙들지 않는다.
                self._expire(ep, "화면 카드 만료(timeout)")
                return
            if verdict not in ("accept", "reject"):
                return
            ep.awaiting_user = False
            if verdict == "accept":
                self._dispatch(ep, now)
            elif verdict == "reject":
                for c in ep.commands:
                    c.status = "skipped"
                ep.outcome = "rejected"
                self._log(f"[control] 사용자 거절 → {ep.state} 명령 취소")
            return
        if verdict != "reject":
            return
        target = ep if ep is not None and ep.executed and ep.outcome == "executed" else None
        if target is None and self.history:
            target = self.history[-1]
        if (target is not None and target.executed and target.outcome == "executed"
                and target.executed_ts is not None and now - target.executed_ts <= self.undo_window):
            self._undo(target, now)
        else:
            self._log("[control] 되돌릴 실행이 없거나 되돌리기 창이 지남")

    def on_result(self, payload: dict[str, Any], now: float | None = None) -> bool:
        """`deskmate/control/result` 수신. 아는 command_id 면 반영하고 True."""
        now = self._clock() if now is None else now
        cid = payload.get("command_id")
        for ep in ([self.episode] if self.episode else []) + self.history[-5:]:
            for c in ep.commands:
                if c.command_id == cid and c.terminal:
                    # 이미 끝난 명령에 같은 command_id 결과가 다시 오면 상태는 그대로 두고 previous·actual 만 보충한다 —
                    # PC life_sim 의 모의 어댑터는 succeeded 를 먼저 보내고 previous·actual 을 한 번 더 보낸다(실기 앱은 한 번).
                    late = {k: payload[k] for k in ("previous", "actual") if k in payload}
                    if late:
                        c.result = dict(c.result or {}, **late)
                        self._log(f"[control] {c.target_id}.{c.operation} 늦은 결과로 previous/actual 보충 {late}")
                    continue
                if c.command_id == cid and not c.terminal and c.sent_ts is not None:
                    if now >= c.expires_ts:
                        c.status, c.completed_ts, c.error_code = "timeout", now, "result_expired"
                        return False
                    c.result = dict(payload)
                    status = str(payload.get("status", "")).lower()
                    error = payload.get("error_code")
                    c.error_code = error if isinstance(error, str) and len(error) <= 64 and all(
                        char.isalnum() or char == "_" for char in error) else None
                    actual_matches = same_value(payload.get("actual_value"), c.value)
                    if c.operation == "adjust_brightness" and isinstance(c.value, dict):
                        av = payload.get("actual_value")
                        actual_matches = (type(av) in (int, float) and 0 <= av <= 255
                                          and ("actual" not in payload or same_value(av, payload["actual"])))
                    # Pico fan countdown may advance between write/read, matching
                    # FanState.matches' existing two-second comparison contract.
                    if (c.target_id == "vent_fan" and c.operation == "set_timer"
                            and payload.get("confirmation") == "state_readback"
                            and type(payload.get("actual_value")) is int and type(c.value) is int):
                        actual_matches = (0 <= payload["actual_value"] <= 65535
                                          and 0 <= c.value <= 65535
                                          and abs(payload["actual_value"] - c.value) <= 2)
                    ble_unconfirmed = (c.target_id in ("desk_lamp", "vent_fan")
                                       and payload.get("confirmation") != "state_readback")
                    if status == "succeeded" and (payload.get("confirmation") == "transport_only"
                            or (ble_unconfirmed and c.context.get("policy_rule"))):
                        status, c.error_code = "executing", "device_state_unconfirmed"
                    elif status == "succeeded" and "actual_value" in payload and not actual_matches:
                        status, c.error_code = "failed", "actual_value_mismatch"
                    elif status == "succeeded" and c.context.get("policy_rule") and "actual_value" not in payload:
                        status, c.error_code = "failed", "actual_value_unconfirmed"
                    c.state_confirmed = (status == "succeeded" and "actual_value" in payload
                                         and actual_matches and not ble_unconfirmed
                                         and payload.get("confirmation") != "transport_only")
                    c.status = status if status in ("accepted", "executing", "succeeded", "failed", "timeout", "cancelled") else "failed"
                    if c.terminal:
                        c.completed_ts = now
                    self._log(f"[control] {c.target_id}.{c.operation}={c.value!r} → {c.status}")
                    return True
        return False

    def tick(self, now: float | None = None) -> None:
        """타임아웃 처리. 매 프레임 호출."""
        now = self._clock() if now is None else now
        ep = self.episode
        if ep is not None and ep.awaiting_user and now - ep.started_ts >= self.suggest_timeout:
            self._expire(ep, "suggestion deadline reached")
        # Closed episodes can still receive an undo command in MONITOR/RECOVERY.
        episodes = self.history + ([ep] if ep is not None else [])
        for episode in episodes:
            for c in episode.commands:
                if not c.terminal and c.sent_ts is not None and now >= c.expires_ts:
                    c.status, c.completed_ts, c.error_code = "timeout", now, "result_timeout"
                    self._log(f"[control] {c.target_id}.{c.operation} result timeout")

    def _expire(self, ep: Episode, why: str) -> None:
        # 제안에 응답이 없으면 만료 — 실행하지 않고 넘어간다(무응답도 하나의 신호로 기록)
        ep.awaiting_user = False
        for c in ep.commands:
            c.status = "skipped"
        ep.outcome = "expired"
        self._log(f"[control] {why} → expired")

    def action_done(self, now: float | None = None) -> bool:
        """현재 에피소드가 끝났는지(FSM frame.action_done 로 전달)."""
        self.tick(now)
        return self.episode is None or self.episode.done

    def close_episode(self) -> None:
        if self.episode is not None:
            self.history.append(self.episode)
            self.episode = None

    @staticmethod
    def _episode_summary(ep: Episode) -> dict[str, Any]:
        return {"state": ep.state, "cause": ep.cause, "gate": ep.gate.value, "outcome": ep.outcome,
                "awaiting_user": ep.awaiting_user,
                "commands": [{"command_id": c.command_id, "target": c.target_id, "op": c.operation,
                              "value": c.value, "status": c.status, "error_code": c.error_code,
                              "reason": c.context.get("policy_rule") or c.cause, "state_confirmed": c.state_confirmed}
                             for c in ep.commands],
                **({"advice": ep.advice} if ep.advice else {})}

    def summary(self) -> dict[str, Any]:
        if self.episode is not None:
            return {"active": True, **self._episode_summary(self.episode)}
        # Preserve the outcome through MONITOR instead of dropping the only
        # state that would let a display distinguish failure from completion.
        return {"active": False, **({"last_episode": self._episode_summary(self.history[-1])}
                                   if self.history else {})}

    # ---------------------------------------------------------------- 내부
    def _plan(self, state: str, cause: str | None, gate: GateMode, now: float,
              *, specs: list[dict[str, Any]] | None = None) -> list[ControlCommand]:
        if state == "ACTION_ENV" and self._plan_commands is not None:
            specs = self._plan_commands(state, cause, gate, now)
        elif specs is None:
            specs = self.actions.get(cause or "", []) if state in self.default_action_states else []
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
                value=spec.get("value"), undo_value=spec.get("undo_value"), undo_mode=spec.get("undo"),
                origin_state=state, cause=cause,
                gate=gate.value, requires_confirmation=irreversible, expires_ts=now + self.timeout,
                context=dict(spec.get("context") or {}),
            ))
        return cmds

    def _dispatch(self, ep: Episode, now: float) -> None:
        sent = False
        for c in ep.commands:
            if self._dispatch_guard is not None and not self._dispatch_guard(c, now):
                c.status = "skipped"
                continue
            c.sent_ts, c.status = now, "executing"
            # A suggestion can wait 180 s before dispatch; its execution timeout
            # starts when the user accepts, not when the card was created.
            c.expires_ts = now + self.timeout
            self._last_sent[(c.target_id, c.operation)] = now
            if self._on_dispatched is not None:
                self._on_dispatched(c, now)
            self.publish_cmd(c.to_payload())
            sent = True
        ep.executed = sent
        ep.executed_ts = now if sent else None
        ep.outcome = "executed" if sent else "advised" if ep.advice and not ep.commands else "skipped"
        self._log(f"[control] {ep.state}/{ep.cause}: {len(ep.commands)}건 발행 ({ep.gate.value})")

    def _undo_plan(self, c: ControlCommand) -> tuple[str, Any] | None:
        """이 명령을 되돌리는 (operation, value). 못 되돌리면 None.

        undo: previous — 앱 결과의 previous(바꾸기 전 원시 밝기)로 set_brightness. 결과가 없거나 previous 가 없으면 None.
        그 밖은 같은 operation 에 undo_value.
        """
        if c.undo_mode == "previous":
            previous = (c.result or {}).get("previous")
            if previous is None:
                self._log(f"[control] {c.target_id}.{c.operation} 되돌리기 생략 — 결과에 previous 가 없다")
                return None
            return "set_brightness", previous
        if c.undo_value is None:
            return None
        return c.operation, c.undo_value

    def _undo(self, ep: Episode, now: float) -> None:
        sent = False
        for c in list(ep.commands):
            if c.gate == "undo" or c.status not in ("succeeded", "executing"):
                continue
            plan = self._undo_plan(c)
            if plan is None:
                continue
            undo_op, undo_value = plan
            undo = ControlCommand(
                command_id=uuid.uuid4().hex[:12], target_id=c.target_id, operation=undo_op, value=undo_value,
                origin_state=ep.state, cause=ep.cause, gate="undo", requires_confirmation=False,
                expires_ts=now + self.timeout, sent_ts=now, status="executing",
                context=dict(c.context, undo_of_value=c.value, undo_of_command_id=c.command_id),
            )
            if self._dispatch_guard is not None and not self._dispatch_guard(undo, now):
                continue
            ep.commands.append(undo)
            self.publish_cmd(undo.to_payload())
            sent = True
        if not sent:
            self._log("[control] no eligible command to undo")
            return
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
        self.brightness: dict[str, int] = {}      # target → 모의 원시 밝기(adjust_brightness 계산용)
        self._pending: list[tuple[float, dict[str, Any]]] = []

    DEFAULT_BRIGHTNESS = 110          # 모의 램프의 처음 원시 밝기(0~255). Pi 5 앱의 실제 값은 램프 상태에서 온다

    def handle_command(self, payload: dict[str, Any]) -> None:
        target, operation, value = payload["target_id"], payload["operation"], payload["value"]
        result = {"command_id": payload["command_id"], "status": "succeeded", "actual_value": value,
                  "completed_ts_ms": int(self._clock() * 1000)}
        if operation == "adjust_brightness":
            # 앱과 같은 계약: 지금 밝기 × (1 + percent/100), 0~255 로 자른 뒤 previous·actual 을 결과에 싣는다
            previous = int(self.brightness.get(target, self.DEFAULT_BRIGHTNESS))
            percent = float((value or {}).get("percent", 0))
            actual = int(round(previous * (1.0 + percent / 100.0)))
            if (value or {}).get("min_raw") is not None:          # 깨우기 최소 밝기: 결과 = max(계산값, min_raw)
                actual = max(actual, int((value or {})["min_raw"]))
            actual = max(0, min(255, actual))
            self.brightness[target] = actual
            self.state[target] = {operation: value, "brightness": actual}
            result.update(previous=previous, actual=actual, actual_value=actual)
        else:
            if operation == "set_brightness":
                self.brightness[target] = int(value)
            self.state[target] = {operation: value}
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
