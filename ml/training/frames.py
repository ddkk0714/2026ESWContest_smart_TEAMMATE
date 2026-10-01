"""SensorFrame·state 로그를 같은 tick 으로 맞춰 17개 특징과 약한 라벨을 만든다."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
from deskmate_hub.inference import State  # noqa: E402
from deskmate_hub.presentation import _PHASE_BY_STATE  # noqa: E402

SIGNALS = ("keystroke", "posture", "respiration", "environment", "elapsed")
FEATURES = tuple(f"{signal}_{field}" for signal in SIGNALS for field in ("phi", "delta", "available")) + (
    "present", "pc_ratio",
)
CLASSES = ("focus", "fatigue", "rest", "idle")
CLASS_INDEX = {name: index for index, name in enumerate(CLASSES)}
PHASE_CLASS = {"focus": "focus", "fatigue": "fatigue", "recovery": "rest", "rest": "rest",
               "idle": "idle", "start": "idle", "end": "idle"}
CORRECTED_CLASS = {"FOCUS_PC": "focus", "FATIGUE": "fatigue", "REST": "rest", "IDLE": "idle"}


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip() and not line.lstrip().startswith("#")]


def feature_vector(frame: dict) -> np.ndarray:
    values = []
    for name in SIGNALS:
        signal = (frame.get("signals") or {}).get(name) or {}
        valid = bool(signal.get("available", False))
        values.extend((float(signal.get("phi") or 0) if valid else 0.0,
                       float(signal.get("delta") or 0) if valid else 0.0, float(valid)))
    values.extend((float(bool(frame.get("present", False))), float(frame.get("pc_ratio") or 0)))
    return np.asarray(values, dtype=np.float32)


def phase_label(phase: str | None) -> int | None:
    name = PHASE_CLASS.get(phase or "")
    return CLASS_INDEX[name] if name is not None else None


def phases_from_trace(frames: list[dict], trace: list[dict], *, origin: float) -> list[str | None]:
    """trace 는 전이 tick 만 있으므로 각 프레임 직전의 마지막 상태를 유지한다."""
    events = sorted(trace, key=lambda item: item["t"])
    phases = []
    index = 0
    state = None
    for frame in frames:
        elapsed = float(frame["now"]) - origin
        while index < len(events) and float(events[index]["t"]) <= elapsed:
            state = events[index]["state"]
            index += 1
        try:
            phases.append(_PHASE_BY_STATE[State(state)] if state is not None else None)
        except ValueError:
            phases.append(None)
    return phases


def state_phases(states: list[dict], count: int) -> list[str | None]:
    if len(states) != count:
        raise ValueError(f"frame/state tick 수 불일치: {count}/{len(states)}")
    return [(state.get("data") or {}).get("phase") for state in states]


def label_ticks(frames: list[dict], phases: list[str | None], esm: list[dict] = ()) -> tuple[list[int | None], list[str], dict]:
    if len(frames) != len(phases):
        raise ValueError("frame/phase tick 수 불일치")
    labels = [phase_label(phase) for phase in phases]
    sources = ["fsm"] * len(frames)
    unknown = dict(Counter(str(phase) for phase, label in zip(phases, labels) if label is None))
    for record in esm:
        if record.get("verdict") != "correct" or record.get("corrected_state") not in CORRECTED_CLASS:
            continue
        start, end = record.get("target_window_start_ms"), record.get("target_window_end_ms")
        if start is None or end is None:
            continue
        target = CLASS_INDEX[CORRECTED_CLASS[record["corrected_state"]]]
        for index, frame in enumerate(frames):
            tick_ms = float(frame["now"]) * 1000
            if start <= tick_ms <= end:
                labels[index], sources[index] = target, "esm"
    return labels, sources, unknown


def windows(frames: list[dict], labels: list[int | None], sources: list[str], *, session_id: str,
            width: int = 6, stride: int = 1) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    if width < 1 or stride < 1:
        raise ValueError("window/stride 는 양수여야 합니다")
    vectors = [feature_vector(frame) for frame in frames]
    xs, ys, meta = [], [], []
    for start in range(0, len(frames) - width + 1, stride):
        if any(label is None for label in labels[start:start + width]):
            continue
        xs.append(np.stack(vectors[start:start + width]))
        ys.append(labels[start + width - 1])
        meta.append({"session_id": session_id, "start_tick": start,
                     "label_source": sources[start + width - 1], "augmentation": "original"})
    return (np.stack(xs).astype(np.float32) if xs else np.empty((0, width, len(FEATURES)), np.float32),
            np.asarray(ys, dtype=np.int64), meta)
