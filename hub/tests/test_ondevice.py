import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from deskmate_hub.personalization.contract import model_metadata
from deskmate_hub.personalization.portable import Head, PortableBackend
from deskmate_hub.personalization.ondevice import OnDeviceLearner, load_ondevice_config, metrics
from deskmate_hub.personalization.privacy import PersonalizationPrivacy


def bundle(tmp_path):
    metadata = model_metadata(6, 10, "baseline")
    layers = {}
    for name, channels in (("conv1", 17), ("conv2", 16)):
        layers[name] = {"weights": [[[0.0] * 16 for _ in range(channels)] for _ in range(3)], "bias": [0.0] * 16}
    layers["embedding"] = {"weights": [[0.0] * 16 for _ in range(16)], "bias": [0.0] * 16}
    path = tmp_path / "common.json"
    path.write_text(json.dumps({"format": "deskmate-portable-cnn/1", "metadata": metadata,
                                "layers": layers, "head": {"weights": [[0.0] * 4 for _ in range(16)], "bias": [0.0] * 4}}))
    metadata["model_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, metadata


def learner(tmp_path, *, consent=True):
    path, metadata = bundle(tmp_path)
    cfg = load_ondevice_config()
    cfg.update(enabled=True, head_path=str(tmp_path / "head.json"), epochs=30, learning_rate=0.3, updates_per_tick=16)
    privacy = PersonalizationPrivacy({"enabled": True, "policy_approved": True, "policy_version": "lab-1",
                                     "data_root": str(tmp_path), "consent_file": str(tmp_path / "consent.json"),
                                     "personal_model_files": [cfg["head_path"], cfg["head_path"] + ".tmp"]})
    privacy.consented = consent
    result = OnDeviceLearner(cfg, privacy)
    backend = PortableBackend(path, metadata)
    runtime = SimpleNamespace(backend=backend, status="predicted")
    backend.last_embedding = [1.0] + [0.0] * 15
    result.observe(runtime, "FOCUS_PC", 0)
    return result, runtime, privacy


def samples(result):
    for session in range(4):
        for label in range(4):
            for repetition in range(2):
                result.samples.append({"tick": session * 100 + label * 10 + repetition,
                                       "session": session, "label": label,
                                       "embedding": [float(i == label) for i in range(16)]})


def finish(result):
    for _ in range(200):
        result.advance("END")
        if result.status in ("accepted", "rejected", "storage_error"):
            return
    pytest.fail("bounded learner did not finish")


def test_local_learning_preserves_backbone_and_common_then_restores(tmp_path):
    result, runtime, privacy = learner(tmp_path)
    samples(result)
    original_common = runtime.backend.common_head.record()
    frozen = json.dumps(runtime.backend.layers)
    finish(result)
    assert result.status == "accepted"
    assert metrics(runtime.backend.head, list(result.samples))["macro_f1"] == 1
    assert runtime.backend.common_head.record() == original_common
    assert json.dumps(runtime.backend.layers) == frozen
    saved = json.loads((tmp_path / "head.json").read_text())
    assert set(saved) == {"schema", "backbone_sha256", "head"}
    restarted = OnDeviceLearner(result.config, privacy)
    new_backend = PortableBackend(tmp_path / "common.json", model_metadata_with_hash(tmp_path))
    new_backend.last_embedding = [0.0] * 16
    restarted.observe(SimpleNamespace(backend=new_backend, status="predicted"), "IDLE", 0)
    assert restarted.head_source == "personal"
    assert new_backend.head.record() == saved["head"]


def model_metadata_with_hash(tmp_path):
    metadata = model_metadata(6, 10, "baseline")
    metadata["model_sha256"] = hashlib.sha256((tmp_path / "common.json").read_bytes()).hexdigest()
    return metadata


def test_no_consent_no_samples_no_training(tmp_path):
    result, runtime, privacy = learner(tmp_path, consent=False)
    assert result.latest is None
    assert not result.correct({"verdict": "correct"})
    samples(result)
    result.advance("IDLE")
    assert result.job is None and not (tmp_path / "head.json").exists()
    result.observe(runtime, "IDLE", 10)
    assert not result.samples


def test_cancel_mid_job_erases_samples_and_candidate(tmp_path):
    result, runtime, privacy = learner(tmp_path)
    samples(result)
    result.advance("IDLE")
    assert result.job is not None
    privacy.consented = False
    result.observe(runtime, "IDLE", 10)
    assert result.job is None and result.latest is None and not result.samples
    assert runtime.backend.head.record() == runtime.backend.common_head.record()


def test_bounded_updates_only_when_idle(tmp_path):
    result, runtime, _ = learner(tmp_path)
    samples(result)
    result.advance("FOCUS_PC")
    assert result.job is None
    result.advance("IDLE")
    step = result.job["step"]
    assert 0 < step <= result.config["updates_per_tick"]
    result.advance("FATIGUE")
    assert result.job["step"] == step
    result.observe(runtime, "FATIGUE", 10)
    assert result.snapshot()["status"] == "paused"
    result.observe(runtime, "END", 20)
    result.advance("END")
    assert result.snapshot()["status"] == "training"


@pytest.mark.parametrize("missing", ["session", "label"])
def test_insufficient_data_keeps_common(tmp_path, missing):
    result, runtime, _ = learner(tmp_path)
    samples(result)
    result.samples = type(result.samples)((s for s in result.samples if s[missing] != (3 if missing == "session" else 2)), maxlen=512)
    result.advance("IDLE")
    assert result.job is None and result.head_source == "common"


def test_bad_candidate_rejected_and_audit_not_reused(tmp_path):
    result, runtime, _ = learner(tmp_path)
    samples(result)
    result._prepare()
    result.job["head"].bias = [1000.0, 0.0, 0.0, 0.0]
    result._finish()
    assert result.status == "rejected"
    assert runtime.backend.head.record() == runtime.backend.common_head.record()
    assert not (tmp_path / "head.json").exists()
    result.advance("IDLE")
    assert result.job is None


def test_write_failure_never_applies_candidate(tmp_path, monkeypatch):
    result, runtime, _ = learner(tmp_path)
    samples(result)
    monkeypatch.setattr("deskmate_hub.personalization.ondevice.os.replace", lambda *args: (_ for _ in ()).throw(OSError("read only")))
    finish(result)
    assert result.status == "storage_error"
    assert runtime.backend.head.record() == runtime.backend.common_head.record()
    assert not (tmp_path / "head.json.tmp").exists()


@pytest.mark.parametrize("kind", ["corrupt", "different_backbone"])
def test_invalid_checkpoint_falls_back_to_common(tmp_path, kind):
    result, runtime, privacy = learner(tmp_path)
    (tmp_path / "head.json").write_text("bad" if kind == "corrupt" else json.dumps({"schema": "deskmate-personal-head/1", "backbone_sha256": "wrong"}))
    result.loaded = False
    result.observe(runtime, "IDLE", 10)
    assert result.status == "head_load_failed"
    assert result.head_source == "common"


def test_only_fresh_explicit_correction_and_last_label_wins(tmp_path):
    result, runtime, _ = learner(tmp_path)
    record = {"verdict": "accept", "corrected_state": "FATIGUE", "ts": 10,
              "target_window_start_ms": 0, "target_window_end_ms": 10000}
    assert not result.correct(record)
    record["verdict"] = "correct"
    assert result.correct(record)
    record["corrected_state"] = "REST"
    assert result.correct(record)
    assert len(result.samples) == 1 and result.samples[0]["label"] == 2
    record["ts"] = 20
    assert not result.correct(record)
    result.latest = None
    assert not result.correct(dict(record, ts=10))


def test_unregistered_head_is_unavailable(tmp_path):
    result, _, privacy = learner(tmp_path)
    privacy.config["personal_model_files"] = []
    assert OnDeviceLearner(result.config, privacy).status == "unavailable"


def test_runtime_failure_cancels_training(tmp_path):
    result, runtime, _ = learner(tmp_path)
    samples(result)
    result.advance("IDLE")
    runtime.backend = None
    runtime.status = "unavailable"
    result.observe(runtime, "IDLE", 10)
    assert result.backend is None and result.job is None and result.latest is None


def test_gradient_matches_numpy_and_stable_large_logits():
    import numpy as np
    weights = np.arange(64, dtype=float).reshape(16, 4) / 100
    bias = np.array([0.1, 0.2, 0.3, 0.4])
    x = np.arange(16, dtype=float) / 10
    head = Head(weights.tolist(), bias.tolist())
    logits = x @ weights + bias
    p = np.exp(logits - logits.max()); p /= p.sum()
    error = p - np.eye(4)[2]
    head.update(x.tolist(), 2, rate=0.02, l2=0.001)
    np.testing.assert_allclose(head.weights, weights - 0.02 * (x[:, None] * error + 0.001 * weights))
    np.testing.assert_allclose(head.bias, bias - 0.02 * error)
    head.bias = [10000.0, 0.0, -10000.0, 0.0]
    assert sum(head.predict([0.0] * 16)) == 1


def test_live_hub_opt_in_exposes_diagnostics_and_delete_resets(tmp_path):
    from deskmate_hub.live import LiveHub
    from deskmate_hub.ingest import SensorCache, load_ingest_config
    from deskmate_hub.personalization import load_personalization_config
    result, _, privacy = learner(tmp_path)
    path, metadata = bundle(tmp_path)
    meta_path = tmp_path / "metadata.json"
    meta_path.write_text(json.dumps(metadata))
    model_cfg = load_personalization_config()
    model_cfg.update(enabled=True, backend="portable_cnn", model_path=str(path), metadata_path=str(meta_path))
    ingest = load_ingest_config()
    ingest["baseline"]["persist"]["path"] = str(tmp_path / "baseline.json")
    cache = SensorCache()
    hub = LiveHub(cache, privacy_cfg=privacy.config, ondevice_cfg=result.config, ingest_cfg=ingest,
                  personalization_cfg=model_cfg, out=io.StringIO())
    assert hub.personalization.backend is None
    cache.put_feedback({"kind": "personalization", "action": "grant", "confirmed": True,
                        "policy_version": "lab-1", "hub_boot_id": hub.boot_id, "request_id": "grant-1"})
    state = hub.tick_once(10)
    assert isinstance(hub.personalization.backend, PortableBackend)
    assert state["data"]["sensor_summary"]["ondevice_learning"]["execution"] == "local"
    hub.ondevice.samples.append({"session": 0, "tick": 0, "label": 0, "embedding": [0.0] * 16})
    cache.put_feedback({"kind": "personalization", "action": "delete", "confirmed": True,
                        "hub_boot_id": hub.boot_id, "request_id": "delete-1"})
    # A power loss can leave an atomic-save sidecar. Consent deletion covers it too.
    (tmp_path / "head.json.tmp").write_text("interrupted personal checkpoint")
    hub.tick_once(20)
    assert not hub.ondevice.samples and hub.personalization.backend is None
    assert not (tmp_path / "head.json.tmp").exists()
    assert path.exists()


def test_training_execution_without_native_ml_or_missing_board_extensions(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    path, metadata = bundle(tmp_path)
    script = f'''
import sys
for name in ("numpy", "tensorflow", "tflite_runtime", "_socket", "_ssl", "_random"):
    sys.modules[name] = None
from deskmate_hub.personalization.portable import PortableBackend
b = PortableBackend({str(path)!r}, {metadata!r})
assert b.predict([[0.0] * 17 for _ in range(6)]) == [0.25] * 4
original = b.common_head.record()
for _ in range(50):
    b.head.update([1.0] + [0.0] * 15, 1, rate=0.1, l2=0.001)
assert b.head.predict([1.0] + [0.0] * 15)[1] > 0.8
assert b.common_head.record() == original
print("board-safe head training executed")
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_invalid_training_policy_unavailable(tmp_path):
    result, _, privacy = learner(tmp_path)
    for key, invalid in (("epochs", 0), ("tick_budget_ms", float("nan")), ("learning_rate", True), ("l2", -1)):
        assert OnDeviceLearner(dict(result.config, **{key: invalid}), privacy).status == "unavailable"


def test_bad_weights_or_fingerprint_rejected(tmp_path):
    path, metadata = bundle(tmp_path)
    metadata["model_sha256"] = "wrong"
    with pytest.raises(ValueError, match="fingerprint"):
        PortableBackend(path, metadata)
    content = json.loads(path.read_text())
    content["layers"]["conv1"]["weights"][0][0][0] = float("nan")
    path.write_text(json.dumps(content))
    metadata["model_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="weight"):
        PortableBackend(path, metadata)


def test_observe_only_preserves_fsm_result_and_report(tmp_path):
    from deskmate_hub.live import LiveHub
    from deskmate_hub.ingest import SensorCache, load_ingest_config
    from deskmate_hub.personalization import load_personalization_config
    result, _, privacy = learner(tmp_path)
    path, metadata = bundle(tmp_path)
    meta_path = tmp_path / "metadata.json"
    meta_path.write_text(json.dumps(metadata))
    model = dict(load_personalization_config(), enabled=True, backend="portable_cnn", model_path=str(path), metadata_path=str(meta_path))
    ingest = load_ingest_config()
    ingest["baseline"]["persist"]["path"] = str(tmp_path / "baseline.json")
    hubs = [LiveHub(SensorCache(), ingest_cfg=ingest, privacy_cfg=privacy.config, ondevice_cfg=result.config,
                    personalization_cfg=model, out=io.StringIO()),
            LiveHub(SensorCache(), ingest_cfg=ingest, personalization_cfg={"enabled": False}, out=io.StringIO())]
    for hub in hubs:
        hub.boot_id = "comparison"
    hubs[0].privacy.consented = True
    hubs[0]._grant_personalization()
    for t in range(0, 100, 10):
        states = [hub.tick_once(t) for hub in hubs]
        summary = states[0]["data"]["sensor_summary"]
        for key in ("privacy", "personalization", "ondevice_learning"):
            summary.pop(key)
        assert states[0] == states[1]
    assert hubs[0].report_envelope(100) == hubs[1].report_envelope(100)


def test_field_self_check_uses_synthetic_inputs_without_persistent_writes(tmp_path):
    from deskmate_hub.personalization.self_check import run_self_check
    from deskmate_hub.personalization import load_personalization_config
    path, metadata = bundle(tmp_path)
    meta_path = tmp_path / "metadata.json"
    meta_path.write_text(json.dumps(metadata))
    config = dict(load_personalization_config(), backend="portable_cnn", model_path=str(path), metadata_path=str(meta_path))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = run_self_check(config, load_ondevice_config(), period=10, normalization="baseline")
    assert result["status"] == "passed" and result["synthetic"]
    assert result["head_update_verified"] and result["backbone_unchanged"]
    assert result["persistent_writes"] is False
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    # The check must not enable or mutate the actual deployment config.
    assert config["enabled"] is False
    assert run_self_check(dict(config, backend="tflite"), {}, period=10, normalization="baseline")["status"] == "not_run"
    assert run_self_check(dict(config, model_path="missing"), {}, period=10, normalization="baseline")["status"] == "failed"
    assert run_self_check(config, {"learning_rate": "bad", "l2": 0}, period=10, normalization="baseline")["status"] == "failed"
