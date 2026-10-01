"""증강은 창 모양과 센서 가용성 비트를 유지한다."""
from __future__ import annotations

import numpy as np
import pytest

from ml.training import augment


def sample():
    window = np.full((6, 17), 0.5, np.float32)
    window[:, [2, 5, 8, 11, 14]] = 1
    window[:, 15] = 1
    window[:, 3:6] = 0  # 원래 결측인 자세는 증강해도 0
    return window


@pytest.mark.parametrize("name", augment.KINDS)
def test_each_augmentation_reproducible_and_preserves_mask(name):
    original = sample()
    operation = augment.OPERATIONS[name]
    first = operation(original, np.random.default_rng(7))
    second = operation(original, np.random.default_rng(7))
    assert first.shape == original.shape and first.dtype == np.float32
    assert np.array_equal(first, second) and np.array_equal(original, sample())
    assert np.all((first >= 0) & (first <= 1))
    assert np.all(first[:, 3:6] == 0)
    assert set(np.unique(first[:, [2, 5, 8, 11, 14]])) <= {0.0, 1.0}


def test_dropout_turns_off_exactly_one_present_signal():
    original = sample()
    changed = augment.sensor_dropout(original, np.random.default_rng(3))
    before = original[:, [2, 5, 8, 11, 14]].sum(axis=0)
    after = changed[:, [2, 5, 8, 11, 14]].sum(axis=0)
    assert np.sum((before > 0) & (after == 0)) == 1
    assert np.array_equal(original[:, 15:], changed[:, 15:])


@pytest.mark.parametrize("name", ["jitter", "baseline_shift", "mix_scale", "time_stretch"])
def test_non_sensor_columns_are_not_perturbed(name):
    # 호흡·경과의 phi(늘 0)와 호흡 소실 0/1·경과 시계는 흔들지 않는다 — 원본에 없는 값이 생기면 안 된다.
    from ml.training.frames import FEATURES

    window = sample()
    cols = [FEATURES.index(c) for c in augment.EXEMPT]
    window[:, cols] = 0
    window[:, FEATURES.index("respiration_delta")] = [0, 0, 1, 1, 1, 0]
    window[:, FEATURES.index("elapsed_delta")] = np.linspace(0.1, 0.2, 6)
    changed = augment.OPERATIONS[name](window, np.random.default_rng(11))
    phi_cols = [FEATURES.index("respiration_phi"), FEATURES.index("elapsed_phi")]
    assert np.all(changed[:, phi_cols] == 0)
    assert set(np.unique(changed[:, FEATURES.index("respiration_delta")])) <= {0.0, 1.0}
    if name != "time_stretch":
        assert np.allclose(changed[:, cols], window[:, cols])
