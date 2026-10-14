"""PC-only frozen-backbone adaptation, with explicit eligibility/fallback."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import yaml
from .frames import CLASSES, FEATURES
from .train_common import load_dataset, metrics, group_id

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "hub/deskmate_hub/config/personal_head.yaml"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def weight_hash(layers):
    h = hashlib.sha256()
    for layer in layers:
        h.update(layer.name.encode())
        for weight in layer.get_weights():
            h.update(str(weight.shape).encode())
            h.update(weight.dtype.str.encode())
            h.update(weight.tobytes())
    return h.hexdigest()


def eligibility(data, common_report, policy, synthetic_demo):
    reasons = []
    used = {g for values in common_report["groups"].values() for g in values}
    personal = {g for values in data["groups"].values() for g in values}
    if used & personal:
        raise ValueError("personal data overlaps common model train/validation/test")
    sources = {m["source"] for m in data["manifest"]["materials"] if m["source"] != "esm"}
    if synthetic_demo and sources != {"synthetic"}:
        raise ValueError("synthetic demo cannot consume real logs")
    if not synthetic_demo and sources != {"log"}:
        raise ValueError("real adaptation requires only authorized local logs")
    for split in ("train", "validation", "test"):
        if len(data["groups"][split]) < policy[f"min_{split}_groups"]:
            reasons.append(f"insufficient_{split}_groups")
        counts = data["train_original_counts"] if split == "train" else np.bincount(data[split][1], minlength=len(CLASSES))
        if np.any(counts < policy[f"min_{split}_per_class"]):
            reasons.append(f"insufficient_{split}_class_coverage")
    if not synthetic_demo:
        if data["train_esm_originals"] < policy["min_real_esm_train"]:
            reasons.append("insufficient_real_esm")
    return reasons


def compare(common, candidate, policy):
    reasons = []
    if candidate["macro_f1"] - common["macro_f1"] <= policy["min_macro_f1_gain"]:
        reasons.append("macro_f1_not_improved")
    for name in CLASSES:
        if common["recall"][name] is None or candidate["recall"][name] is None:
            reasons.append("missing_eval_class")
        elif common["recall"][name] - candidate["recall"][name] > policy["max_class_recall_regression"]:
            reasons.append(f"recall_regression_{name}")
    return reasons


def adapt(directory, common_model, common_report_path, output, *, synthetic_demo=False,
          authorized_local_data=False, epochs=20, batch_size=64, seed=42,
          validation_fraction=0.25, learning_rate=0.001, policy_path=DEFAULT_POLICY):
    if not synthetic_demo and not authorized_local_data:
        raise ValueError("authorized local data must be explicitly supplied")
    if epochs < 1 or batch_size < 1 or not np.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("invalid training options")
    with open(policy_path, encoding="utf-8") as source:
        policy = yaml.safe_load(source)
    for key, value in policy.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0:
            raise ValueError("invalid policy")
    required = {"min_train_groups", "min_validation_groups", "min_test_groups",
                "min_train_per_class", "min_validation_per_class", "min_test_per_class",
                "min_real_esm_train", "min_macro_f1_gain", "max_class_recall_regression"}
    if set(policy) != required:
        raise ValueError("incomplete policy")
    for key in required - {"min_macro_f1_gain", "max_class_recall_regression"}:
        if type(policy[key]) is not int or policy[key] < 1:
            raise ValueError("sample/group limits must be positive integers")
    if policy["max_class_recall_regression"] > 1 or policy["min_macro_f1_gain"] > 1:
        raise ValueError("metric limits must not exceed one")
    data = load_dataset(directory, seed=seed, validation_fraction=validation_fraction)
    # Count only original corrections from fit groups, never augmented copies.
    with np.load(Path(directory) / "windows.npz", allow_pickle=False) as source:
        meta = [json.loads(s) for s in source["meta"]]
    data["train_esm_originals"] = sum(m["split"] == "train" and m["augmentation"] == "original"
                                     and m["label_source"] == "esm" and group_id(m["session_id"]) in data["groups"]["train"] for m in meta)
    common_report = json.loads(Path(common_report_path).read_text(encoding="utf-8"))
    model_hash = digest(common_model)
    if common_report.get("model_sha256") != model_hash:
        raise ValueError("common checkpoint/report fingerprint mismatch")
    if common_report["features"] != list(FEATURES) or common_report["classes"] != list(CLASSES):
        raise ValueError("common contract mismatch")
    reasons = eligibility(data, common_report, policy, synthetic_demo)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Never accidentally reuse a stale candidate in a fallback run.
    if any(output.iterdir()):
        raise ValueError("output must be empty; choose a fresh run directory")
    report = {"schema_version": "1.0", "mode": "synthetic_demo" if synthetic_demo else "authorized_local",
              "selected": "common", "reasons": reasons, "groups": data["groups"], "policy": policy,
              "dataset_manifest": data["manifest"],
              "seed": seed, "epochs": epochs, "batch_size": batch_size, "learning_rate": learning_rate,
              "common_model_sha256": model_hash, "dataset_sha256": digest(Path(directory)/"windows.npz"),
              "common_report_sha256": digest(common_report_path), "train_esm_originals": data["train_esm_originals"],
              "limitations": ["Synthetic/FSM agreement is not real-user cognitive accuracy.",
                              "File sessions do not establish independent users.",
                              "This result never activates or deploys a hub model."]}
    if not reasons:
        os.environ.setdefault("TF_DETERMINISTIC_OPS", "1")
        import tensorflow as tf
        tf.keras.utils.set_random_seed(seed)
        tf.config.experimental.enable_op_determinism()
        model = tf.keras.models.load_model(common_model, compile=False)
        width = data["manifest"]["window"]
        if model.input_shape != (None, width, len(FEATURES)) or model.output_shape != (None, len(CLASSES)):
            raise ValueError("common tensor shape mismatch")
        head = model.get_layer("personal_head")
        if model.layers[-1] is not head or head.activation.__name__ != "softmax":
            raise ValueError("expected final softmax personal_head")
        backbone = [layer for layer in model.layers if layer is not head]
        before = weight_hash(backbone)
        head_before = weight_hash([head])
        tx, ty = data["test"]
        vx, vy = data["validation"]
        x, y = data["train"]
        common_prediction = model(tx, training=False).numpy().argmax(axis=1)
        common_test = metrics(ty, common_prediction)
        common_val = metrics(vy, model(vx, training=False).numpy().argmax(axis=1))
        for layer in model.layers:
            layer.trainable = layer is head
        model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate), loss="sparse_categorical_crossentropy")
        counts = np.bincount(y, minlength=len(CLASSES))
        weights = len(y) / (len(CLASSES) * counts)
        best, chosen, saved = float("inf"), 0, None
        rng = np.random.default_rng(seed)
        history = []
        for epoch in range(epochs):
            order = rng.permutation(len(y))
            model.reset_metrics()
            for start in range(0, len(y), batch_size):
                ix = order[start:start+batch_size]
                model.train_on_batch(x[ix], y[ix], sample_weight=weights[y[ix]])
            probabilities = model(vx, training=False).numpy()
            loss = float(-np.log(np.clip(probabilities[np.arange(len(vy)), vy], 1e-7, 1)).mean())
            if not np.isfinite(loss):
                raise ValueError("non-finite validation loss")
            history.append({"epoch": epoch+1, "validation_loss": loss})
            if loss < best:
                best, chosen, saved = loss, epoch+1, head.get_weights()
            print(f"personal epoch {epoch+1}/{epochs} validation_loss={loss:.6f}", flush=True)
        head.set_weights(saved)
        after = weight_hash(backbone)
        if before != after:
            raise RuntimeError("frozen backbone changed")
        candidate_val = metrics(vy, model(vx, training=False).numpy().argmax(axis=1))
        candidate_prediction = model(tx, training=False).numpy().argmax(axis=1)
        candidate_test = metrics(ty, candidate_prediction)
        report.update(backbone_before=before, backbone_after=after, head_changed=head_before != weight_hash([head]),
                      common_validation=common_val, candidate_validation=candidate_val,
                      common_test=common_test, candidate_test=candidate_test, selected_epoch=chosen, history=history,
                      tensorflow=tf.__version__, numpy=np.__version__,
                      trainable_parameters=sum(int(np.prod(w.shape)) for w in model.trainable_weights))
        report["by_label_source"] = {source: {"common": metrics(ty[data["test_sources"] == source], common_prediction[data["test_sources"] == source]),
                                              "candidate": metrics(ty[data["test_sources"] == source], candidate_prediction[data["test_sources"] == source])}
                                     for source in ("fsm", "esm")}
        report["reasons"] = ["validation_"+s for s in compare(common_val, candidate_val, policy)]
        report["reasons"] += ["test_"+s for s in compare(common_test, candidate_test, policy)]
        if not report["head_changed"]:
            report["reasons"].append("head_unchanged")
        report["selected"] = "common" if report["reasons"] else "candidate"
        model.save(output / "candidate.keras")
        report["candidate_sha256"] = digest(output / "candidate.keras")
    report["selected_model"] = str((Path(common_model) if report["selected"] == "common" else output / "candidate.keras").resolve())
    report["selected_model_sha256"] = digest(report["selected_model"])
    report["automatic_deployment"] = False
    (output / "personal-report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--common-model", required=True)
    parser.add_argument("--common-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--synthetic-demo", action="store_true")
    parser.add_argument("--authorized-local-data", action="store_true")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    report = adapt(args.dataset, args.common_model, args.common_report, args.output,
                   synthetic_demo=args.synthetic_demo, authorized_local_data=args.authorized_local_data,
                   epochs=args.epochs, batch_size=args.batch_size, seed=args.seed, learning_rate=args.learning_rate,
                   validation_fraction=args.validation_fraction, policy_path=args.policy)
    print(json.dumps({"selected": report["selected"], "reasons": report["reasons"]}))


if __name__ == "__main__":
    main()
