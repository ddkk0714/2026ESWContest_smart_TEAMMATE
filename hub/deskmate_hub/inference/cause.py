"""개입 원인 분석: dominant = argmax(wᵢ·δᵢ) → 원인 그룹 → 출력 상태.

환경성(CO₂·조도) → ACTION_ENV, 자세성/생리(자세·호흡) → ACTION_POSTURE,
인지성(경과시간·키스트로크) → ACTION_BREAK.
"""
from __future__ import annotations

from typing import Any

from .types import SensorFrame


def group_contributions(
    frame: SensorFrame, fatigue_weights: dict[str, float], cfg: dict[str, Any]
) -> dict[str, float]:
    """원인 그룹별 피로 기여도 합 (wᵢ·δᵢ)."""
    routing = cfg["routing"]
    scores: dict[str, float] = {}
    for group, keys in routing.items():
        total = 0.0
        for key in keys:
            w = fatigue_weights.get(key, 0.0)
            sig = frame.get(key)
            if w > 0.0 and sig.available:
                total += w * sig.delta
        scores[group] = total
    return scores


def dominant_cause(
    frame: SensorFrame, fatigue_weights: dict[str, float], cfg: dict[str, Any]
) -> str:
    """가장 크게 기여한 원인 그룹 키를 반환 (없으면 'cognitive')."""
    scores = group_contributions(frame, fatigue_weights, cfg)
    if not scores or max(scores.values()) == 0.0:
        return "cognitive"   # 근거 부족 시 안전하게 휴식 권유
    return max(scores, key=scores.get)
