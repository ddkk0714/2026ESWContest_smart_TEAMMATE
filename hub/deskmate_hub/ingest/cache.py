"""최근 센서 표본 보관소. MQTT 수신 스레드가 넣고 프레임 빌더가 읽는다.

원본 payload 는 보관하지 않는다 — 계약(data-spec §6)에 있는 특징값만 남기고,
키 내용·ToF raw 는 애초에 토픽에 실려 오지 않는다.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Sample:
    """한 토픽의 마지막 표본. received 는 hub 시계(epoch s)."""
    received: float
    ts: float
    seq: int | None
    data: dict[str, Any]


@dataclass
class KeystrokeTick:
    received: float
    input_active: bool


@dataclass
class SensorCache:
    """스레드 안전한 최신 표본 저장소."""
    mmwave: Sample | None = None
    env: Sample | None = None
    keystroke: Sample | None = None
    feedback: deque = field(default_factory=lambda: deque(maxlen=16))
    keystroke_history: deque = field(default_factory=lambda: deque(maxlen=1800))
    seq_gaps: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---- 쓰기 (수신 스레드) ----
    def put(self, kind: str, sample: Sample) -> None:
        with self._lock:
            prev = getattr(self, kind)
            if prev is not None and prev.seq is not None and sample.seq is not None:
                if sample.seq != prev.seq + 1 and sample.seq > prev.seq:
                    self.seq_gaps[kind] = self.seq_gaps.get(kind, 0) + 1
            setattr(self, kind, sample)
            if kind == "keystroke":
                self.keystroke_history.append(
                    KeystrokeTick(sample.received, bool(sample.data.get("input_active", False)))
                )

    def put_feedback(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.feedback.append(payload)

    # ---- 읽기 (프레임 빌더) ----
    def snapshot(self) -> "CacheView":
        with self._lock:
            return CacheView(
                mmwave=self.mmwave,
                env=self.env,
                keystroke=self.keystroke,
                keystroke_history=list(self.keystroke_history),
                feedback=list(self.feedback),
            )

    def pop_feedback(self) -> dict[str, Any] | None:
        with self._lock:
            return self.feedback.popleft() if self.feedback else None


@dataclass
class CacheView:
    """snapshot() 결과. 락 밖에서 자유롭게 읽는다."""
    mmwave: Sample | None
    env: Sample | None
    keystroke: Sample | None
    keystroke_history: list[KeystrokeTick]
    feedback: list[dict[str, Any]]

    def fresh(self, kind: str, now: float, max_age: float) -> Sample | None:
        sample: Sample | None = getattr(self, kind)
        if sample is None or now - sample.received > max_age:
            return None
        return sample
