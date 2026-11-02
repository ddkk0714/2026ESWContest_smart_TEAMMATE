"""공용 피로 모델(KSS 회귀) 실행과 프로필별 개인 보정. 표준 라이브러리만.

가중치는 `ml/training/fatigue_train.py` 가 만든 JSON(format deskmate-fatigue-linear-1)이다.
없는 특징은 학습 평균(표준화 후 0)으로 둔다 — 센서 하나가 빠져도 점수는 나온다.

개인 head 1단계는 프로필마다 '공용 예측이 평소 얼마나 빗나가는가' 하나(bias)만 배운다.
응답이 적을 때 한두 개로 크게 흔들리지 않게 prior_n 만큼 0 쪽으로 당긴다.
"""
from __future__ import annotations

import json
import math
from typing import Any

FORMAT = "deskmate-fatigue-linear-1"
KSS_MIN, KSS_MAX = 1.0, 9.0


class FatigueModel:
    def __init__(self, spec: dict[str, Any]) -> None:
        if spec.get("format") != FORMAT:
            raise ValueError(f"fatigue: 모르는 형식 {spec.get('format')!r}")
        self.features: list[str] = [str(f) for f in spec["features"]]
        n = len(self.features)
        self.mu = [float(v) for v in spec["mu"]]
        self.sd = [float(v) for v in spec["sd"]]
        self.coef = [float(v) for v in spec["coef"]]
        self.intercept = float(spec["intercept"])
        if not (len(self.mu) == len(self.sd) == len(self.coef) == n) or any(s <= 0 for s in self.sd):
            raise ValueError("fatigue: 가중치 모양이 맞지 않는다")
        self.window_sec = float(spec.get("window_sec", 300))
        self.baseline_sec = float(spec.get("baseline_sec", 600))

    @classmethod
    def load(cls, path: str) -> "FatigueModel":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def predict(self, features: dict[str, float | None]) -> float:
        """KSS 1~9 (경계에서 자른다)."""
        y = self.intercept
        for name, mu, sd, w in zip(self.features, self.mu, self.sd, self.coef):
            v = features.get(name)
            if v is None or not math.isfinite(v):
                continue
            y += w * (v - mu) / sd
        return min(KSS_MAX, max(KSS_MIN, y))

    @staticmethod
    def score(kss: float) -> float:
        """화면·게이트용 0~1 (KSS 1 → 0, 9 → 1)."""
        return (min(KSS_MAX, max(KSS_MIN, kss)) - KSS_MIN) / (KSS_MAX - KSS_MIN)


class PersonalBias:
    """프로필 하나의 보정값. 응답이 올 때마다 (실제 KSS − 공용 예측) 을 더한다."""

    def __init__(self, prior_n: float = 3.0, total: float = 0.0, n: int = 0) -> None:
        self.prior_n, self.total, self.n = prior_n, total, n

    @property
    def offset(self) -> float:
        return self.total / (self.n + self.prior_n)

    def update(self, kss: float, common_prediction: float) -> None:
        self.total += kss - common_prediction
        self.n += 1

    def apply(self, common_prediction: float) -> float:
        return min(KSS_MAX, max(KSS_MIN, common_prediction + self.offset))

    def to_json(self) -> dict[str, float]:
        return {"prior_n": self.prior_n, "total": self.total, "n": self.n}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PersonalBias":
        return cls(float(data.get("prior_n", 3.0)), float(data.get("total", 0.0)), int(data.get("n", 0)))
