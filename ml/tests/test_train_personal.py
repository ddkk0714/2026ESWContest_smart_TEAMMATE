import json
from pathlib import Path
import numpy as np
import pytest
import yaml
from ml.training.train_personal import DEFAULT_POLICY, adapt, compare, digest, eligibility
from ml.training.train_common import load_dataset, metrics
from ml.training.build_dataset import build_dataset
from ml.training.frames import CLASSES, FEATURES


def policy():
    return yaml.safe_load(DEFAULT_POLICY.read_text())


def personal_data(tmp_path):
    build_dataset(name="personal", synthetic="default,short,demo", seeds="101-108", offsets="0", augment_count=1, output_dir=tmp_path)
    return load_dataset(tmp_path, seed=42, validation_fraction=0.25)


def test_eligibility_missing_class_and_overlap(tmp_path):
    data = personal_data(tmp_path)
    assert eligibility(data, {"groups": {"train": ["synth-demo-s1"]}}, policy(), True) == []
    data["train"] = (data["train"][0], np.zeros(len(data["train"][1]), dtype=int))
    data["train_original_counts"] = np.array([1000, 0, 0, 0])
    assert "insufficient_train_class_coverage" in eligibility(data, {"groups": {}}, policy(), True)
    with pytest.raises(ValueError, match="overlaps"):
        eligibility(data, {"groups": data["groups"]}, policy(), True)
    with pytest.raises(ValueError, match="authorized local logs"):
        eligibility(data, {"groups": {}}, policy(), False)


def test_real_esm_eligibility_is_original_only(tmp_path):
    data = personal_data(tmp_path)
    data["manifest"]["materials"] = [{"source": "log"}]
    data["train_esm_originals"] = 0
    assert "insufficient_real_esm" in eligibility(data, {"groups": {}}, policy(), False)
    with pytest.raises(ValueError, match="real logs"):
        eligibility(data, {"groups": {}}, policy(), True)


def test_comparison_regression_ties_and_improvement():
    common = {"macro_f1": 0.7, "recall": dict.fromkeys(CLASSES, 0.7)}
    candidate = {"macro_f1": 0.8, "recall": dict.fromkeys(CLASSES, 0.8)}
    assert compare(common, candidate, policy()) == []
    assert "macro_f1_not_improved" in compare(common, common, policy())
    candidate["recall"]["rest"] = 0.6
    assert "recall_regression_rest" in compare(common, candidate, policy())
    candidate["recall"]["rest"] = None
    assert "missing_eval_class" in compare(common, candidate, policy())


def test_insufficient_data_falls_back_without_candidate(tmp_path):
    directory = tmp_path / "data"
    build_dataset(name="small", synthetic="demo", seeds="101-108", offsets="0", augment_count=0, output_dir=directory)
    model = tmp_path / "common.keras"
    model.write_bytes(b"not loaded when ineligible")
    common_report = tmp_path / "common-report.json"
    common_report.write_text(json.dumps({"model_sha256": digest(model), "features": list(FEATURES),
                                         "classes": list(CLASSES), "groups": {}}))
    strict = policy()
    strict["min_train_per_class"] = 100000
    policy_path = tmp_path / "strict.yaml"
    policy_path.write_text(yaml.safe_dump(strict))
    output = tmp_path / "output"
    report = adapt(directory, model, common_report, output, synthetic_demo=True, policy_path=policy_path)
    assert report["selected"] == "common" and "insufficient_train_class_coverage" in report["reasons"]
    assert not (output / "candidate.keras").exists()
    with pytest.raises(ValueError, match="empty"):
        adapt(directory, model, common_report, output, synthetic_demo=True, policy_path=policy_path)
    model.write_bytes(b"modified")
    with pytest.raises(ValueError, match="fingerprint"):
        adapt(directory, model, common_report, tmp_path / "second", synthetic_demo=True)
    with pytest.raises(ValueError, match="explicitly"):
        adapt(directory, model, common_report, tmp_path / "third")


def test_actual_head_only_training_and_fallback(tmp_path):
    tf = pytest.importorskip("tensorflow")
    from ml.training.train_common import train
    common_dir, personal_dir = tmp_path / "common-data", tmp_path / "personal-data"
    build_dataset(name="common", synthetic="default,short,demo", seeds="1-8", offsets="0", augment_count=0, output_dir=common_dir)
    personal_data(personal_dir)
    common_output = tmp_path / "common"
    train(common_dir, common_output, epochs=2, batch_size=128)
    source = common_output / "common.keras"
    original_hash = digest(source)
    report = adapt(personal_dir, source, common_output / "training-report.json", tmp_path / "personal",
                   synthetic_demo=True, epochs=3, batch_size=128)
    assert report["backbone_before"] == report["backbone_after"]
    assert report["head_changed"] and report["trainable_parameters"] == 68
    assert digest(source) == original_hash
    common = tf.keras.models.load_model(source, compile=False)
    candidate = tf.keras.models.load_model(tmp_path / "personal/candidate.keras", compile=False)
    for layer in common.layers:
        if layer.name != "personal_head":
            for before, after in zip(layer.get_weights(), candidate.get_layer(layer.name).get_weights()):
                np.testing.assert_array_equal(before, after)
    x, y = load_dataset(personal_dir, seed=42, validation_fraction=0.25)["test"]
    prediction = candidate(x, training=False).numpy().argmax(axis=1)
    assert metrics(y, prediction) == report["candidate_test"]
    # Require a full 1.0 macro-F1 gain: candidate must be rejected, never promoted.
    strict = policy()
    strict["min_macro_f1_gain"] = 1.0
    path = tmp_path / "strict.yaml"
    path.write_text(yaml.safe_dump(strict))
    fallback = adapt(personal_dir, source, common_output / "training-report.json", tmp_path / "fallback",
                     synthetic_demo=True, epochs=1, batch_size=128, policy_path=path)
    assert fallback["selected"] == "common"
    assert "test_macro_f1_not_improved" in fallback["reasons"]
