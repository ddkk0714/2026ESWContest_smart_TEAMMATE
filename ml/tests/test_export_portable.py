import json

import numpy as np
import pytest


def test_actual_keras_export_and_full_head_training_parity(tmp_path):
    tf = pytest.importorskip("tensorflow")
    from ml.training.build_dataset import build_dataset
    from ml.training.train_common import build_model
    from ml.training.export_portable import export
    from deskmate_hub.personalization.portable import PortableBackend
    tf.keras.utils.set_random_seed(42)
    model = build_model(tf, 6)
    path = tmp_path / "common.keras"
    model.save(path)
    dataset = tmp_path / "dataset"
    build_dataset(name="portable", synthetic="demo", seeds="1-8", offsets="0", augment_count=0, output_dir=dataset)
    report = export(path, dataset, tmp_path / "bundle", normalization="baseline")
    assert report["max_absolute_error"] < 1e-5 and report["top1_agreement"] == 1
    assert not report["board_verified"]
    metadata = json.loads((tmp_path / "bundle/common.metadata.json").read_text())
    backend = PortableBackend(tmp_path / "bundle/common.portable.json", metadata)
    embedding_model = tf.keras.Model(model.input, model.get_layer("embedding").output)
    x = np.random.default_rng(11).uniform(-0.5, 1, (6, 17)).astype(np.float32)
    np.testing.assert_allclose(backend.embed(x.tolist()), embedding_model(x[None], training=False).numpy()[0], atol=1e-6)
    np.testing.assert_allclose(backend.predict(x.tolist()), model(x[None], training=False).numpy()[0], atol=1e-6)
    with pytest.raises(ValueError, match="fresh"):
        export(path, dataset, tmp_path / "bundle", normalization="baseline")
    model.get_layer("conv1").activation = tf.keras.activations.sigmoid
    model.save(path)
    with pytest.raises(ValueError, match="convolution"):
        export(path, dataset, tmp_path / "bad", normalization="baseline")
    assert not (tmp_path / "bad").exists()
