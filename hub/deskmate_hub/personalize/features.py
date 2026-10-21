"""상황 벡터 — docs/personalization-spec.md §2, 로그 features 원값은 personalization-log.md §2.

`Situation` 은 단위 있는 원값, `vectorize()` 가 밴딧 입력([0,1]·[-1,1])으로 바꾼다.
정규화 상수는 personalize.yaml `features:` 에서만 온다.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

POSTURE_LABELS: tuple[str, ...] = (
    "upright", "lean_forward", "recline", "face_down", "chin_rest",
    "drowsy", "away", "misplaced", "forward_head",
)

FEATURE_NAMES: tuple[str, ...] = (
    "bias",
    *(f"posture_{label}" for label in POSTURE_LABELS),
    "posture_hold", "slump_hold",
    "present", "drowsy", "drowsy_count",
    "co2", "dark", "temp_dev", "humidity_dev",
    "pc_ratio", "typing",
    "c_fatigue", "c_focus",
    "elapsed", "since_last",
    "hour_sin", "hour_cos", "weekend", "after_bedtime",
    "season_sin", "season_cos", "windy", "rain", "outdoor_temp_diff",
)
FEATURE_INDEX = {name: i for i, name in enumerate(FEATURE_NAMES)}
DIM = len(FEATURE_NAMES)


def season_of(month: int) -> str:
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


@dataclass
class Situation:
    """결정 시점의 상황(원값). 없는 값은 None 으로 두고 missing 에 적는다."""
    ts: float                                   # epoch seconds
    hour_local: float                           # 0–23.99
    weekday: int                                # 0=월 … 6=일
    month: int
    posture_dist: dict[str, float] = field(default_factory=dict)
    posture_label: str = "unknown"
    posture_hold_min: float = 0.0
    slump_hold_min: float = 0.0
    present: bool = True
    drowsy: bool = False
    drowsy_count_30min: int = 0
    co2_ppm: float | None = None
    lux: float | None = None
    temp_c: float | None = None
    humidity_pct: float | None = None
    pc_ratio: float = 0.0
    typing_active: bool = False
    c_fatigue: float = 0.0
    c_focus: float = 0.0
    session_elapsed_min: float = 0.0
    since_last_intervention_min: float | None = None
    after_bedtime: bool = False
    weather: dict[str, Any] | None = None       # {windy, rain, pm_bad, outdoor_temp_c}

    @property
    def season(self) -> str:
        return season_of(self.month)

    def missing(self) -> list[str]:
        keys = []
        for key in ("co2_ppm", "lux", "temp_c", "humidity_pct", "weather"):
            if getattr(self, key) is None:
                keys.append(key)
        if not self.posture_dist:
            keys.append("posture_dist")
        return keys

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("ts")
        data["posture_dist"] = {label: float(self.posture_dist.get(label, 0.0)) for label in POSTURE_LABELS}
        data["season"] = self.season
        data["missing"] = self.missing()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], ts: float = 0.0) -> "Situation":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "ts"}
        return cls(ts=ts, **known)


def _ramp(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return float(min(1.0, max(0.0, (value - low) / (high - low))))


def _comfort_deviation(value: float, lo: float, hi: float, margin: float) -> float:
    if value < lo:
        return _ramp(lo - value, 0.0, margin)
    if value > hi:
        return _ramp(value - hi, 0.0, margin)
    return 0.0


def vectorize(sit: Situation, fcfg: dict[str, Any]) -> np.ndarray:
    """Situation → 길이 DIM 의 float 벡터. 결측은 0."""
    x = np.zeros(DIM, dtype=float)
    x[FEATURE_INDEX["bias"]] = 1.0
    total = sum(sit.posture_dist.values()) or 1.0
    for label in POSTURE_LABELS:
        x[FEATURE_INDEX[f"posture_{label}"]] = float(sit.posture_dist.get(label, 0.0)) / total
    hold_full = float(fcfg["posture_hold_full_min"])
    x[FEATURE_INDEX["posture_hold"]] = _ramp(sit.posture_hold_min, 0.0, hold_full)
    x[FEATURE_INDEX["slump_hold"]] = _ramp(sit.slump_hold_min, 0.0, hold_full)
    x[FEATURE_INDEX["present"]] = 1.0 if sit.present else 0.0
    x[FEATURE_INDEX["drowsy"]] = 1.0 if sit.drowsy else 0.0
    x[FEATURE_INDEX["drowsy_count"]] = _ramp(sit.drowsy_count_30min, 0.0, float(fcfg["drowsy_count_full"]))
    if sit.co2_ppm is not None:
        x[FEATURE_INDEX["co2"]] = _ramp(sit.co2_ppm, float(fcfg["co2_base_ppm"]), float(fcfg["co2_full_ppm"]))
    if sit.lux is not None:
        x[FEATURE_INDEX["dark"]] = 1.0 - _ramp(sit.lux, float(fcfg["lux_dim_low"]), float(fcfg["lux_dim_high"]))
    if sit.temp_c is not None:
        x[FEATURE_INDEX["temp_dev"]] = _comfort_deviation(
            sit.temp_c, fcfg["temp_comfort_min"], fcfg["temp_comfort_max"], fcfg["temp_margin"])
    if sit.humidity_pct is not None:
        x[FEATURE_INDEX["humidity_dev"]] = _comfort_deviation(
            sit.humidity_pct, fcfg["humidity_comfort_min"], fcfg["humidity_comfort_max"], fcfg["humidity_margin"])
    x[FEATURE_INDEX["pc_ratio"]] = float(min(1.0, max(0.0, sit.pc_ratio)))
    x[FEATURE_INDEX["typing"]] = 1.0 if sit.typing_active else 0.0
    x[FEATURE_INDEX["c_fatigue"]] = float(sit.c_fatigue)
    x[FEATURE_INDEX["c_focus"]] = float(sit.c_focus)
    x[FEATURE_INDEX["elapsed"]] = _ramp(sit.session_elapsed_min, 0.0, float(fcfg["session_full_min"]))
    since = sit.since_last_intervention_min
    x[FEATURE_INDEX["since_last"]] = 1.0 if since is None else _ramp(since, 0.0, float(fcfg["since_last_full_min"]))
    angle = 2.0 * math.pi * sit.hour_local / 24.0
    x[FEATURE_INDEX["hour_sin"]] = math.sin(angle)
    x[FEATURE_INDEX["hour_cos"]] = math.cos(angle)
    x[FEATURE_INDEX["weekend"]] = 1.0 if sit.weekday >= 5 else 0.0
    x[FEATURE_INDEX["after_bedtime"]] = 1.0 if sit.after_bedtime else 0.0
    s_angle = 2.0 * math.pi * (sit.month - 1) / 12.0
    x[FEATURE_INDEX["season_sin"]] = math.sin(s_angle)
    x[FEATURE_INDEX["season_cos"]] = math.cos(s_angle)
    if sit.weather:
        x[FEATURE_INDEX["windy"]] = 1.0 if sit.weather.get("windy") else 0.0
        x[FEATURE_INDEX["rain"]] = 1.0 if sit.weather.get("rain") else 0.0
        outdoor = sit.weather.get("outdoor_temp_c")
        if outdoor is not None and sit.temp_c is not None:
            diff = (float(outdoor) - float(sit.temp_c)) / float(fcfg["outdoor_temp_diff_full_c"])
            x[FEATURE_INDEX["outdoor_temp_diff"]] = float(max(-1.0, min(1.0, diff)))
    return x


def prior_vector(spec: dict[str, float] | None) -> np.ndarray:
    """YAML 의 {특징이름: 가중치} → 길이 DIM 벡터. 모르는 이름은 오류."""
    theta = np.zeros(DIM, dtype=float)
    for name, weight in (spec or {}).items():
        if name not in FEATURE_INDEX:
            raise KeyError(f"prior 에 모르는 특징 이름: {name}")
        theta[FEATURE_INDEX[name]] = float(weight)
    return theta
