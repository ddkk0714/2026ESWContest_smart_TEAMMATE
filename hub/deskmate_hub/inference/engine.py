"""규칙 기반 FSM 코어 (1단계 추론 엔진).

- 매 tick 은 30초 주기의 SensorFrame 하나를 받아 상태를 한 단계 전이한다.
  (전이 조건은 docs/fsm-spec.md · config/fsm.yaml)
- 한 tick 당 최대 한 번 전이한다. 전이가 즉시 이어지는 상태(FATIGUE→CAUSE_ANALYSIS 등)는
  다음 tick 에서 진행된다. 시간 조건은 frame.now(epoch) 로 판정하므로 결정적이다.
- 2단계 분류기 없이 단독 완전 동작한다. 임계값은 코드에 없고 cfg 에서만 온다.
"""
from __future__ import annotations

from typing import Any

from .cause import dominant_cause, group_contributions
from .config import load_config
from .context import detect_context
from .scoring import compute_scores
from .states import CAUSE_TO_ACTION, FOCUS_STATES, State
from .types import Context, GateMode, Scores, SensorFrame, TickResult

_ACTION_STATES = {State.ACTION_ENV, State.ACTION_POSTURE, State.ACTION_BREAK}
_ALL_CAUSES = ("environment", "posture", "cognitive")


def _focus_state_for(context: Context) -> State:
    return {
        Context.PC: State.FOCUS_PC,
        Context.MIXED: State.FOCUS_MIXED,
        Context.NPC: State.FOCUS_NPC,
    }[context]


