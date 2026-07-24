"""컨텍스트 판정과 가중치 선택 (PC / MIXED / 비PC)."""
from __future__ import annotations

from typing import Any

from .types import SIGNAL_KEYS, Context


def detect_context(pc_ratio: float, cfg: dict[str, Any]) -> Context:
    """PC_ratio(15분 윈도우)로 컨텍스트를 정한다."""
    c = cfg["context"]
    if pc_ratio > c["pc_ratio_high"]:
        return Context.PC
    if pc_ratio < c["pc_ratio_low"]:
        return Context.NPC
    return Context.MIXED


def resolve_weights(
    kind: str, context: Context, pc_ratio: float, cfg: dict[str, Any]
) -> dict[str, float]:
    """kind('focus'|'fatigue') 가중치를 컨텍스트별로 반환.

    MIXED 는 blend = r·PC + (1−r)·NPC, r = pc_ratio.
    respiration_enabled=False 이면 호흡 가중치를 0 으로 두고 scoring 이 재정규화한다.
    """
    table = cfg["weights"][kind]
    pc_w, npc_w = table["pc"], table["npc"]

    if context is Context.PC:
        weights = dict(pc_w)
    elif context is Context.NPC:
        weights = dict(npc_w)
    else:
        r = pc_ratio
        weights = {k: r * pc_w[k] + (1 - r) * npc_w[k] for k in SIGNAL_KEYS}

    if not cfg.get("respiration_enabled", False):
        weights["respiration"] = 0.0
    return weights
