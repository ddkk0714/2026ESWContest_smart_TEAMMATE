"""FSM 상태 정의 (VER5, 5계층 18상태). docs/fsm-spec.md 참조."""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    # 대기
    IDLE = "IDLE"
    # 시작
    START = "START"
    CONTEXT_DETECT = "CONTEXT_DETECT"
    # 몰입 (ACTIVE)
    FOCUS_PC = "FOCUS_PC"
    FOCUS_MIXED = "FOCUS_MIXED"
    FOCUS_NPC = "FOCUS_NPC"          # 비PC
    # 판단
    FOCUS_BREAK = "FOCUS_BREAK"
    FATIGUE_SUSPECT = "FATIGUE_SUSPECT"
    FATIGUE = "FATIGUE"
    # 후속조치
    CAUSE_ANALYSIS = "CAUSE_ANALYSIS"
    MONITOR = "MONITOR"
    ESCALATE = "ESCALATE"
    RECOVERY = "RECOVERY"
    # 출력
    ACTION_ENV = "ACTION_ENV"
    ACTION_POSTURE = "ACTION_POSTURE"
    ACTION_BREAK = "ACTION_BREAK"
    REST = "REST"
    END = "END"


# 몰입 슈퍼상태(ACTIVE) — 판단·후속조치 전이를 공유한다.
FOCUS_STATES = frozenset({State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC})

# 개입 원인 그룹 → 출력 상태 매핑
CAUSE_TO_ACTION = {
    "environment": State.ACTION_ENV,
    "posture": State.ACTION_POSTURE,
    "cognitive": State.ACTION_BREAK,
}
