"""FSM 결과를 디스플레이가 소비하는 상태 계약으로 변환한다."""
from __future__ import annotations

import time
from typing import Any

from .inference import State, TickResult

_PHASE_BY_STATE = {
    State.IDLE: "idle",
    State.START: "start",
    State.CONTEXT_DETECT: "start",
    State.FOCUS_PC: "focus",
    State.FOCUS_MIXED: "focus",
    State.FOCUS_NPC: "focus",
    State.FOCUS_BREAK: "focus",
    State.FATIGUE_SUSPECT: "fatigue",
    State.FATIGUE: "fatigue",
    State.CAUSE_ANALYSIS: "fatigue",
    State.MONITOR: "fatigue",
    State.ESCALATE: "fatigue",
    State.ACTION_ENV: "fatigue",
    State.ACTION_POSTURE: "fatigue",
    State.ACTION_BREAK: "fatigue",
    State.REST: "recovery",
    State.RECOVERY: "recovery",
    State.END: "end",
}


def state_envelope(
    result: TickResult,
    *,
    boot_id: str,
    seq: int,
    ts: float | None = None,
    sensor_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`deskmate/state/phase`와 같은 JSON envelope를 만든다.

    sensor_summary는 UI 시연용 선택 필드다. 센서 원본은 넣지 않는다.
    """
    focus = _unit(result.scores.c_focus)
    fatigue = _unit(result.scores.c_fatigue)
    return {
        "schema_version": "1.0",
        "ts": time.time() if ts is None else ts,
        "node": "hub",
        "boot_id": boot_id,
        "seq": seq,
        "data": {
            "fsm_state": result.state.value,
            "phase": _PHASE_BY_STATE[result.state],
            "context": result.context.value,
            "c_focus": round(focus, 4),
            "c_fatigue": round(fatigue, 4),
            "confidence": round(max(focus, fatigue), 4),
            "source": "fsm",
            "gate": result.gate.value,
            "cause": result.cause,
            "reasons": list(result.actions),
            "sensor_summary": sensor_summary or {},
        },
    }


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
