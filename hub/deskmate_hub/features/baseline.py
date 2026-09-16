"""개인 기준선(baseline) 정규화 — 중앙값·MAD 기반 Modified z-score.

포스터 「추론 레이어 2단계 : 개인화 · 개인 기준선 정규화」의 1단계 구현이다.
세션 초기(START, `baseline_sec`)에 모인 원지표(예: keystroke idle_ratio, mmWave motion_level, CO₂)로
신호별 median·MAD 를 만들고, 이후 표본을 평소 대비 상대값으로 바꾼다:

    z = 0.6745 * (x - median) / MAD          (Iglewicz & Hoaglin 의 Modified z-score)
    delta/phi = clip(z / z_full)   (증가가 피로·집중저하 증거인 지표), 방향이 반대면 -z 를 쓴다

시간대(hour bucket)별 기준선을 따로 유지해 오전/오후 습관 차이를 흡수하고, opt-in 이면 로컬 JSON 에 저장해
다음 세션의 초기값(seed)으로 쓴다. 원시 시퀀스는 저장하지 않는다 — median·MAD·표본 수만 남긴다
(data-spec §7 `baseline_record`, §12 프라이버시).
표준 라이브러리만 사용한다(Pi 4 제한 Python).
"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

MODIFIED_Z_CONST = 0.6745


def median_mad(values: Iterable[float]) -> tuple[float, float]:
    xs = sorted(float(v) for v in values)
    if not xs:
        raise ValueError("no samples")
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs)
    return med, mad


def modified_z(x: float, med: float, mad: float, *, mad_floor: float) -> float:
    """MAD 가 0 에 가까우면(값이 거의 일정) mad_floor 로 나눠 발산을 막는다."""
    return MODIFIED_Z_CONST * (float(x) - med) / max(mad, mad_floor)


@dataclass
class MetricBaseline:
    """한 지표의 기준선. 원시 표본은 보정 창 동안만 메모리에 두고 확정 후 버린다."""
    median: float | None = None
    mad: float | None = None
    sample_count: int = 0
    created_ts: float | None = None
    _samples: deque = field(default_factory=lambda: deque(maxlen=4096), repr=False)

    @property
    def ready(self) -> bool:
        return self.median is not None

    def add(self, x: float) -> None:
        self._samples.append(float(x))

    def finalize(self, min_samples: int) -> bool:
        if len(self._samples) < min_samples:
            return False
        self.median, self.mad = median_mad(self._samples)
        self.sample_count = len(self._samples)
        self.created_ts = time.time()
        self._samples.clear()
        return True

    def to_record(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_samples", None)
        return d

    @classmethod
    def from_record(cls, d: dict[str, Any]) -> "MetricBaseline":
        b = cls()
        b.median, b.mad = d.get("median"), d.get("mad")
        b.sample_count = int(d.get("sample_count") or 0)
        b.created_ts = d.get("created_ts")
        return b


class BaselineStore:
    """지표별·시간대별 기준선 모음.

    - `bucket_hours`: 시간대 버킷 폭(시간). 3 이면 0-3, 3-6, … 8개 버킷.
    - `persist_path`: opt-in 저장 경로(None 이면 저장 안 함). JSON 한 파일, median·MAD·표본 수만.
    """

    def __init__(self, cfg: dict[str, Any], *, persist_path: str | None = None, now: float | None = None) -> None:
        self.cfg = cfg
        self.min_samples = int(cfg.get("min_samples", 20))
        self.mad_floor = dict(cfg.get("mad_floor", {}))
        self.default_mad_floor = float(cfg.get("default_mad_floor", 1e-3))
        self.z_full = float(cfg.get("z_full", 3.5))
        self.bucket_hours = int(cfg.get("bucket_hours", 3))
        self.persist_path = persist_path
        self._session: dict[str, MetricBaseline] = {}           # 이번 세션(보정 창) 기준선
        self._buckets: dict[str, dict[str, MetricBaseline]] = {}  # bucket -> metric -> baseline (이전 세션 seed)
        self.calibrating = False
        if persist_path and os.path.exists(persist_path):
            self._load()

    # ---- 보정 창 ----
    def begin_calibration(self) -> None:
        self._session = {}
        self.calibrating = True

    def observe(self, metric: str, x: float | None) -> None:
        if x is None or not self.calibrating:
            return
        self._session.setdefault(metric, MetricBaseline()).add(x)

    def end_calibration(self, now: float | None = None) -> dict[str, bool]:
        """보정 창 종료. 지표별로 확정 성공 여부를 돌려주고, 성공한 것은 시간대 버킷에도 저장한다."""
        self.calibrating = False
        bucket = self.bucket_for(now)
        result: dict[str, bool] = {}
        for metric, b in self._session.items():
            ok = b.finalize(self.min_samples)
            result[metric] = ok
            if ok:
                self._buckets.setdefault(bucket, {})[metric] = MetricBaseline.from_record(b.to_record())
        if self.persist_path:
            self._save()
        return result

    # ---- 조회 ----
    def bucket_for(self, now: float | None = None) -> str:
        hour = time.localtime(time.time() if now is None else now).tm_hour
        start = (hour // self.bucket_hours) * self.bucket_hours
        return f"h{start:02d}"

    def baseline_for(self, metric: str, now: float | None = None) -> MetricBaseline | None:
        """이번 세션 기준선 → 같은 시간대 버킷 → 아무 버킷 순으로 찾는다."""
        b = self._session.get(metric)
        if b is not None and b.ready:
            return b
        b = self._buckets.get(self.bucket_for(now), {}).get(metric)
        if b is not None and b.ready:
            return b
        for bucket in self._buckets.values():
            if metric in bucket and bucket[metric].ready:
                return bucket[metric]
        return None

    def normalize(self, metric: str, x: float | None, *, direction: int = 1, now: float | None = None) -> float | None:
        """x → [0,1] 증거값. 기준선이 없거나 x 가 None 이면 None(호출자가 선형 폴백 사용).

        direction=+1: 값이 커질수록 증거(idle_ratio, flight_cv, CO₂ …). -1: 작아질수록 증거.
        """
        if x is None:
            return None
        b = self.baseline_for(metric, now)
        if b is None or b.median is None or b.mad is None:
            return None
        z = modified_z(x, b.median, b.mad, mad_floor=float(self.mad_floor.get(metric, self.default_mad_floor)))
        return max(0.0, min(1.0, direction * z / self.z_full))

    def snapshot(self) -> dict[str, Any]:
        """UI·로그용 요약(원시 표본 없음)."""
        return {
            "calibrating": self.calibrating,
            "session": {m: b.to_record() for m, b in self._session.items() if b.ready},
            "buckets": {k: {m: b.to_record() for m, b in v.items()} for k, v in self._buckets.items()},
        }

    # ---- 저장 (opt-in) ----
    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.persist_path)), exist_ok=True)
        with open(self.persist_path, "w", encoding="utf-8") as fh:
            json.dump({"schema": "baseline_record/1.0", "buckets": self.snapshot()["buckets"]}, fh, ensure_ascii=False, indent=1)

    def _load(self) -> None:
        try:
            with open(self.persist_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        for bucket, metrics in (data.get("buckets") or {}).items():
            self._buckets[bucket] = {m: MetricBaseline.from_record(rec) for m, rec in metrics.items()}

    def forget(self) -> None:
        """개인화 데이터 삭제(opt-out). 파일도 지운다."""
        self._session, self._buckets = {}, {}
        if self.persist_path and os.path.exists(self.persist_path):
            os.remove(self.persist_path)
