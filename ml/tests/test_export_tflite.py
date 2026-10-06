import io
from pathlib import Path
import json
import numpy as np
import pytest
import yaml
from ml.training.export_tflite import DEFAULT_POLICY, digest, export, parity_metrics


def test_parity_acceptance_rejects_changes_and_nonfinite():
    policy = yaml.safe_load(DEFAULT_POLICY.read_text())
    first = np.array([[0.8, 0.1, 0.05, 0.05]])
    assert parity_metrics(first, first, [1], policy)["passed"]
    assert not parity_metrics(first, np.array([[0.1, 0.8, 0.05, 0.05]]), [1], policy)["passed"]
    with pytest.raises(ValueError):
        parity_metrics(first, np.full((1, 4), np.nan), [1], policy)
    with pytest.raises(ValueError):
        parity_metrics(first, np.array([[0.2]*4]), [1], policy)


def test_actual_conversion_hub_and_corruption(tmp_path):
    tf = pytest.importorskip("tensorflow")
    from ml.training.train_common import build_model
    from ml.training.build_dataset import build_dataset
    from deskmate_hub.personalization import load_personalization_config
    from deskmate_hub.personalization.tflite_backend import TFLiteBackend
    from deskmate_hub.personalization.runtime import PersonalizationRuntime
    from deskmate_hub.live import LiveHub
    from deskmate_hub.ingest import SensorCache
    tf.keras.utils.set_random_seed(42)
    model = build_model(tf, 6)
    path = tmp_path / "common.keras"
    model.save(path)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"selected": "common", "selected_model": str(path),
                                     "selected_model_sha256": digest(path), "seed": 42}))
    directory = tmp_path / "data"
    build_dataset(name="export", synthetic="demo", seeds="1-8", offsets="0", augment_count=0, output_dir=directory)
    output = tmp_path / "bundle"
    report = export(selection, directory, output, normalization="baseline")
    assert report["parity"]["passed"] and report["parity"]["top1_agreement"] == 1
    metadata = json.loads((output / "selected.metadata.json").read_text())
    factory = lambda path, meta: TFLiteBackend(path, meta, interpreter_factory=tf.lite.Interpreter)
    cfg = load_personalization_config()
    cfg.update(enabled=True, model_path=str(output / "selected.tflite"), metadata_path=str(output / "selected.metadata.json"))
    normal = LiveHub(SensorCache(), out=io.StringIO(), control_cfg={"enabled": False})
    observed = LiveHub(SensorCache(), out=io.StringIO(), control_cfg={"enabled": False},
                       personalization_cfg=cfg, personalization_backend_factory=factory)
    observed.boot_id = normal.boot_id
    statuses = []
    for now in range(100, 300, 10):
        a, b = normal.tick_once(now), observed.tick_once(now)
        statuses.append(b["data"]["sensor_summary"].pop("personalization")["status"])
        assert a == b
    assert "predicted" in statuses
    assert normal.report_envelope(now=300) == observed.report_envelope(now=300)
    with pytest.raises(ValueError, match="tensor"):
        factory(cfg["model_path"], dict(metadata, input_shape=[1, 7, 17]))
    with pytest.raises(ValueError, match="fresh"):
        export(selection, directory, output, normalization="baseline")
    # Damaged model, with fingerprint check and with actual interpreter parsing.
    Path(cfg["model_path"]).write_bytes(b"corrupt")
    r = PersonalizationRuntime(cfg, period=10, normalization="baseline", backend_factory=factory)
    assert r.status == "unavailable"
    metadata.pop("model_sha256")
    Path(cfg["metadata_path"]).write_text(json.dumps(metadata))
    r = PersonalizationRuntime(cfg, period=10, normalization="baseline", backend_factory=factory)
    assert r.status == "unavailable"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="fingerprint"):
        export(selection, directory, tmp_path / "other", normalization="baseline")
