"""FSM 입출력 계약 (types) — features/ 와 engine 사이의 경계.

engine 은 이 dataclass 만 알면 되므로 특징 추출 구현(김태환)과 느슨히 결합된다.
모든 신호 기여도(phi/delta)는 baseline 대비 상대값으로 [0,1] 정규화되어 들어온다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# 신호 키 — config weights / routing 과 반드시 일치
SIGNAL_KEYS = ("keystroke", "posture", "respiration", "environment", "elapsed")


class Context(str, Enum):
    """작업 컨텍스트 (PC_ratio 로 판정)."""
    PC = "pc"
    MIXED = "mixed"
    NPC = "npc"


class GateMode(str, Enum):
    """신뢰도 게이트 결과."""
    AUTO = "auto"        # 자동 제어
    SUGGEST = "suggest"  # 제안 카드
    NONE = "none"        # 무동작(로그만)


@dataclass
class Signal:
    """단일 신호의 집중/피로 기여도.

    phi: 집중(C_focus) 기여도, delta: 피로(C_fatigue) 기여도, 둘 다 [0,1].
    available=False 이면 scoring 에서 제외하고 분모를 재정규화한다
    (예: 비타이핑 구간의 keystroke, 정지 구간 밖의 respiration).
    """
    phi: float = 0.0
    delta: float = 0.0
    available: bool = True


@dataclass
class SensorFrame:
    """한 tick 의 입력. ingest/features 가 30초 주기로 만들어 engine 에 넣는다."""
    now: float                      # epoch seconds
    present: bool                   # 재실 여부 (ToF)
    pc_ratio: float                 # 15분 윈도우 내 PC/키보드 활성 비율 [0,1]
    signals: dict[str, Signal] = field(default_factory=dict)

    # 이벤트 플래그
    touch: bool = False             # 디스플레이 터치(작업 시작)
    end_touch: bool = False         # 작업 종료 터치
    action_done: bool = False       # ACTION_* 실행 완료
    break_accepted: bool | None = None  # ACTION_BREAK 사용자 결정(수락/거절)

    def get(self, key: str) -> Signal:
        return self.signals.get(key, Signal(available=False))


@dataclass
class Scores:
    c_focus: float
    c_fatigue: float
    focus_weights: dict[str, float]
    fatigue_weights: dict[str, float]


@dataclass
class TickResult:
    state: "object"                 # State enum (states.py) — 순환 import 방지용 느슨한 타입
    context: Context
    scores: Scores
    actions: list[str] = field(default_factory=list)
    gate: GateMode = GateMode.NONE
    cause: str | None = None        # dominant 그룹 (environment/posture/cognitive)
