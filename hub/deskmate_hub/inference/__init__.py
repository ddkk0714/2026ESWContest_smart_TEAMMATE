"""규칙 기반 FSM 추론 엔진 (1단계). 담당: 박소연.

사용 예:
    from deskmate_hub.inference import FSMEngine, SensorFrame, Signal
    engine = FSMEngine()                 # config/fsm.yaml 로드
    result = engine.tick(frame)          # 30초 주기 호출
"""
from .config import load_config
from .engine import FSMEngine
from .report import (
    FatigueEpisode,
    Intervention,
    SessionRecorder,
    SessionReport,
    build_report,
    format_session_report,
)
from .states import State
from .types import Context, GateMode, Scores, SensorFrame, Signal, TickResult

__all__ = [
    "FSMEngine",
    "SensorFrame",
    "Signal",
    "Scores",
    "TickResult",
    "State",
    "Context",
    "GateMode",
    "load_config",
    "SessionRecorder",
    "SessionReport",
    "FatigueEpisode",
    "Intervention",
    "build_report",
    "format_session_report",
]
