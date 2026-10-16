"""Export the selected checkpoint, validate float32 parity, publish a verified bundle."""
from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path
import numpy as np
import yaml
from .train_common import load_dataset
from .frames import CLASSES, FEATURES
from deskmate_hub.personalization.contract import model_metadata
from deskmate_hub.personalization.tflite_backend import TFLiteBackend

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "hub/deskmate_hub/config/tflite_export.yaml"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parity_metrics(reference, converted, elapsed, policy):
    if reference.shape != converted.shape or reference.ndim != 2 or reference.shape[1] != len(CLASSES) or not len(reference):
        raise ValueError("output shape mismatch")
    for values in (reference, converted):
        if not np.isfinite(values).all() or np.any((values < -policy["probability_range_tolerance"]) | (values > 1 + policy["probability_range_tolerance"])):
            raise ValueError(f"invalid output probabilities: min={values.min()}, max={values.max()}, finite={np.isfinite(values).all()}")
        if np.any(np.abs(values.sum(axis=1)-1) > policy["probability_sum_tolerance"]):
            raise ValueError("probability sum mismatch")
    error = np.abs(reference-converted)
    absolute = float(error.max())
    agreement = float(np.mean(reference.argmax(axis=1) == converted.argmax(axis=1)))
    return {"samples": len(reference), "max_absolute_error": absolute, "mean_absolute_error": float(error.mean()),
            "top1_agreement": agreement, "invoke_p50_ms": float(np.percentile(elapsed, 50)),
            "invoke_p95_ms": float(np.percentile(elapsed, 95)),
            "passed": absolute <= policy["max_absolute_error"] and agreement >= policy["min_top1_agreement"]}


def export(selection_report, dataset, output, *, normalization, period=10, policy_path=DEFAULT_POLICY):
    if normalization not in ("linear", "baseline") or not np.isfinite(period) or period <= 0:
        raise ValueError("explicit valid preprocessing annotation required")
    selection = json.loads(Path(selection_report).read_text(encoding="utf-8"))
    if selection.get("selected") not in ("common", "candidate"):
        raise ValueError("invalid selection")
    model_path = Path(selection["selected_model"])
    if digest(model_path) != selection["selected_model_sha256"]:
        raise ValueError("selected checkpoint fingerprint mismatch")
    data = load_dataset(dataset, seed=selection["seed"], validation_fraction=0.25)
    x = data["test"][0]
    width = data["manifest"]["window"]
    with open(policy_path, encoding="utf-8") as source:
        policy = yaml.safe_load(source)
    required = {"max_absolute_error", "min_top1_agreement", "max_model_bytes", "probability_sum_tolerance", "probability_range_tolerance"}
    if set(policy) != required or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or v <= 0 for v in policy.values()):
        raise ValueError("invalid parity policy")
    if policy["min_top1_agreement"] > 1 or type(policy["max_model_bytes"]) is not int:
        raise ValueError("invalid parity limits")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be a fresh directory")
    import tensorflow as tf
    model = tf.keras.models.load_model(model_path, compile=False)
    if model.input_shape != (None, width, len(FEATURES)) or model.output_shape != (None, len(CLASSES)):
        raise ValueError("checkpoint tensor mismatch")
    # Fixed batch one; only builtin ops, no Select TF/Flex dependency.
    # Freeze variables before conversion: TF 2.20/Keras 3 otherwise emits uninitialized READ_VARIABLE ops.
    from tensorflow.python.framework.convert_to_constants import convert_variables_to_constants_v2
    function = tf.function(lambda inputs: model(inputs, training=False),
                           input_signature=[tf.TensorSpec([1, width, len(FEATURES)], tf.float32)])
    frozen = convert_variables_to_constants_v2(function.get_concrete_function())
    converter = tf.lite.TFLiteConverter.from_concrete_functions([frozen])
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS]
    content = converter.convert()
    if len(content) > policy["max_model_bytes"]:
        raise ValueError("TFLite model exceeds size budget")
    metadata = model_metadata(width, period, normalization)
    metadata["model_sha256"] = hashlib.sha256(content).hexdigest()
    reference = model(x, training=False).numpy()
    # Temporary artifacts are not published until every check passes.
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "selected.tflite"
        path.write_bytes(content)
        backend = TFLiteBackend(path, metadata, interpreter_factory=tf.lite.Interpreter)
        backend.predict(x[0])  # Exclude warm-up from latency statistics.
        converted, elapsed = [], []
        for window in x:
            start = time.perf_counter()
            converted.append(backend.predict(window))
            elapsed.append((time.perf_counter()-start)*1000)
        parity = parity_metrics(reference, np.asarray(converted), elapsed, policy)
    if not parity["passed"]:
        raise ValueError(f"numerical parity rejected: {parity}")
    report = {"schema_version": "1.0", "selected": selection["selected"], "source_model_sha256": digest(model_path),
              "selection_report_sha256": digest(selection_report), "dataset_sha256": digest(Path(dataset)/"windows.npz"),
              "tensorflow": tf.__version__, "numpy": np.__version__, "model_bytes": len(content),
              "model_sha256": metadata["model_sha256"], "policy": policy, "parity": parity,
              "preprocessing_annotation": {"normalization": normalization, "frame_period_sec": period,
                                            "source": "explicit CLI annotation; historical dataset lacks capture config"},
              "runtime": "PC TensorFlow Lite Interpreter, single thread", "automatic_deployment": False,
              "limitations": ["PC latency does not validate ATLAS ABI/native runtime or Pi 4 latency.",
                              "Numerical parity does not establish real-user cognitive accuracy."]}
    output.mkdir(parents=True, exist_ok=True)
    (output / "selected.tflite").write_bytes(content)
    (output / "selected.metadata.json").write_text(json.dumps(metadata, indent=2)+"\n", encoding="utf-8")
    (output / "export-report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-report", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--normalization", choices=("linear", "baseline"), required=True)
    parser.add_argument("--period", type=float, default=10)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    report = export(args.selection_report, args.dataset, args.output, normalization=args.normalization,
                    period=args.period, policy_path=args.policy)
    print(json.dumps({"model_bytes": report["model_bytes"], "parity": report["parity"]}))


if __name__ == "__main__":
    main()
