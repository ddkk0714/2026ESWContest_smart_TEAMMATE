"""조합 조건(guard) 평가 — 가중합이 표현하지 못하는 다신호 AND 규칙.

사양은 docs/guard-spec.md 다. 규칙 자체는 코드가 아니라 config/fsm.yaml 의
`guards:` 에 선언한다(임계값·가중치와 같은 취급).

`C_fatigue = Σ(wᵢ·δᵢ)` 는 신호를 독립적으로 더하므로 "셋이 동시에 성립" 과
"하나만 크게 성립" 을 구분하지 못한다. guard 는 그 구분을 담당하는 병렬 층이며,
guards 가 비어 있으면 엔진은 guard 도입 전과 동일하게 동작한다.
"""
from __future__ import annotations

from typing import Any

from .types import SIGNAL_KEYS, SensorFrame

_COMBINATORS = ("all", "any", "none")
_META_KEYS = ("enabled", "hold_sec", "route_to", "actions")
_PREDICATE_KEYS = (
    "signal", "reason", "available",
    "delta_gte", "delta_lt", "phi_gte", "phi_lt",
)
_NUMERIC_KEYS = ("delta_gte", "delta_lt", "phi_gte", "phi_lt")


# ── 검증 (설정 로드 시 1회) ──────────────────────────────────────
# 오타는 조용히 거짓이 되는 대신 즉시 실패한다. 전송 스키마의 "알 수 없는 필드
# 무시"(data-spec §14) 는 설정 파일에는 적용하지 않는다 — 규칙이 말없이
# 동작하지 않는 쪽이 훨씬 위험하다.

def _fail(guard: str, msg: str) -> None:
    raise ValueError(f"guards.{guard}: {msg}")


def _validate_node(guard: str, node: Any) -> None:
    if not isinstance(node, dict):
        _fail(guard, f"조건은 매핑이어야 한다: {node!r}")
    combs = [k for k in _COMBINATORS if k in node]
    if combs:
        unknown = set(node) - set(_COMBINATORS)
        if unknown:
            _fail(guard, f"결합자와 조건을 한 매핑에 섞을 수 없다: {sorted(unknown)}")
        for key in combs:
            items = node[key]
            if not isinstance(items, list):
                _fail(guard, f"'{key}' 는 목록이어야 한다")
            for item in items:
                _validate_node(guard, item)
        return

    unknown = set(node) - set(_PREDICATE_KEYS)
    if unknown:
        _fail(guard, f"알 수 없는 조건 키 {sorted(unknown)}")
    signal = node.get("signal")
    if signal is None:
        _fail(guard, "조건에 'signal' 이 필요하다")
    if signal not in SIGNAL_KEYS:
        _fail(guard, f"알 수 없는 신호 '{signal}' (가능: {list(SIGNAL_KEYS)})")
    if len(set(node) - {"signal"}) == 0:
        _fail(guard, f"'{signal}' 에 검사할 조건이 없다")
    for key in _NUMERIC_KEYS:
        if key in node and not isinstance(node[key], (int, float)):
            _fail(guard, f"'{key}' 는 수치여야 한다")


def _validate(defs: dict[str, Any]) -> None:
    for name, gdef in defs.items():
        if not isinstance(gdef, dict):
            _fail(name, "guard 정의는 매핑이어야 한다")
        unknown = set(gdef) - set(_META_KEYS) - set(_COMBINATORS)
        if unknown:
            _fail(name, f"알 수 없는 키 {sorted(unknown)}")
        if not any(k in gdef for k in _COMBINATORS):
            _fail(name, "all/any/none 중 하나가 필요하다")
        actions = gdef.get("actions")
        if actions is not None and not isinstance(actions, list):
            _fail(name, "'actions' 는 목록이어야 한다")
        _validate_node(name, {k: v for k, v in gdef.items() if k in _COMBINATORS})


# ── 평가 ────────────────────────────────────────────────────────

def _predicate(frame: SensorFrame, spec: dict[str, Any]) -> bool:
    sig = frame.get(spec["signal"])
    if "available" in spec and sig.available is not bool(spec["available"]):
        return False
    if "reason" in spec and not sig.has(spec["reason"]):
        return False
    # 값 비교는 가용한 신호에서만 의미가 있다. 미가용 신호의 0.0 을
    # "낮다" 로 읽으면 안 된다 (data-spec DAT-T01: 0 과 결측은 다르다).
    for key in _NUMERIC_KEYS:
        if key not in spec:
            continue
        if not sig.available:
            return False
        value = sig.delta if key.startswith("delta") else sig.phi
        threshold = float(spec[key])
        if key.endswith("_gte") and not value >= threshold:
            return False
        if key.endswith("_lt") and not value < threshold:
            return False
    return True


def _combine(frame: SensorFrame, node: dict[str, Any]) -> bool:
    combs = [k for k in _COMBINATORS if k in node]
    if not combs:
        return _predicate(frame, node)
    for key in combs:                      # 결합자가 여럿이면 그것들끼리 AND
        items = node[key] or []
        results = (_combine(frame, item) for item in items)
        if key == "all" and not all(results):
            return False
        if key == "any" and not any(results):
            return False
        if key == "none" and any(results):
            return False
    return True


class GuardEvaluator:
    """guard 상태(지속 타이머·활성 여부)를 tick 사이에 유지한다."""

    def __init__(self, cfg: dict[str, Any]):
        self.defs: dict[str, Any] = dict(cfg.get("guards") or {})
        _validate(self.defs)
        self._since: dict[str, float] = {}   # 조건이 끊기지 않고 성립한 시작 시각
        self._active: set[str] = set()       # hold 까지 통과해 성립 중인 guard

    def _reset(self, name: str) -> None:
        self._since.pop(name, None)
        self._active.discard(name)

    def evaluate(self, frame: SensorFrame, now: float) -> tuple[list[str], list[str]]:
        """(성립 중인 guard, 이번 tick 에 새로 성립한 guard) 를 선언 순서로 반환."""
        active: list[str] = []
        newly: list[str] = []
        for name, gdef in self.defs.items():
            if not gdef.get("enabled", True) or not _combine(
                frame, {k: v for k, v in gdef.items() if k in _COMBINATORS}
            ):
                self._reset(name)            # 끊기면 지속 타이머도 0 으로
                continue
            since = self._since.setdefault(name, now)
            if now - since < float(gdef.get("hold_sec", 0.0)):
                self._active.discard(name)   # 조건은 맞지만 아직 지속 미달
                continue
            active.append(name)
            if name not in self._active:
                self._active.add(name)
                newly.append(name)
        return active, newly

    def actions_for(self, names: list[str]) -> list[str]:
        out: list[str] = []
        for name in names:
            out.extend(self.defs[name].get("actions") or [])
        return out

    def route_for(self, names: list[str]) -> str | None:
        """route_to 를 가진 활성 guard 중 선언 순서가 가장 앞선 것."""
        active = set(names)
        for name, gdef in self.defs.items():
            if name in active and gdef.get("route_to"):
                return gdef["route_to"]
        return None
