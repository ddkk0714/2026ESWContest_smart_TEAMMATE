"""Shared training/runtime contract; no third-party imports."""
import math

SIGNALS = ("keystroke", "posture", "respiration", "environment", "elapsed")
FEATURES = tuple(f"{s}_{f}" for s in SIGNALS for f in ("phi", "delta", "available")) + ("present", "pc_ratio")
CLASSES = ("focus", "fatigue", "rest", "idle")
VERSION = "1.0"


def feature_values(frame):
    values = []
    for name in SIGNALS:
        signal = (frame.get("signals") or {}).get(name) or {}
        valid = bool(signal.get("available", False))
        values.extend((float(signal.get("phi") or 0) if valid else 0.0,
                       float(signal.get("delta") or 0) if valid else 0.0, float(valid)))
    values.extend((float(bool(frame.get("present", False))), float(frame.get("pc_ratio") or 0)))
    if not all(math.isfinite(v) for v in values):
        raise ValueError("non-finite feature")
    return values


def model_metadata(window, period, normalization):
    return {"contract_version": VERSION, "features": list(FEATURES), "classes": list(CLASSES),
            "input_shape": [1, window, len(FEATURES)], "output_shape": [1, len(CLASSES)],
            "dtype": "float32", "frame_period_sec": period, "normalization": normalization}
