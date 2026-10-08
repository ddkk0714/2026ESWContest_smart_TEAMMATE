import json
import numpy as np
import pytest
from ml.training.build_dataset import build_dataset
from ml.training.train_common import group_id, load_dataset, metrics


def dataset(tmp_path):
    build_dataset(name="cnn", synthetic="demo", seeds="1-8", offsets="0,3", augment_count=1, output_dir=tmp_path)
    return tmp_path


def test_source_group_split_and_original_validation(tmp_path):
    data = load_dataset(dataset(tmp_path), seed=42, validation_fraction=0.25)
    groups = data["groups"]
    assert not set(groups["train"]) & set(groups["validation"])
    assert not set(groups["train"]) & set(groups["test"])
    assert not set(groups["validation"]) & set(groups["test"])
    repeated = load_dataset(tmp_path, seed=42, validation_fraction=0.25)
    assert repeated["groups"] == groups
    manifest = data["manifest"]
    assert not {group_id(s) for s in manifest["sessions"]["train"]} & {group_id(s) for s in manifest["sessions"]["test"]}
    with np.load(tmp_path / "windows.npz") as archive:
        metadata = [json.loads(s) for s in archive["meta"]]
    expected = sum(group_id(m["session_id"]) in groups["validation"] and m["augmentation"] == "original" for m in metadata)
    assert len(data["validation"][1]) == expected


@pytest.mark.parametrize("failure", ["overlap", "feature_order", "nan", "test_augmentation"])
def test_invalid_dataset_rejected(tmp_path, failure):
    dataset(tmp_path)
    path = tmp_path / "windows.npz"
    with np.load(path) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    meta = [json.loads(s) for s in arrays["meta"]]
    if failure == "overlap":
        train_session = next(m["session_id"] for m in meta if m["split"] == "train")
        next(m for m in meta if m["split"] == "test")["session_id"] = train_session
    elif failure == "test_augmentation":
        next(m for m in meta if m["split"] == "test")["augmentation"] = "noise"
    elif failure == "nan":
        arrays["X_train"][0, 0, 0] = np.nan
    else:
        p = tmp_path / "manifest.json"
        manifest = json.loads(p.read_text())
        manifest["features"].reverse()
        p.write_text(json.dumps(manifest))
    arrays["meta"] = np.asarray([json.dumps(m) for m in meta])
    np.savez_compressed(path, **arrays)
    with pytest.raises(ValueError):
        load_dataset(tmp_path, seed=42, validation_fraction=0.25)


def test_metrics_macro_f1_and_missing_source():
    result = metrics(np.array([0, 0, 1, 2]), np.array([0, 1, 1, 2]))
    assert result["accuracy"] == 0.75
    assert result["recall"]["idle"] is None
    assert result["macro_f1"] == pytest.approx((2/3 + 2/3 + 1 + 0)/4)
    assert metrics(np.array([], dtype=int), np.array([], dtype=int))["macro_f1"] is None


def test_actual_training_checkpoint_roundtrip(tmp_path):
    tf = pytest.importorskip("tensorflow")
    from ml.training.train_common import train
    directory = dataset(tmp_path / "dataset")
    output = tmp_path / "model"
    report = train(directory, output, epochs=2, batch_size=64)
    model = tf.keras.models.load_model(output / "common.keras")
    probabilities = model(np.zeros((1, 6, 17), dtype=np.float32), training=False).numpy()
    assert probabilities.shape == (1, 4)
    assert probabilities.sum() == pytest.approx(1)
    assert report["selected_epoch"] in (1, 2)
    assert report["parameters"] < 10000
    assert report["cnn"]["samples"] > 0
    assert report["by_label_source"]["esm"]["samples"] == 0
