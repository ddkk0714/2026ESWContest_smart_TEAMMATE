"""tick 특징·phase·ESM 정정과 전이 trace 복원을 검증한다."""
from __future__ import annotations

import numpy as np

from ml.training.frames import (
    CLASS_INDEX, FEATURES, feature_vector, label_ticks, phase_label, phases_from_trace,
    state_phases, windows,
)


def test_feature_order_and_missing_signal():
    frame = {"present": True, "pc_ratio": 0.25,
             "signals": {"keystroke": {"phi": 0.8, "delta": 0.4, "available": True},
                         "posture": {"phi": 0.9, "delta": 0.7, "available": False}}}
    vector = feature_vector(frame)
    assert len(FEATURES) == 17 and vector.dtype == np.float32
    assert FEATURES[:6] == ("keystroke_phi", "keystroke_delta", "keystroke_available",
                             "posture_phi", "posture_delta", "posture_available")
    assert np.allclose(vector[:6], [0.8, 0.4, 1, 0, 0, 0])
    assert np.allclose(vector[-2:], [1, 0.25])


def test_phase_mapping_and_unknown_count():
    for phase, expected in (("focus", "focus"), ("fatigue", "fatigue"), ("recovery", "rest"),
                            ("rest", "rest"), ("idle", "idle"), ("start", "idle"), ("end", "idle")):
        assert phase_label(phase) == CLASS_INDEX[expected]
    ticks = [{"now": 1}, {"now": 2}]
    labels, sources, unknown = label_ticks(ticks, ["focus", "mystery"])
    assert labels == [0, None] and sources == ["fsm", "fsm"] and unknown == {"mystery": 1}


def test_esm_correction_overrides_only_target_ticks():
    ticks = [{"now": 1}, {"now": 2}, {"now": 3}]
    esm = [{"verdict": "correct", "corrected_state": "REST",
            "target_window_start_ms": 1500, "target_window_end_ms": 2500}]
    labels, sources, _ = label_ticks(ticks, ["focus"] * 3, esm)
    assert labels == [0, CLASS_INDEX["rest"], 0]
    assert sources == ["fsm", "esm", "fsm"]
    x, y, meta = windows(ticks, labels, sources, session_id="one", width=2)
    assert x.shape == (2, 2, 17) and y.tolist() == [2, 0]
    assert meta[0]["label_source"] == "esm" and meta[0]["start_tick"] == 0


def test_trace_forward_fill_and_state_alignment():
    ticks = [{"now": 100}, {"now": 110}, {"now": 120}]
    trace = [{"t": 0, "state": "FOCUS_PC"}, {"t": 20, "state": "REST"}]
    assert phases_from_trace(ticks, trace, origin=100) == ["focus", "focus", "recovery"]
    assert state_phases([{"data": {"phase": p}} for p in ("focus", "fatigue", "recovery")], 3) == [
        "focus", "fatigue", "recovery"]
