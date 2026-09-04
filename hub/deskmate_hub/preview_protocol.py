"""Transport-neutral state store and validation for display preview adapters."""
from __future__ import annotations

import threading
from collections import deque
from typing import Any


class PreviewStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._feedback: deque[dict[str, Any]] = deque()
        self._test_frames: deque[dict[str, Any]] = deque()

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._state = payload

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state

    def add_feedback(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._feedback.append(payload)

    def pop_feedback(self) -> dict[str, Any] | None:
        with self._lock:
            return self._feedback.popleft() if self._feedback else None

    def add_test_frame(self, payload: dict[str, Any]) -> None:
        with self._lock:
            # Slider drags can produce requests faster than the demo loop.
            self._test_frames.clear()
            self._test_frames.append(payload)

    def pop_test_frame(self) -> dict[str, Any] | None:
        with self._lock:
            return self._test_frames.popleft() if self._test_frames else None


def validate_test_frame(body: dict[str, Any]) -> None:
    if body.get("command") not in {"reset", "tick"}:
        raise ValueError("invalid command")
    if not isinstance(body.get("present"), bool):
        raise ValueError("invalid present")
    _unit(body.get("pc_ratio"))
    advance = body.get("advance_sec")
    if not isinstance(advance, (int, float)) or isinstance(advance, bool) or not 0 <= advance <= 3600:
        raise ValueError("invalid advance_sec")
    signals = body.get("signals")
    if not isinstance(signals, dict) or set(signals) - {
        "keystroke", "posture", "respiration", "environment", "elapsed"
    }:
        raise ValueError("invalid signals")
    for signal in signals.values():
        if not isinstance(signal, dict):
            raise ValueError("invalid signal")
        _unit(signal.get("phi"))
        _unit(signal.get("delta"))
        if not isinstance(signal.get("available", True), bool):
            raise ValueError("invalid availability")


def _unit(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("not numeric")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("outside unit interval")
    return number
