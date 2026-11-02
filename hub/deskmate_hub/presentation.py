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
    present: bool = True,
) -> dict[str, Any]:
    """`deskmate/state/phase`와 같은 JSON envelope를 만든다.

    sensor_summary는 UI 시연용 선택 필드다. 센서 원본은 넣지 않는다.
    present false(자리 비움)면 c_focus·c_fatigue·confidence 를 0.0 으로 싣고 `scores_valid: false` 를 붙인다 — 부재면 자세·키 입력·경과가
    빠져 환경 하나만 남고 재정규화가 어두운 방을 피로 1.0 으로 만든다(10-26 보드). 엔진 점수는 그대로(판정은 점수를 안 쓴다).
    """
    focus = _unit(result.scores.c_focus) if present else 0.0
    fatigue = _unit(result.scores.c_fatigue) if present else 0.0
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
            "present": bool(present),
            "scores_valid": bool(present),       # false 면 앱·devview 는 숫자 대신 '자리 비움'
            "source": "fsm",
            "gate": result.gate.value,
            "cause": result.cause,
            "reasons": list(result.actions),
            "sensor_summary": sensor_summary or {},
        },
    }


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