class FSMEngine:
    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or load_config()
        self.th = self.cfg["thresholds"]
        self.tm = self.cfg["timers"]
        self.state: State = State.IDLE
        self.context: Context = Context.MIXED
        self.scores: Scores | None = None

        # 타이머(전이 진입 시각·기준값)
        self._state_since: float = 0.0
        self._absent_since: float | None = None
        self._baseline_since: float = 0.0
        self._fatigue_elevated_since: float | None = None
        self._fatigue_high_since: float | None = None
        self._break_since: float = 0.0
        self._break_entry_fatigue: float = 0.0
        self._monitor_entry_fatigue: float = 0.0
        self._rest_since: float = 0.0
        # 이번 피로 에피소드에서 아직 시도하지 않은 개입 원인
        self._remaining_options: list[str] = []

    # ── 내부 헬퍼 ──────────────────────────────────────────────
    def _goto(self, new_state: State, now: float) -> None:
        self.state = new_state
        self._state_since = now

    def _held(self, since: float | None, now: float, key: str) -> bool:
        return since is not None and (now - since) >= self.tm[key]

    def _gate(self, confidence: float) -> GateMode:
        g = self.cfg["gate"]
        if confidence >= g["conf_auto"]:
            return GateMode.AUTO
        if confidence >= g["conf_suggest"]:
            return GateMode.SUGGEST
        return GateMode.NONE

    # ── 메인 tick ─────────────────────────────────────────────
    def tick(self, frame: SensorFrame) -> TickResult:
        now = frame.now
        self.context = detect_context(frame.pc_ratio, self.cfg)
        self.scores = compute_scores(frame, self.context, self.cfg)
        actions: list[str] = []
        gate = GateMode.NONE
        cause: str | None = None

        # 재실 타이머
        if frame.present:
            self._absent_since = None
        elif self._absent_since is None:
            self._absent_since = now

        # 전역: 모든 활성 상태에서 재실 없음 10분 → IDLE
        if self.state not in (State.IDLE, State.END) and self._held(
            self._absent_since, now, "absent_idle_sec"
        ):
            self._goto(State.IDLE, now)
            return self._result(actions=["absent_timeout"], gate=gate, cause=cause)

        s, c, th = self.state, self.scores, self.th

        if s is State.IDLE:
            if frame.touch:
                self._baseline_since = now
                self._goto(State.START, now)
                actions.append("baseline_timer.start")

        elif s is State.START:
            actions.append("capture_baseline")
            if frame.present and self._held(self._baseline_since, now, "baseline_sec"):
                self._goto(State.CONTEXT_DETECT, now)
                actions.append("store_baseline")

        elif s is State.CONTEXT_DETECT:
            self._goto(_focus_state_for(self.context), now)
            actions.append(f"set_weights:{self.context.value}")

        elif s in FOCUS_STATES:
            if frame.end_touch:
                self._goto(State.END, now)
            else:
                target_focus = _focus_state_for(self.context)
                if target_focus is not s:                       # 컨텍스트 재진입
                    self._goto(target_focus, now)
                    actions.append(f"context_switch:{self.context.value}")
                elif c.c_fatigue < th["fatigue_focus_low"]:
                    self._fatigue_elevated_since = None
                    if c.c_focus >= th["focus_break"]:
                        self._break_since = now
                        self._break_entry_fatigue = c.c_fatigue
                        self._goto(State.FOCUS_BREAK, now)
                        actions.append("classify_focus_cause")
                else:                                            # 피로 상승
                    if self._fatigue_elevated_since is None:
                        self._fatigue_elevated_since = now
                    if self._held(self._fatigue_elevated_since, now, "fatigue_elevated_hold_sec"):
                        self._goto(State.FATIGUE_SUSPECT, now)
                        actions.append("display_warning:yellow")

        elif s is State.FOCUS_BREAK:
            actions.append("micro_intervention")
            if c.c_focus < th["reengage_success"]:
                self._goto(_focus_state_for(self.context), now)   # 재유도 성공
                actions.append("reengage_success")
            elif self._held(self._break_since, now, "focus_break_poll_sec"):
                if c.c_fatigue > self._break_entry_fatigue or c.c_fatigue >= th["fatigue_focus_low"]:
                    self._goto(State.FATIGUE_SUSPECT, now)        # 실패 + 피로↑
                    actions.append("display_warning:yellow")
                else:
                    self._goto(_focus_state_for(self.context), now)

        elif s is State.FATIGUE_SUSPECT:
            if c.c_fatigue < th["natural_recovery"]:
                self._fatigue_high_since = None
                self._goto(_focus_state_for(self.context), now)   # 자연 회복
                actions.append("natural_recovery")
            elif c.c_fatigue >= th["fatigue_confirm"]:
                if self._fatigue_high_since is None:
                    self._fatigue_high_since = now
                if self._held(self._fatigue_high_since, now, "fatigue_confirm_hold_sec"):
                    self._remaining_options = list(_ALL_CAUSES)
                    self._goto(State.FATIGUE, now)
                    actions.append("log_fatigue_event")
            else:
                self._fatigue_high_since = None

        elif s is State.FATIGUE:
            self._goto(State.CAUSE_ANALYSIS, now)

        elif s is State.CAUSE_ANALYSIS:
            groups = group_contributions(frame, c.fatigue_weights, self.cfg)
            options = [g for g in self._remaining_options if g in groups] or self._remaining_options
            cause = max(options, key=lambda g: groups.get(g, 0.0)) if options else "cognitive"
            if cause in self._remaining_options:
                self._remaining_options.remove(cause)
            gate = self._gate(c.c_fatigue)
            self._goto(CAUSE_TO_ACTION[cause], now)
            actions.append(f"route:{cause}")

        elif s in _ACTION_STATES:
            actions.append({
                State.ACTION_ENV: "thinq:env",
                State.ACTION_POSTURE: "posture_alert",
                State.ACTION_BREAK: "break_suggest",
            }[s])
            gate = self._gate(c.c_fatigue)
            if s is State.ACTION_BREAK and frame.break_accepted:
                self._rest_since = now
                self._goto(State.REST, now)
                actions.append("rest_timer.start")
            elif frame.action_done or frame.break_accepted is False:
                self._monitor_entry_fatigue = c.c_fatigue
                self._goto(State.MONITOR, now)
                if frame.break_accepted is False:
                    actions.append("esm_label:reject")

        elif s is State.MONITOR:
            # TODO(9월): C_fatigue trend 를 5~10분 윈도우로 측정. 지금은 진입 대비 부호로 판정.
            if c.c_fatigue < self._monitor_entry_fatigue:
                self._goto(State.RECOVERY, now)
                actions += ["reward:+", "update_ai_policy"]
            else:
                self._goto(State.ESCALATE, now)
                actions += ["reward:-", "update_ai_policy"]

        elif s is State.RECOVERY:
            if c.c_fatigue < th["recovery_success"]:
                self._goto(_focus_state_for(self.context), now)
                actions += ["recovery_ok", "reward:+"]
            elif c.c_fatigue >= th["recovery_fail"]:
                self._goto(State.ESCALATE, now)
                actions += ["recovery_fail", "reward:-"]

        elif s is State.ESCALATE:
            actions.append("log_intervention_fail")
            if self._remaining_options:
                self._goto(State.CAUSE_ANALYSIS, now)             # 다른 원인 재시도
            else:
                self._rest_since = now
                self._goto(State.REST, now)                       # 강제 휴식
                actions.append("force_rest")

        elif s is State.REST:
            actions.append("maintain_env")
            if self._held(self._rest_since, now, "rest_min_sec"):
                self._goto(State.RECOVERY, now)

        elif s is State.END:
            actions += ["save_session_log", "display_summary"]

        return self._result(actions=actions, gate=gate, cause=cause)

    def _result(self, actions: list[str], gate: GateMode, cause: str | None) -> TickResult:
        return TickResult(
            state=self.state,
            context=self.context,
            scores=self.scores,
            actions=actions,
            gate=gate,
            cause=cause,
        )
