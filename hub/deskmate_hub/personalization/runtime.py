"""Observe-only inference. Failures latch until restart; never modify FSM."""
import json
import math
import time
from collections import deque

from .contract import CLASSES, feature_values, model_metadata


class PersonalizationRuntime:
    def __init__(self, config, *, period, normalization, backend_factory=None):
        self.config = config
        self.backend = None
        self.rows = deque()
        self.last_now = None
        self.status = "disabled"
        self.expected = None
        if not config.get("enabled"):
            return
        try:
            if config.get("mode") != "observe":
                raise ValueError("observe mode required")
            width = config["window_ticks"]
            if type(width) is not int or width < 1:
                raise ValueError("invalid window")
            for key in ("max_period_jitter_sec", "max_inference_ms", "probability_sum_tolerance", "probability_range_tolerance"):
                if not math.isfinite(float(config[key])) or float(config[key]) <= 0:
                    raise ValueError("invalid limits")
            self.expected = model_metadata(width, period, normalization)
            self.rows = deque(maxlen=width)
            with open(config["metadata_path"], encoding="utf-8") as source:
                metadata = json.load(source)
            if any(metadata.get(k) != v for k, v in self.expected.items()):
                raise ValueError("metadata contract mismatch")
            # Check the file before importing any optional native dependency.
            with open(config["model_path"], "rb") as source:
                if not source.read(1):
                    raise ValueError("empty model")
            if backend_factory is None:
                from .tflite_backend import TFLiteBackend
                backend_factory = TFLiteBackend
            self.backend = backend_factory(config["model_path"], metadata)
            self.status = "warming_up"
        except Exception:
            self.status = "unavailable"

    def observe(self, frame, *, reset=False):
        base = {"mode": "observe", "status": self.status, "decision_source": "fsm"}
        if self.backend is None:
            return base
        try:
            now = float(frame["now"])
            if not math.isfinite(now):
                raise ValueError("invalid timestamp")
            delta = None if self.last_now is None else now - self.last_now
            if reset or (delta is not None and (delta <= 0 or abs(delta - self.expected["frame_period_sec"]) > self.config["max_period_jitter_sec"])):
                self.rows.clear()
            self.last_now = now
            self.rows.append(feature_values(frame))
            if len(self.rows) < self.rows.maxlen:
                self.status = "warming_up"
                return dict(base, status=self.status, window_ready=len(self.rows))
            start = time.perf_counter()
            probabilities = [float(p) for p in self.backend.predict(list(self.rows))]
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > self.config["max_inference_ms"]:
                raise ValueError("inference exceeded budget")
            tolerance = self.config["probability_range_tolerance"]
            if len(probabilities) != len(CLASSES) or any(not math.isfinite(p) or not -tolerance <= p <= 1 + tolerance for p in probabilities):
                raise ValueError("invalid probabilities")
            if abs(sum(probabilities) - 1) > self.config["probability_sum_tolerance"]:
                raise ValueError("probability sum mismatch")
            probabilities = [min(1.0, max(0.0, p)) for p in probabilities]
            index = max(range(len(CLASSES)), key=probabilities.__getitem__)
            self.status = "predicted"
            return dict(base, status=self.status, label=CLASSES[index], confidence=probabilities[index],
                        probabilities=dict(zip(CLASSES, probabilities)), inference_ms=elapsed)
        except Exception:
            self.rows.clear()
            self.last_now = None
            self.backend = None
            self.status = "unavailable"
            return dict(base, status=self.status)
