"""최근 센서 표본 보관소. MQTT 수신 스레드가 넣고 프레임 빌더가 읽는다.

원본 payload 는 보관하지 않는다 — 계약(data-spec §6)에 있는 특징값만 남기고,
키 내용은 애초에 토픽에 실려 오지 않는다. 예외는 ToF 원본 격자(UART 0x05, 2026-10-17): Pi 4 자세 모델의
입력이라 마지막 격자 하나만 메모리에 둔다. MQTT·로그·요약으로는 내보내지 않는다.
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
    tof: Sample | None = None            # UART 0x03 tof_feature (5 Hz)
    tof_posture: Sample | None = None    # UART 0x04 자세 요약 (1 Hz)
    tof_grid: Sample | None = None       # UART 0x05 원본 8×8 센서 프레임 묶음(표본 하나, 5 Hz). 메모리 전용 — 발행·기록 금지
    tof_mlp: Sample | None = None        # posture/tof_mlp 가 격자마다 낸 자세 판정 (1 s 다수결)
    weather: Sample | None = None        # deskmate/env/weather (Pi 5 앱, retain, 30 분). FSM 입력 아님 — personalize 만 읽는다
    feedback: deque = field(default_factory=lambda: deque(maxlen=16))
    control_results: deque = field(default_factory=lambda: deque(maxlen=64))
    feedback_received: deque = field(default_factory=lambda: deque(maxlen=16))
    control_received: deque = field(default_factory=lambda: deque(maxlen=64))
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

    def put_feedback(self, payload: dict[str, Any], *, received: float | None = None) -> None:
        with self._lock:
            self.feedback.append(payload)
            self.feedback_received.append(received)

    def put_control_result(self, payload: dict[str, Any], *, received: float | None = None) -> None:
        with self._lock:
            self.control_results.append(payload)
            self.control_received.append(received)

    def pop_control_result_events(self) -> list[tuple[dict[str, Any], float | None]]:
        with self._lock:
            items = list(zip(self.control_results, self.control_received))
            self.control_results.clear()
            self.control_received.clear()
            return items

    def pop_control_results(self) -> list[dict[str, Any]]:
        return [payload for payload, _ in self.pop_control_result_events()]

    # ---- 읽기 (프레임 빌더) ----
    def snapshot(self) -> "CacheView":
        with self._lock:
            return CacheView(
                mmwave=self.mmwave,
                env=self.env,
                keystroke=self.keystroke,
                tof=self.tof,
                tof_posture=self.tof_posture,
                tof_grid=self.tof_grid,
                tof_mlp=self.tof_mlp,
                weather=self.weather,
                keystroke_history=list(self.keystroke_history),
                feedback=list(self.feedback),
            )

    def pop_feedback_event(self) -> tuple[dict[str, Any] | None, float | None]:
        with self._lock:
            if not self.feedback:
                return None, None
            return self.feedback.popleft(), self.feedback_received.popleft()

    def pop_feedback_events(self) -> list[tuple[dict[str, Any], float | None]]:
        with self._lock:
            items = list(zip(self.feedback, self.feedback_received))
            self.feedback.clear()
            self.feedback_received.clear()
            return items

    def pop_feedback(self) -> dict[str, Any] | None:
        return self.pop_feedback_event()[0]


@dataclass
class CacheView:
    """snapshot() 결과. 락 밖에서 자유롭게 읽는다."""
    mmwave: Sample | None
    env: Sample | None
    keystroke: Sample | None
    keystroke_history: list[KeystrokeTick]
    feedback: list[dict[str, Any]]
    tof: Sample | None = None
    tof_posture: Sample | None = None
    tof_grid: Sample | None = None
    tof_mlp: Sample | None = None
    weather: Sample | None = None

    def fresh(self, kind: str, now: float, max_age: float) -> Sample | None:
        sample: Sample | None = getattr(self, kind)
        if sample is None or now - sample.received > max_age:
            return None
        return sample
