"""export_mlp_json.py: numpy 학습 가중치(.npz) → JSON → 허브 순수 Python 추론이 같은 답을 내는지.

Pi 4 에는 numpy 가 없으므로 (1) 특징 벡터와 (2) 순전파 확률을 양쪽에서 따로 계산해 비교한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "hub"))

from export_mlp_json import compare, npz_to_spec, numpy_forward  # noqa: E402
from train_mlp import FAR_MM, features as np_features  # noqa: E402

from deskmate_hub.posture.tof_mlp import TofMlpModel  # noqa: E402

SIM_NPZ = HERE / "models" / "mlp_sim60.npz"
HUB_JSON = REPO / "hub" / "deskmate_hub" / "config" / "tof_mlp.json"


def random_npz(tmp_path, n_in=48, n_hidden=5, n_out=4, seed=3):
    r = np.random.default_rng(seed)
    path = tmp_path / "toy.npz"
    np.savez(path, mu=r.normal(size=n_in).astype(np.float32), sd=(r.uniform(0.5, 2, n_in)).astype(np.float32),
             classes=np.array([f"c{i}" for i in range(n_out)]),
             W1=r.normal(size=(n_in, n_hidden)), b1=r.normal(size=n_hidden),
             W2=r.normal(size=(n_hidden, n_out)), b2=r.normal(size=n_out))
    return np.load(path)


def test_exported_json_matches_numpy_forward(tmp_path):
    npz = random_npz(tmp_path)
    spec = npz_to_spec(npz, source="toy", grid=(4, 4))
    assert spec["n_in"] == 48 and spec["grid"] == {"width": 4, "height": 4} and spec["classes"] == ["c0", "c1", "c2", "c3"]
    assert compare(npz, spec, n=200) < 1e-5
    # JSON 왕복 뒤에도 같다
    spec2 = json.loads(json.dumps(spec))
    assert compare(npz, spec2, n=50) < 1e-5
    # 16 zone 은 정사각이라 --grid 없이도 4×4 로 잡는다
    assert npz_to_spec(npz, source="toy", grid=None)["grid"] == {"width": 4, "height": 4}


def test_non_square_zone_count_requires_grid(tmp_path):
    npz = random_npz(tmp_path, n_in=3 * 128)
    with pytest.raises(ValueError):
        npz_to_spec(npz, source="toy", grid=None)
    assert npz_to_spec(npz, source="toy", grid=(16, 8))["grid"] == {"width": 16, "height": 8}
    with pytest.raises(ValueError):
        npz_to_spec(npz, source="toy", grid=(8, 8))


def test_pure_python_features_equal_training_features(tmp_path):
    """허브 features() 는 train_mlp.features 와 같은 벡터를 내야 한다(원본 d·st → valid·FAR·기준선 변화)."""
    npz = random_npz(tmp_path, n_in=3 * 64)
    model = TofMlpModel(npz_to_spec(npz, source="toy", grid=(8, 8)))
    r = np.random.default_rng(1)
    raw = r.integers(-1, 2500, size=(10, 64)).astype(np.int16)
    st = r.choice([5, 9, 6, 10, 0, 255, 4], size=(10, 64)).astype(np.uint8)
    valid = np.isin(st, (5, 9, 6, 10)) & (raw >= 0)
    dist = np.where(valid, raw.astype(np.float32), np.nan)
    base = np.where(r.random(64) < 0.8, r.uniform(400, 1200, 64), np.nan).astype(np.float32)
    ref = np_features(dist.reshape(10, 8, 8), base.reshape(8, 8))
    baseline = [None if np.isnan(b) else float(b) for b in base]
    for i in range(10):
        got = model.features([int(v) for v in raw[i]], [int(v) for v in st[i]], baseline)
        assert np.allclose(got, ref[i], atol=1e-6)
    # 기준선 없음 = FAR (변화 −5 → 클립 −3), 기준선과 같으면 0
    assert FAR_MM == model.far_mm and model.features([500] * 64, [5] * 64, None)[64] == -3.0
    assert model.features([1700] * 64, [5] * 64, None)[64] == pytest.approx(-1.0)


@pytest.mark.skipif(not SIM_NPZ.exists() or not HUB_JSON.exists(), reason="시뮬 가중치 또는 허브 JSON 없음")
def test_committed_hub_json_is_the_sim60_export():
    """hub/deskmate_hub/config/tof_mlp.json 이 models/mlp_sim60.npz 와 같은 모델인지(재생성 누락 방지)."""
    npz = np.load(SIM_NPZ)
    spec = json.loads(HUB_JSON.read_text(encoding="utf-8"))
    assert spec["source"].endswith("models/mlp_sim60.npz") and spec["classes"] == [str(c) for c in npz["classes"]]
    assert compare(npz, spec, n=100) < 1e-4
    model = TofMlpModel(spec)
    X = (npz["mu"] + npz["sd"] * np.random.default_rng(5).normal(size=(20, 192))).astype(np.float32)
    ref = numpy_forward(npz, X)
    for i in range(20):
        assert np.abs(np.asarray(model.forward([float(v) for v in X[i]])) - ref[i]).max() < 1e-4
