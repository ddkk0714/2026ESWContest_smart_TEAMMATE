"""이중 신뢰도 계산: C_fatigue = Σ(wᵢ·δᵢ), C_focus = Σ(vᵢ·φᵢ).

가용하지 않은 신호(available=False 또는 가중치 0)는 제외하고 분모를 재정규화한다.
키스트로크가 없는 구간에서는 해당 항이 빠지고 분모도 줄어든다.
"""
from __future__ import annotations

from typing import Any

from .context import Context, resolve_weights
from .types import SIGNAL_KEYS, Scores, SensorFrame


def _weighted(frame: SensorFrame, weights: dict[str, float], attr: str) -> float:
    num = 0.0
    den = 0.0
    for key in SIGNAL_KEYS:
        w = weights.get(key, 0.0)
        if w <= 0.0:
            continue
        sig = frame.get(key)
        if not sig.available:
            continue
        num += w * getattr(sig, attr)
        den += w
    if den == 0.0:
        return 0.0
    return num / den


def compute_scores(frame: SensorFrame, context: Context, cfg: dict[str, Any]) -> Scores:
    focus_w = resolve_weights("focus", context, frame.pc_ratio, cfg)
    fatigue_w = resolve_weights("fatigue", context, frame.pc_ratio, cfg)
    return Scores(
        c_focus=_weighted(frame, focus_w, "phi"),
        c_fatigue=_weighted(frame, fatigue_w, "delta"),
        focus_weights=focus_w,
        fatigue_weights=fatigue_w,
    )
