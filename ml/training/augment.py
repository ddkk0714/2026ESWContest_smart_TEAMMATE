"""센서 가용성 비트를 보존하는 창 단위 증강."""
from __future__ import annotations

import numpy as np

from .frames import FEATURES, SIGNALS

# 센서 측정값이 아닌 칸에는 잡음·오프셋·배율을 넣지 않는다. hub 가 늘 0 으로 두는 phi(호흡·경과),
# 0/1 판정인 호흡 소실 delta, 세션 시계인 경과 delta 를 흔들면 원본에 없는 값이 생긴다(evidence 의 KS 로 드러남).
EXEMPT = ("respiration_phi", "respiration_delta", "elapsed_phi", "elapsed_delta")
_EXEMPT_COLS = [FEATURES.index(name) for name in EXEMPT]
_MEASURED_PHI_DELTA = [c for i in range(len(SIGNALS)) for c in (3 * i, 3 * i + 1) if c not in _EXEMPT_COLS]
_MEASURED_DELTA = [3 * i + 1 for i in range(len(SIGNALS)) if 3 * i + 1 not in _EXEMPT_COLS]

PARAMETERS = {
    "jitter": {"sigma": 0.03},
    "time_stretch": {"ratio_min": 0.8, "ratio_max": 1.25},
    "sensor_dropout": {"signals": list(SIGNALS)},
    "baseline_shift": {"offset_min": -0.1, "offset_max": 0.1},
    "mix_scale": {"factor_min": 0.85, "factor_max": 1.15},
}
KINDS = tuple(PARAMETERS)


def _finish(window: np.ndarray) -> np.ndarray:
    out = np.clip(window, 0, 1).astype(np.float32)
    for signal in range(len(SIGNALS)):
        out[:, 3 * signal:3 * signal + 2] *= out[:, 3 * signal + 2:3 * signal + 3]
    return out


def jitter(window: np.ndarray, rng: np.random.Generator, sigma: float = 0.03) -> np.ndarray:
    out = window.copy()
    out[:, _MEASURED_PHI_DELTA] += rng.normal(0, sigma, size=(len(out), len(_MEASURED_PHI_DELTA)))
    return _finish(out)


def time_stretch(window: np.ndarray, rng: np.random.Generator,
                 ratio_min: float = 0.8, ratio_max: float = 1.25) -> np.ndarray:
    width = len(window)
    ratio = rng.uniform(ratio_min, ratio_max)
    center = (width - 1) / 2
    positions = np.clip(center + (np.arange(width) - center) / ratio, 0, width - 1)
    out = np.stack([np.interp(positions, np.arange(width), window[:, col])
                    for col in range(window.shape[1])], axis=1).astype(np.float32)
    for col in [3 * index + 2 for index in range(len(SIGNALS))] + [15] + _EXEMPT_COLS:
        out[:, col] = window[np.rint(positions).astype(int), col]
    return _finish(out)


def sensor_dropout(window: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = window.copy()
    available = [i for i in range(len(SIGNALS)) if np.any(out[:, 3 * i + 2] > 0)]
    chosen = int(rng.choice(available if available else len(SIGNALS)))
    out[:, 3 * chosen:3 * chosen + 3] = 0
    return _finish(out)


def baseline_shift(window: np.ndarray, rng: np.random.Generator,
                   offset_min: float = -0.1, offset_max: float = 0.1) -> np.ndarray:
    out = window.copy()
    for signal in range(len(SIGNALS)):
        cols = [c for c in (3 * signal, 3 * signal + 1) if c in _MEASURED_PHI_DELTA]
        if cols:
            out[:, cols] += rng.uniform(offset_min, offset_max)
    return _finish(out)


def mix_scale(window: np.ndarray, rng: np.random.Generator,
              factor_min: float = 0.85, factor_max: float = 1.15) -> np.ndarray:
    out = window.copy()
    out[:, _MEASURED_DELTA] *= rng.uniform(factor_min, factor_max)
    return _finish(out)


OPERATIONS = {name: globals()[name] for name in KINDS}
