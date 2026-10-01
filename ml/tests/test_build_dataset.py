"""세션 분할·NPZ 계약·증거 리포트를 작은 합성 세션으로 확인한다."""
from __future__ import annotations

import json

import numpy as np

from ml.training.build_dataset import build_dataset, split_sessions
from ml.training.evidence import generate_evidence
from ml.training import augment


def test_session_split_has_no_overlap():
    train, test = split_sessions(["a", "b", "c", "d"], eval_fraction=0.25, seed=5)
    assert not train & test and train | test == {"a", "b", "c", "d"}
    assert (train, test) == split_sessions(["a", "b", "c", "d"], eval_fraction=0.25, seed=5)
    assert split_sessions(["a", "b"], eval_fraction=0, seed=5)[1] == set()


def test_small_synthetic_dataset_and_evidence(tmp_path):
    output = tmp_path / "dataset"
    manifest = build_dataset(name="tiny", synthetic="demo", seeds="1-2", offsets="0",
                             augment_count=1, output_dir=output)
    assert manifest["sessions"]["total"] == 2
    assert len(manifest["sessions"]["train"]) == len(manifest["sessions"]["test"]) == 1
    assert len(manifest["materials"]) == 2 and all(len(m["sha256"]) == 64 for m in manifest["materials"])
    saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert saved["features"] == manifest["features"] and len(saved["features"]) == 17
    with np.load(output / "windows.npz", allow_pickle=False) as data:
        assert set(data.files) == {"X_train", "y_train", "X_test", "y_test", "meta"}
        assert data["X_train"].shape[1:] == (6, 17)
        assert len(data["X_train"]) == 2 * sum(manifest["counts"]["train_original"].values())
        assert len(data["X_test"]) == sum(manifest["counts"]["test"].values())
        meta = [json.loads(item) for item in data["meta"]]
        train_ids = {item["session_id"] for item in meta if item["split"] == "train"}
        test_ids = {item["session_id"] for item in meta if item["split"] == "test"}
        assert not train_ids & test_ids
        assert all(item["augmentation"] == "original" for item in meta if item["split"] == "test")
    report = tmp_path / "evidence.md"
    analysis = generate_evidence(output, report)
    text = report.read_text(encoding="utf-8")
    assert all(section in text for section in ("데이터 규모", "증강 방법", "분포 비교", "기준 분류기", "한계"))
    assert "nearest_centroid_original" in analysis["classifiers"]
    assert "softmax_logistic_balanced_augmented" in analysis["classifiers"]
    # 결측·개인차 내성: 깨끗한 평가와 세 가지 교란 조건을 두 학습 방식으로 비교한다
    assert "결측·개인차 내성" in text and "해석:" in text
    assert set(analysis["robustness"]) == {"clean", "sensor_dropout", "baseline_shift", "mix_scale"}
    assert all(set(v) == {"original", "augmented"} for v in analysis["robustness"].values())
    assert saved["augmentation"]["exempt_from_noise"] == list(augment.EXEMPT)


def test_real_frame_state_pair_and_esm_correction(tmp_path):
    logs = tmp_path / "inputs"
    logs.mkdir()
    for stamp, start in (("a", 100), ("b", 200)):
        frame_lines = [json.dumps({"now": start + tick, "present": True, "pc_ratio": 0.5,
                                   "signals": {}}) for tick in range(4)]
        state_lines = [json.dumps({"data": {"phase": "focus"}}) for _ in range(4)]
        (logs / f"frames-{stamp}.jsonl").write_text("\n".join(frame_lines) + "\n", encoding="utf-8")
        (logs / f"state-{stamp}.jsonl").write_text("\n".join(state_lines) + "\n", encoding="utf-8")
    esm = logs / "esm-test.jsonl"
    esm.write_text(json.dumps({"verdict": "correct", "corrected_state": "FATIGUE",
                               "target_window_start_ms": 101000, "target_window_end_ms": 102000}) + "\n",
                   encoding="utf-8")
    manifest = build_dataset(name="logs", synthetic="", logs=str(logs), esm=str(esm),
                             window=2, augment_count=0, output_dir=tmp_path / "output")
    assert manifest["sessions"]["total"] == 2
    assert manifest["counts"]["label_source"]["esm"] > 0
    assert manifest["counts"]["train_augmented"] == {name: 0 for name in manifest["classes"]}


def test_balanced_logistic_recovers_a_rare_class():
    from ml.training.evidence import predict_logistic

    rng = np.random.default_rng(0)
    common = rng.normal(0, 1, size=(400, 2))
    rare = rng.normal([1.2, 1.2], 0.5, size=(12, 2))
    x = np.vstack([common, rare])
    y = np.array([0] * 400 + [2] * 12)
    test = rng.normal([1.2, 1.2], 0.5, size=(40, 2))
    plain = np.mean(predict_logistic(x, y, test) == 2)
    balanced = np.mean(predict_logistic(x, y, test, balanced=True) == 2)
    assert balanced > plain

