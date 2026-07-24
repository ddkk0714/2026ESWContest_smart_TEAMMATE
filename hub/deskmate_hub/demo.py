"""합성 데모 세션 — 실기기/로그 없이 전체 경로를 한번 훑는다.

경로: IDLE → START → CONTEXT_DETECT → FOCUS_PC → (피로 상승) →
      FATIGUE_SUSPECT → FATIGUE → CAUSE_ANALYSIS → ACTION_BREAK →
      MONITOR → RECOVERY → FOCUS_PC → END
`python -m deskmate_hub --demo` 로 실행한다. 타이밍은 config 기본값(baseline 300s,
elevated/confirm hold 180s) 기준 30초 간격.
"""
from __future__ import annotations

from .inference import SensorFrame, Signal

# 신호 프리셋 (delta=피로 기여, phi=집중 기여)
_CALM = {
    "posture": {"delta": 0.10, "phi": 0.10},
    "elapsed": {"delta": 0.10, "phi": 0.10},
    "keystroke": {"delta": 0.10, "phi": 0.10},
    "environment": {"delta": 0.10},
}
_HOT = {
    "posture": {"delta": 0.90},
    "elapsed": {"delta": 0.90},
    "keystroke": {"delta": 0.85},
    "environment": {"delta": 0.80},
}


def _f(t: float, sig: dict, **flags) -> SensorFrame:
    signals = {k: Signal(**v) for k, v in sig.items()}
    return SensorFrame(now=t, present=True, pc_ratio=0.9, signals=signals, **flags)


def demo_frames() -> list[SensorFrame]:
    frames: list[SensorFrame] = [_f(0, _CALM, touch=True)]      # IDLE→START
    frames += [_f(t, _CALM) for t in range(30, 331, 30)]        # baseline→CONTEXT_DETECT→FOCUS
    frames += [_f(360, _CALM), _f(390, _CALM)]                  # 건강한 몰입
    frames += [_f(t, _HOT) for t in range(420, 601, 30)]        # 피로 상승 → FATIGUE_SUSPECT(600)
    frames += [_f(t, _HOT) for t in range(630, 811, 30)]        # 지속 → FATIGUE(810)
    frames += [_f(840, _HOT)]                                   # →CAUSE_ANALYSIS
    frames += [_f(870, _HOT)]                                   # →ACTION_BREAK(cognitive 지배)
    frames += [_f(900, _HOT, action_done=True)]                # 실행 완료 →MONITOR
    frames += [_f(t, _CALM) for t in range(930, 1051, 30)]     # 개선 →RECOVERY(930)→FOCUS(960)
    frames += [_f(1080, _CALM, end_touch=True)]                # →END
    return frames
