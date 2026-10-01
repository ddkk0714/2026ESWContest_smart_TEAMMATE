"""세션 분할·NPZ 계약·증거 리포트를 작은 합성 세션으로 확인한다."""
from __future__ import annotations

import json

import numpy as np

from ml.training.build_dataset import build_dataset, split_sessions
from ml.training.evidence import generate_evidence


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
