"""센서 가용성 비트를 보존하는 창 단위 증강."""
from __future__ import annotations

import numpy as np

from .frames import SIGNALS

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
    for signal in range(len(SIGNALS)):
        out[:, 3 * signal:3 * signal + 2] += rng.normal(0, sigma, size=(len(out), 2))
    return _finish(out)


def time_stretch(window: np.ndarray, rng: np.random.Generator,
                 ratio_min: float = 0.8, ratio_max: float = 1.25) -> np.ndarray:
    width = len(window)
    ratio = rng.uniform(ratio_min, ratio_max)
    center = (width - 1) / 2
    positions = np.clip(center + (np.arange(width) - center) / ratio, 0, width - 1)
    out = np.stack([np.interp(positions, np.arange(width), window[:, col])
                    for col in range(window.shape[1])], axis=1).astype(np.float32)
    for col in [3 * index + 2 for index in range(len(SIGNALS))] + [15]:
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
        out[:, 3 * signal:3 * signal + 2] += rng.uniform(offset_min, offset_max)
    return _finish(out)


def mix_scale(window: np.ndarray, rng: np.random.Generator,
              factor_min: float = 0.85, factor_max: float = 1.15) -> np.ndarray:
    out = window.copy()
    out[:, [3 * i + 1 for i in range(len(SIGNALS))]] *= rng.uniform(factor_min, factor_max)
    return _finish(out)


OPERATIONS = {name: globals()[name] for name in KINDS}
