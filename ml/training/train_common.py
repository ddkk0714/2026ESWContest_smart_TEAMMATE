"""Train a small PC-only Conv1D; select on validation, evaluate test once."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import numpy as np
from .frames import CLASSES, FEATURES
from .evidence import predict_logistic, summarize_windows


def group_id(session):
    # Offset variants share one simulated source session.
    return session.rsplit("-o", 1)[0] if session.startswith("synth-") else session


def metrics(truth, prediction):
    matrix = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    for a, b in zip(truth, prediction):
        matrix[int(a), int(b)] += 1
    recall, f1 = {}, {}
    for i, name in enumerate(CLASSES):
        tp, actual, predicted = matrix[i, i], matrix[i].sum(), matrix[:, i].sum()
        recall[name] = float(tp / actual) if actual else None
        f1[name] = float(2 * tp / (actual + predicted)) if actual + predicted else 0.0
    return {"accuracy": float(np.mean(truth == prediction)) if len(truth) else None,
            "macro_f1": float(np.mean(list(f1.values()))) if len(truth) else None,
            "recall": recall, "f1": f1, "confusion": matrix.tolist(), "samples": len(truth)}


def load_dataset(directory, *, seed, validation_fraction):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["features"] != list(FEATURES) or manifest["classes"] != list(CLASSES):
        raise ValueError("dataset feature/class contract mismatch")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation fraction must be in (0,1)")
    with np.load(directory / "windows.npz", allow_pickle=False) as archive:
        train_x, train_y = archive["X_train"], archive["y_train"]
        test_x, test_y = archive["X_test"], archive["y_test"]
        meta = [json.loads(item) for item in archive["meta"]]
    train_meta = [m for m in meta if m["split"] == "train"]
    test_meta = [m for m in meta if m["split"] == "test"]
    if len(train_meta) != len(train_y) or len(test_meta) != len(test_y):
        raise ValueError("metadata length mismatch")
    for x, y in ((train_x, train_y), (test_x, test_y)):
        if x.ndim != 3 or x.shape[1:] != (manifest["window"], len(FEATURES)) or len(x) != len(y):
            raise ValueError("window shape mismatch")
        if not np.isfinite(x).all() or y.ndim != 1 or y.dtype.kind not in "iu" or np.any((y < 0) | (y >= len(CLASSES))):
            raise ValueError("invalid features/labels")
    train_groups = {group_id(m["session_id"]) for m in train_meta}
    test_groups = {group_id(m["session_id"]) for m in test_meta}
    if train_groups & test_groups:
        raise ValueError("train/test source session overlap; rebuild dataset")
    if any(m["augmentation"] != "original" for m in test_meta):
        raise ValueError("test augmentation is forbidden")
    if len(train_groups) < 2 or not test_groups:
        raise ValueError("need at least two train groups and one test group")
    order = np.random.default_rng(seed).permutation(sorted(train_groups))
    count = min(len(order)-1, max(1, round(len(order)*validation_fraction)))
    val_groups = set(order[:count])
    fit_groups = train_groups - val_groups
    fit = np.asarray([group_id(m["session_id"]) in fit_groups for m in train_meta])
    val = np.asarray([group_id(m["session_id"]) in val_groups and m["augmentation"] == "original" for m in train_meta])
    if not fit.any() or not val.any() or not len(test_y):
        raise ValueError("empty train/validation/test")
    test_sources = np.asarray([m["label_source"] for m in test_meta])
    return {"train": (train_x[fit], train_y[fit]), "validation": (train_x[val], train_y[val]),
            "test": (test_x, test_y), "test_sources": test_sources, "manifest": manifest,
            "train_original_counts": np.bincount(train_y[fit & np.asarray([m["augmentation"] == "original" for m in train_meta])], minlength=len(CLASSES)),
            "groups": {"train": sorted(fit_groups), "validation": sorted(val_groups), "test": sorted(test_groups)}}


def build_model(tf, width):
    inputs = tf.keras.Input(shape=(width, len(FEATURES)), dtype="float32", name="features")
    x = tf.keras.layers.Conv1D(16, 3, padding="same", activation="relu", name="conv1")(inputs)
    x = tf.keras.layers.Conv1D(16, 3, padding="same", activation="relu", name="conv2")(x)
    x = tf.keras.layers.GlobalAveragePooling1D(name="pool")(x)
    x = tf.keras.layers.Dense(16, activation="relu", name="embedding")(x)
    outputs = tf.keras.layers.Dense(len(CLASSES), activation="softmax", name="personal_head")(x)
    return tf.keras.Model(inputs, outputs, name="deskmate_common")


def train(directory, output, *, epochs=30, batch_size=64, seed=42, validation_fraction=0.25, learning_rate=0.001):
    if epochs < 1 or batch_size < 1 or not np.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("invalid training options")
    data = load_dataset(directory, seed=seed, validation_fraction=validation_fraction)
    # Import TensorFlow only in PC training; hub has no dependency on it.
    os.environ.setdefault("TF_DETERMINISTIC_OPS", "1")
    import tensorflow as tf
    tf.keras.utils.set_random_seed(seed)
    tf.config.experimental.enable_op_determinism()
    model = build_model(tf, data["manifest"]["window"])
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate), loss="sparse_categorical_crossentropy")
    x, y = data["train"]
    vx, vy = data["validation"]
    tx, ty = data["test"]
    counts = np.bincount(y, minlength=len(CLASSES))
    weights = np.zeros(len(CLASSES), dtype=np.float32)
    present = counts > 0
    weights[present] = len(y) / (present.sum() * counts[present])
    rng = np.random.default_rng(seed)
    best_loss, best_epoch, best_weights = float("inf"), 0, None
    history = []
    for epoch in range(epochs):
        order = rng.permutation(len(y))
        model.reset_metrics()
        for start in range(0, len(y), batch_size):
            indices = order[start:start+batch_size]
            model.train_on_batch(x[indices], y[indices], sample_weight=weights[y[indices]])
        probabilities = model(vx, training=False).numpy()
        loss = float(-np.log(np.clip(probabilities[np.arange(len(vy)), vy], 1e-7, 1)).mean())
        if not np.isfinite(loss):
            raise ValueError("non-finite validation loss")
        history.append({"epoch": epoch+1, "validation_loss": loss})
        print(f"epoch {epoch+1}/{epochs} validation_loss={loss:.6f}", flush=True)
        if loss < best_loss:
            best_loss, best_epoch = loss, epoch+1
            best_weights = model.get_weights()
    model.set_weights(best_weights)
    prediction = model(tx, training=False).numpy().argmax(axis=1)
    baseline = predict_logistic(summarize_windows(x), y, summarize_windows(tx), balanced=True)
    report = {"schema_version": "1.0", "seed": seed, "epochs": epochs, "batch_size": batch_size,
              "learning_rate": learning_rate, "validation_fraction": validation_fraction,
              "selected_epoch": best_epoch, "history": history, "groups": data["groups"],
              "classes": list(CLASSES), "features": list(FEATURES), "parameters": model.count_params(),
              "train_counts": dict(zip(CLASSES, map(int, counts))),
              "tensorflow": tf.__version__, "numpy": np.__version__, "python": platform.python_version(),
              "dataset_sha256": hashlib.sha256((Path(directory)/"windows.npz").read_bytes()).hexdigest(),
              "dataset_manifest": data["manifest"], "cnn": metrics(ty, prediction),
              "baseline_balanced_logistic": metrics(ty, baseline),
              "by_label_source": {source: metrics(ty[data["test_sources"] == source], prediction[data["test_sources"] == source])
                                  for source in ("fsm", "esm")},
              "limitations": ["FSM labels measure teacher agreement, not cognitive accuracy.",
                              "Synthetic sessions do not establish real-user generalization.",
                              "Log groups are file sessions, not verified independent users.",
                              "Missing classes require new data before deployment."]}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    model.save(output / "common.keras")
    report["model_bytes"] = (output / "common.keras").stat().st_size
    report["model_sha256"] = hashlib.sha256((output / "common.keras").read_bytes()).hexdigest()
    (output / "training-report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default="ml/models/common")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    args = parser.parse_args()
    report = train(args.dataset, args.output, epochs=args.epochs, batch_size=args.batch_size,
                   seed=args.seed, validation_fraction=args.validation_fraction, learning_rate=args.learning_rate)
    print(json.dumps({"cnn": report["cnn"], "parameters": report["parameters"]}))


if __name__ == "__main__":
    main()
