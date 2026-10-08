"""Explicit lab startup check: synthetic inputs, no personal records or writes."""
import json

from .runtime import PersonalizationRuntime


def run_self_check(model_config, training_config, *, period, normalization):
    if model_config.get("backend") != "portable_cnn":
        return {"status": "not_run", "reason": "portable_backend_required"}
    try:
        runtime = PersonalizationRuntime(dict(model_config, enabled=True, mode="observe"),
                                         period=period, normalization=normalization)
        if runtime.backend is None:
            return {"status": "failed", "reason": "common_model_unavailable"}
        frozen = json.dumps(runtime.backend.layers)
        common = json.dumps(runtime.backend.common_head.record())
        for tick in range(model_config["window_ticks"]):
            result = runtime.observe({"now": tick * period, "present": False, "pc_ratio": 0.0, "signals": {}})
        if result["status"] != "predicted":
            return {"status": "failed", "reason": "common_inference_failed"}
        candidate = runtime.backend.common_head.copy()
        for label in range(4):
            candidate.update([float(i == label) for i in range(16)], label,
                             rate=training_config["learning_rate"], l2=training_config["l2"])
        changed = json.dumps(candidate.record()) != common
        unchanged = json.dumps(runtime.backend.layers) == frozen and json.dumps(runtime.backend.common_head.record()) == common
        return {"status": "passed" if changed and unchanged else "failed", "synthetic": True,
                "persistent_writes": False, "head_update_verified": changed, "backbone_unchanged": unchanged,
                "inference_ms": result["inference_ms"], "head_parameters": 68}
    except Exception as exc:
        # A diagnostic failure cannot prevent the real hub from starting.
        return {"status": "failed", "reason": type(exc).__name__}
