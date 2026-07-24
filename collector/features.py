"""입력 타이밍 → 특징 벡터 → 팀 MQTT 규약 페이로드.

발행 스키마는 docs/mqtt-topics.md 의 `deskmate/sensor/keystroke` 를 따른다:
  dwell_mean_ms, dwell_std_ms, flight_mean_ms, flight_std_ms, idle_ratio, correction_rate
추가(additive) 신호: typing_active / input_active / mouse_active / flight_cv / mouse_event_rate
  → hub 가 "마우스 작업 vs 글 읽기"를 구분하고 리듬 불규칙성을 쓰도록 돕는다.
  (규약 추가분이므로 이민혁과 docs/mqtt-topics.md 확정 필요 — PR 참조)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .capture import KeyEvent, KeyKind, MouseEvent


@dataclass
class Stat:
    mean: float = 0.0
    std: float = 0.0

    @classmethod
    def of(cls, xs: list[float]) -> "Stat":
        if not xs:
            return cls()
        if len(xs) == 1:
            return cls(mean=round(xs[0], 2), std=0.0)
        return cls(mean=round(statistics.fmean(xs), 2), std=round(statistics.pstdev(xs), 2))


@dataclass
class Features:
    window_s: int
    typing_active: bool = False
    mouse_active: bool = False
    input_active: bool = False
    keydown_count: int = 0
    dwell: Stat = field(default_factory=Stat)
    flight: Stat = field(default_factory=Stat)
    flight_cv: float = 0.0
    correction_rate: float = 0.0        # 백스페이스 빈도
    idle_ratio: float = 0.0
    pause_count: int = 0
    mouse_event_rate: float = 0.0       # 분당 마우스 이벤트

    def to_payload(self, node: str) -> dict:
        """docs/mqtt-topics.md 규약 필드 + additive 필드. ts 는 publisher 가 주입."""
        return {
            "node": node,
            "window_s": self.window_s,
            # --- 규약 확정 필드 ---
            "dwell_mean_ms": self.dwell.mean,
            "dwell_std_ms": self.dwell.std,
            "flight_mean_ms": self.flight.mean,
            "flight_std_ms": self.flight.std,
            "idle_ratio": self.idle_ratio,
            "correction_rate": self.correction_rate,
            # --- additive (이민혁 확정 대기) ---
            "typing_active": self.typing_active,
            "mouse_active": self.mouse_active,
            "input_active": self.input_active,
            "flight_cv": self.flight_cv,
            "mouse_event_rate": self.mouse_event_rate,
        }


def extract(
    events: list[KeyEvent],
    window_s: int,
    now_t: float,
    mouse_events: list[MouseEvent] | None = None,
    flight_gap_max_s: float = 2.0,
    idle_gap_s: float = 3.0,
) -> Features:
    """[now_t - window_s, now_t] 구간에서 특징을 뽑는다."""
    start_t = now_t - window_s
    evs = [e for e in events if start_t <= e.t <= now_t]
    f = Features(window_s=int(window_s))

    # ---------- 마우스 (키보드 유무와 무관하게 계산) ----------
    mevs = [e for e in (mouse_events or []) if start_t <= e.t <= now_t]
    if mevs:
        f.mouse_active = True
        f.mouse_event_rate = round(len(mevs) / window_s * 60.0, 1)

    # ---------- 키보드 ----------
    downs = [e for e in evs if e.down]
    f.keydown_count = len(downs)
    if not downs:
        f.idle_ratio = 1.0
        f.input_active = f.mouse_active
        return f

    f.typing_active = True
    f.input_active = True

    bs = sum(1 for e in downs if e.kind == KeyKind.BACKSPACE)
    f.correction_rate = round(bs / len(downs), 4)

    # dwell: 같은 종류 press→다음 release FIFO 매칭(근사)
    dwell_ms: list[float] = []
    pending: dict[KeyKind, list[float]] = {}
    for e in evs:
        if e.down:
            pending.setdefault(e.kind, []).append(e.t)
        else:
            q = pending.get(e.kind)
            if q:
                dt = (e.t - q.pop(0)) * 1000.0
                if 0 < dt < 2000:
                    dwell_ms.append(dt)
    f.dwell = Stat.of(dwell_ms)

    # flight: 연속 keydown 간격. 멈춤(> flight_gap_max)은 리듬 통계서 제외
    dts = [e.t for e in downs]
    flights_ms: list[float] = []
    pauses = 0
    for a, b in zip(dts, dts[1:]):
        gap = b - a
        if gap >= idle_gap_s:
            pauses += 1
        if gap <= flight_gap_max_s:
            flights_ms.append(gap * 1000.0)
    f.flight = Stat.of(flights_ms)
    f.flight_cv = round(f.flight.std / f.flight.mean, 3) if f.flight.mean else 0.0
    f.pause_count = pauses

    # idle 비율: 앞뒤 여백 + 입력 사이 idle_gap 초과분
    idle = max(0.0, dts[0] - start_t) + max(0.0, now_t - dts[-1])
    for a, b in zip(dts, dts[1:]):
        if (b - a) > idle_gap_s:
            idle += (b - a)
    f.idle_ratio = round(min(1.0, idle / window_s), 4)

    return f
