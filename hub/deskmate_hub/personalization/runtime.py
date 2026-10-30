"""Observe-only inference. Failures latch until restart; never modify FSM."""
import json
import math
import time
from collections import deque

from .assets import read_asset
from .contract import CLASSES, feature_values, model_metadata


class PersonalizationRuntime:
    def __init__(self, config, *, period, normalization, backend_factory=None):
        self.config = config
        self.backend = None
        self.rows = deque()
        self.last_now = None
        self.status = "disabled"
        self.reason = None          # unavailable 일 때 왜인지(화면·로그용). 2026-10-26: demo-b 에서 "왜 꺼졌나" 를 바로 읽을 수 있게
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
            # 파일 경로 또는 패키지 안 경로(보드 payload) — assets.read_asset
            metadata = json.loads(read_asset(config["metadata_path"]).decode("utf-8"))
            mismatch = [f"{k}: {metadata.get(k)!r} vs {v!r}" for k, v in self.expected.items() if metadata.get(k) != v]
            if mismatch:
                # 예: normalization: 'baseline' vs 'linear' — demo-b(선형 정규화)에서는 이 모델(baseline 학습)을 못 쓴다
                raise ValueError("metadata contract mismatch (" + ", ".join(mismatch) + ")")
            # Check the file before importing any optional native dependency.
            if not read_asset(config["model_path"])[:1]:
                raise ValueError("empty model")
            if backend_factory is None:
                backend_name = config.get("backend", "tflite")
                if backend_name == "portable_cnn":
                    from .portable import PortableBackend
                    backend_factory = PortableBackend
                elif backend_name == "tflite":
                    from .tflite_backend import TFLiteBackend
                    backend_factory = TFLiteBackend
                else:
                    raise ValueError("unsupported backend")
            self.backend = backend_factory(config["model_path"], metadata)
            self.status = "warming_up"
        except Exception as exc:
            self.status = "unavailable"
            self.reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

    def _base(self):
        # kind: 이 블록은 2단계 피로 관찰 CNN(stage2, FSM·개입 불변)이다 — 밴딧(개입 선택)은 sensor_summary.personalize 에 따로 실린다
        base = {"kind": "stage2_observe", "mode": "observe", "status": self.status, "decision_source": "fsm"}
        if self.status == "unavailable" and self.reason:
            base["reason"] = self.reason
        return base

    def observe(self, frame, *, reset=False):
        base = self._base()
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
        except Exception as exc:
            self.rows.clear()
            self.last_now = None
            self.backend = None
            self.status = "unavailable"
            self.reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return self._base()
