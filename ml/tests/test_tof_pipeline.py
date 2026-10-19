"""Synthetic feature messages exercise PC training and the portable board path."""
import json

import numpy as np
import pytest

from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import MqttLineSource
from deskmate_hub.inference import State
from deskmate_hub.live import frame_to_dict
from ml.training.build_dataset import build_dataset


def logs(directory):
    directory.mkdir()
    cfg = load_ingest_config()
    cfg["tof"]["enabled"] = True
    for session in range(4):
        cache, tracker = SensorCache(), SessionTracker()
        records, states = [], []
        for tick in range(24):
            now = 1000 + session * 1000 + tick * 10
            label = tick // 6
            payload = {"ts": now, "data": {
                "present": label != 3, "valid": True,
                "posture": "away" if label == 3 else "lean_forward" if label == 1 else "upright",
                "motion_score": 0.1, "baseline_deviation": 0.9 if label == 1 else 0.1,
                "posture_confidence": 0.9, "baseline_ready": True,
            }}
            assert MqttLineSource(cache).feed_line(
                "MQTT\tdeskmate/sensor/tof/synthetic\t" + json.dumps(payload), now=now) == "tof"
            result = build_frame(cache.snapshot(), now, cfg, tracker, fsm_state=State.FOCUS_PC)
            records.append({**frame_to_dict(result), "feature_profile": "tof_features_v1"})
            states.append({"data": {"phase": ("focus", "fatigue", "rest", "idle")[label]}})
        (directory / f"frames-{session}.jsonl").write_text(
            "\n".join(map(json.dumps, records)) + "\n", encoding="utf-8")
        (directory / f"state-{session}.jsonl").write_text(
            "\n".join(map(json.dumps, states)) + "\n", encoding="utf-8")


def test_tof_profile_is_preserved_and_mixed_sources_rejected(tmp_path):
    source = tmp_path / "logs"
    logs(source)
    manifest = build_dataset(name="tof", synthetic="", logs=source,
                             augment_count=0, output_dir=tmp_path / "dataset")
    assert manifest["feature_profile"] == "tof_features_v1"
    path = source / "frames-0.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0].pop("feature_profile")
    path.write_text("\n".join(map(json.dumps, records)) + "\n")
    with pytest.raises(ValueError, match="mixed feature profiles"):
        build_dataset(name="mixed", synthetic="", logs=source,
                      augment_count=0, output_dir=tmp_path / "mixed")


def test_actual_pc_training_export_and_local_head_update(tmp_path):
    tf = pytest.importorskip("tensorflow")
    from ml.training.train_common import train, load_dataset
    from ml.training.export_portable import export
    from deskmate_hub.personalization.runtime import PersonalizationRuntime
    from deskmate_hub.personalization import load_personalization_config

    source = tmp_path / "logs"
    logs(source)
    dataset, model_dir, bundle = (tmp_path / name for name in ("dataset", "model", "bundle"))
    build_dataset(name="tof", synthetic="", logs=source, augment_count=0, output_dir=dataset)
    report = train(dataset, model_dir, epochs=1, batch_size=16)
    assert report["dataset_manifest"]["feature_profile"] == "tof_features_v1"
    parity = export(model_dir / "common.keras", dataset, bundle, normalization="baseline")
    assert parity["max_absolute_error"] < 1e-5

    cfg = load_personalization_config()
    cfg.update(enabled=True, backend="portable_cnn", feature_profile="tof_features_v1",
               model_path=str(bundle / "common.portable.json"),
               metadata_path=str(bundle / "common.metadata.json"))
    runtime = PersonalizationRuntime(cfg, period=10, normalization="baseline")
    assert runtime.status == "warming_up"
    backend = runtime.backend
    x = load_dataset(dataset, seed=42, validation_fraction=0.25)["test"][0][0]
    model = tf.keras.models.load_model(model_dir / "common.keras", compile=False)
    np.testing.assert_allclose(backend.predict(x.tolist()), model(x[None], training=False).numpy()[0], atol=1e-6)
    frozen, original = json.dumps(backend.layers), backend.common_head.record()
    candidate = backend.head.copy()
    label = int(np.argmin(backend.predict(x.tolist())))
    candidate.update(backend.last_embedding, label, rate=0.01, l2=0.001)
    assert candidate.record() != original
    assert json.dumps(backend.layers) == frozen and backend.common_head.record() == original
