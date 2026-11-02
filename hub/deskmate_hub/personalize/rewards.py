"""보상 정의 — docs/personalization-spec.md §5.1. numpy 없이 쓸 수 있게 policy 에서 떼어 둔다(기록 전용 경로·Pi 4)."""
from __future__ import annotations

VERDICTS = ("accept", "reject", "timeout", "undo", "auto_kept")
REWARD_ACCEPT = {"accept": 1.0, "timeout": 0.0, "reject": -1.0, "undo": -2.0, "auto_kept": 0.0}


def reward_effect(c_before: float, c_after: float, full_drop: float) -> float:
    return float(max(-1.0, min(1.0, (c_before - c_after) / full_drop)))
