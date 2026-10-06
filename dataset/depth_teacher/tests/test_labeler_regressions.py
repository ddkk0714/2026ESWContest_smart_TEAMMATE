"""카메라 없이 결측·깊은 졸음·불완전한 기준선 오류를 재현한다."""
from pathlib import Path
import sys

import numpy as np
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from posture.geometry import FEATURES
from posture.labeler import Calibrator, DrowsinessDetector, PostureLabeler, build_baseline


@pytest.fixture
def cfg():
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml")
                          .read_text(encoding="utf-8"))


@pytest.fixture
def baseline():
    f = dict.fromkeys(FEATURES, 0.0)
    f.update(head_dist=0.8, sh_width=0.4, hand_face_dist=1.0)
    return f


@pytest.mark.parametrize("invalid", [True, False])
def test_tracking_gap_does_not_count_as_posture_duration(cfg, baseline, invalid):
    labeler = PostureLabeler(cfg, baseline)
    bad = dict(baseline, head_fwd=0.1)
    assert labeler.update(0, bad)["forward_head"] == 0
    if invalid:
        assert labeler.update(0.1, None)["posture"] == "invalid"
    assert labeler.update(10, bad)["forward_head"] == 0
    for t in np.arange(10.1, 11.21, 0.1):
        result = labeler.update(float(t), bad)
    assert result["forward_head"] == 1


def test_tracking_loss_discards_latched_posture_and_stale_samples(cfg, baseline):
    labeler = PostureLabeler(cfg, baseline)
    for t in np.arange(0, 1.21, 0.1):
        result = labeler.update(float(t), dict(baseline, head_fwd=0.1))
    assert result["forward_head"] == 1
    labeler.update(1.3, None)
    assert labeler.update(1.4, baseline)["posture"] == "normal"


def two_nods(cfg):
    det = DrowsinessDetector(cfg)
    for t, pitch in [(0, 0), (0.5, 20), (1, 0), (2, 0), (2.5, 20), (3, 0)]:
        result = det.update(t, pitch, 0, 0.8, 0, 1)
    assert result["nods_window"] == 2
    assert result["drowsy"] == 1
    return det


def test_deep_sleep_survives_nod_timeout_then_expires_on_recovery(cfg):
    det = two_nods(cfg)
    for t in np.arange(3.1, 30.01, 0.1):
        assert det.update(float(t), 20, 0, 0.8, 0, 1)["drowsy"] == 1
    for t in np.arange(30.1, 37.01, 0.1):
        result = det.update(float(t), 0, 0, 0.8, 0, 1)
    assert result["drowsy"] == 0


def test_static_head_down_without_nods_never_starts_sleep(cfg):
    det = DrowsinessDetector(cfg)
    for t in np.arange(0, 30.01, 0.1):
        assert det.update(float(t), 20, 0, 0.8, 0, 1)["drowsy"] == 0


@pytest.mark.parametrize("change", ["body", "chin_rest", "leaning"])
def test_posture_change_ends_sleep_hold(cfg, change):
    det = two_nods(cfg)
    for t in np.arange(3.1, 12.01, 0.1):
        result = det.update(float(t), 20, 0, 0.8, 0, 1)
    assert result["drowsy"] == 1
    for t in np.arange(12.1, 20.01, 0.1):
        result = det.update(float(t), 20, 0, 0.8, 0.1 if change == "body" else 0, 1,
                            chin_rest=change == "chin_rest", leaning=change == "leaning")
    assert result["drowsy"] == 0


def test_calibration_retries_missing_head_baseline(cfg, baseline):
    calib = Calibrator(cfg)
    missing = dict(baseline, head_pitch=np.nan, nose_drop=np.nan)
    for t in np.arange(0, 14.1, 0.1):
        assert calib.add(float(t), missing) is None
    for t in np.arange(14.1, 23.1, 0.1):
        result = calib.add(float(t), baseline)
        if result is not None:
            break
    assert result is not None
    labeler = PostureLabeler(cfg, result)
    for t in np.arange(24, 26.1, 0.1):
        labels = labeler.update(float(t), dict(baseline, head_pitch=30, nose_drop=0.05))
    assert labels["head_down"] == 1


def test_baseline_requires_feature_coverage_not_just_one_measurement(cfg, baseline):
    missing = dict(baseline, head_pitch=np.nan, nose_drop=np.nan)
    assert build_baseline(cfg, [missing] * 9 + [baseline]) is None
    assert build_baseline(cfg, [baseline] + [None] * 9) is None


def test_nose_baseline_fallback_remains_supported(cfg, baseline):
    partial = dict(baseline, head_pitch=np.nan)
    base = build_baseline(cfg, [partial] * 10)
    assert base is not None
    labeler = PostureLabeler(cfg, base)
    for t in np.arange(0, 2.1, 0.1):
        labels = labeler.update(float(t), dict(baseline, nose_drop=0.05))
    assert labels["head_down"] == 1


def test_reject_old_unusable_baseline(cfg, baseline):
    with pytest.raises(ValueError, match="고개 기준"):
        PostureLabeler(cfg, dict(baseline, head_pitch=np.nan, nose_drop=np.nan))
