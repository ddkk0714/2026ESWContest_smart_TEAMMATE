"""PC-only export of initial frozen CNN; all subsequent head learning stays local."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import yaml

from .train_common import load_dataset
from deskmate_hub.personalization.contract import model_metadata
from deskmate_hub.personalization.portable import PortableBackend

POLICY = Path(__file__).resolve().parents[2] / "hub/deskmate_hub/config/portable_export.yaml"


def export(model_path, dataset, output, *, normalization, period=10, policy_path=POLICY):
    if normalization not in ("baseline", "linear") or not np.isfinite(period) or period <= 0:
        raise ValueError("invalid preprocessing")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be fresh")
    with open(policy_path, encoding="utf-8") as source:
        policy = yaml.safe_load(source)
    if (set(policy) != {"max_absolute_error", "min_top1_agreement", "max_model_bytes"}
            or any(type(v) not in (int, float) or not np.isfinite(v) or v <= 0 for v in policy.values())
            or policy["min_top1_agreement"] > 1 or type(policy["max_model_bytes"]) is not int):
        raise ValueError("invalid export policy")
    data = load_dataset(dataset, seed=42, validation_fraction=0.25)
    import tensorflow as tf
    model = tf.keras.models.load_model(model_path, compile=False)
    width = data["manifest"]["window"]
    if (model.input_shape != (None, width, 17) or model.output_shape != (None, 4)
            or [layer.name for layer in model.layers] != ["features", "conv1", "conv2", "pool", "embedding", "personal_head"]):
        raise ValueError("unsupported portable architecture")
    for name in ("conv1", "conv2"):
        layer = model.get_layer(name)
        if (not isinstance(layer, tf.keras.layers.Conv1D) or layer.filters != 16 or layer.kernel_size != (3,)
                or layer.padding != "same" or layer.strides != (1,) or layer.dilation_rate != (1,)
                or layer.groups != 1 or layer.data_format != "channels_last" or layer.activation.__name__ != "relu"):
            raise ValueError("unsupported convolution")
    pool = model.get_layer("pool")
    if not isinstance(pool, tf.keras.layers.GlobalAveragePooling1D) or pool.data_format != "channels_last" or pool.keepdims:
        raise ValueError("unsupported pooling")
    for name, units, activation in (("embedding", 16, "relu"), ("personal_head", 4, "softmax")):
        layer = model.get_layer(name)
        if not isinstance(layer, tf.keras.layers.Dense) or layer.units != units or layer.activation.__name__ != activation:
            raise ValueError("unsupported dense")
    metadata = model_metadata(width, period, normalization)
    layers = {}
    for name in ("conv1", "conv2", "embedding", "personal_head"):
        weights, bias = model.get_layer(name).get_weights()
        layers[name] = {"weights": weights.tolist(), "bias": bias.tolist()}
    content = (json.dumps({"format": "deskmate-portable-cnn/1", "metadata": metadata,
                          "layers": {k: layers[k] for k in ("conv1", "conv2", "embedding")},
                          "head": layers["personal_head"]}, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(content) > policy["max_model_bytes"]:
        raise ValueError("portable size exceeded")
    metadata["model_sha256"] = hashlib.sha256(content).hexdigest()
    # Validate in memory through a local staging file; no public bundle before parity passes.
    import tempfile
    with tempfile.TemporaryDirectory() as staging:
        path = Path(staging) / "common.portable.json"
        path.write_bytes(content)
        backend = PortableBackend(path, metadata)
        x = data["test"][0]
        reference = model(x, training=False).numpy()
        predicted, elapsed = [], []
        for rows in x:
            start = time.perf_counter()
            predicted.append(backend.predict(rows.tolist()))
            elapsed.append((time.perf_counter() - start) * 1000)
        predicted = np.asarray(predicted)
        error = float(np.max(np.abs(reference - predicted)))
        agreement = float(np.mean(reference.argmax(axis=1) == predicted.argmax(axis=1)))
        if error > policy["max_absolute_error"] or agreement < policy["min_top1_agreement"]:
            raise ValueError("portable parity rejected")
    report = {"schema": "deskmate-portable-export/1", "samples": len(x), "model_bytes": len(content),
              "source_sha256": hashlib.sha256(Path(model_path).read_bytes()).hexdigest(),
              "max_absolute_error": error, "top1_agreement": agreement,
              "pc_p50_ms": float(np.percentile(elapsed, 50)), "pc_p95_ms": float(np.percentile(elapsed, 95)),
              "policy": policy, "board_verified": False}
    output.mkdir(parents=True, exist_ok=True)
    (output / "common.portable.json").write_bytes(content)
    (output / "common.metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output / "portable-export-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-model", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--normalization", choices=("linear", "baseline"), required=True)
    parser.add_argument("--period", type=float, default=10)
    args = parser.parse_args()
    print(json.dumps(export(args.common_model, args.dataset, args.output, normalization=args.normalization, period=args.period), indent=2))


if __name__ == "__main__":
    main()
