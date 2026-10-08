import io
import json
import sys
from pathlib import Path

import pytest

from deskmate_hub.personalization.contract import feature_values, model_metadata
from deskmate_hub.personalization.runtime import PersonalizationRuntime
from deskmate_hub.personalization import load_personalization_config
from deskmate_hub.live import LiveHub
from deskmate_hub.ingest import SensorCache


class Backend:
    def __init__(self, path, metadata):
        self.calls = []
        self.output = [0.8, 0.1, 0.05, 0.05]

    def predict(self, rows):
        self.calls.append(rows)
        return self.output


def configured(tmp_path):
    cfg = load_personalization_config()
    cfg.update(enabled=True, model_path=str(tmp_path / "model.tflite"), metadata_path=str(tmp_path / "model.json"))
    Path(cfg["model_path"]).write_bytes(b"test backend fixture")
    Path(cfg["metadata_path"]).write_text(json.dumps(model_metadata(6, 10, "linear")))
    return cfg


def runtime(cfg, factory=Backend):
    return PersonalizationRuntime(cfg, period=10, normalization="linear", backend_factory=factory)


def frame(now):
    return {"now": now, "present": True, "pc_ratio": 0.4,
            "signals": {"keystroke": {"phi": 0.3, "delta": -0.1, "available": True}}}


def test_feature_parity_and_missing_mask():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml"))
    import numpy as np
    from training.frames import feature_vector
    f = frame(0)
    f["signals"]["posture"] = {"phi": float("nan"), "delta": 9, "available": False}
    assert feature_values(f)[3:6] == [0, 0, 0]
    np.testing.assert_array_equal(feature_vector(f), np.asarray(feature_values(f), dtype=np.float32))


def test_disabled_does_not_load():
    def fail(*args):
        pytest.fail("loaded when disabled")
    r = runtime({"enabled": False}, fail)
    assert r.observe(frame(0))["status"] == "disabled"


def test_window_prediction_and_period_reset(tmp_path):
    r = runtime(configured(tmp_path))
    for now in range(0, 50, 10):
        assert r.observe(frame(now))["status"] == "warming_up"
    result = r.observe(frame(50))
    assert result["status"] == "predicted" and result["label"] == "focus"
    assert len(r.backend.calls[0]) == 6 and len(r.backend.calls[0][0]) == 17
    for now in (50, 20, 90):
        assert r.observe(frame(now))["window_ready"] == 1
    assert r.observe(frame(100), reset=True)["window_ready"] == 1


@pytest.mark.parametrize("key", ["features", "classes", "input_shape", "output_shape", "dtype", "normalization", "frame_period_sec", "contract_version"])
def test_contract_mismatch_before_backend(tmp_path, key):
    cfg = configured(tmp_path)
    p = Path(cfg["metadata_path"])
    metadata = json.loads(p.read_text())
    metadata[key] = "wrong"
    p.write_text(json.dumps(metadata))
    r = runtime(cfg, lambda *args: pytest.fail("invalid metadata loaded"))
    assert r.status == "unavailable"


@pytest.mark.parametrize("failure", ["missing_model", "empty_model", "missing_metadata", "corrupt_metadata", "backend", "mode"])
def test_loading_failures_do_not_escape(tmp_path, failure):
    cfg = configured(tmp_path)
    factory = Backend
    if failure == "missing_model":
        Path(cfg["model_path"]).unlink()
    elif failure == "empty_model":
        Path(cfg["model_path"]).write_bytes(b"")
    elif failure == "missing_metadata":
        Path(cfg["metadata_path"]).unlink()
    elif failure == "corrupt_metadata":
        Path(cfg["metadata_path"]).write_text("{")
    elif failure == "mode":
        cfg["mode"] = "fusion"
    else:
        def factory(*args):
            raise ImportError("native runtime unavailable")
    assert runtime(cfg, factory).observe(frame(0))["status"] == "unavailable"


@pytest.mark.parametrize("output", [[float("nan"), 0, 0, 1], [1.1, 0, 0, 0], [0.2]*4, [1, 0], None])
def test_invalid_output_latches_fallback(tmp_path, output):
    r = runtime(configured(tmp_path))
    r.backend.output = output
    for now in range(0, 60, 10):
        result = r.observe(frame(now))
    assert result["status"] == "unavailable" and r.backend is None
    assert r.observe(frame(60))["status"] == "unavailable"


def test_inference_budget_and_nonfinite_input(tmp_path, monkeypatch):
    r = runtime(configured(tmp_path))
    clock = iter([0, 1])
    monkeypatch.setattr("deskmate_hub.personalization.runtime.time.perf_counter", lambda: next(clock))
    for now in range(0, 60, 10):
        result = r.observe(frame(now))
    assert result["status"] == "unavailable"
    r = runtime(configured(tmp_path))
    f = frame(0)
    f["signals"]["keystroke"]["phi"] = float("inf")
    assert r.observe(f)["status"] == "unavailable"


def test_observation_keeps_fsm_and_report(tmp_path):
    cfg = configured(tmp_path)
    normal = LiveHub(SensorCache(), out=io.StringIO(), control_cfg={"enabled": False})
    observed = LiveHub(SensorCache(), out=io.StringIO(), control_cfg={"enabled": False},
                       personalization_cfg=cfg, personalization_backend_factory=Backend)
    observed.boot_id = normal.boot_id
    for now in range(100, 300, 10):
        baseline = normal.tick_once(now)
        actual = observed.tick_once(now)
        observation = actual["data"]["sensor_summary"].pop("personalization")
        assert observation["decision_source"] == "fsm"
        assert actual == baseline
    assert observed.report_envelope(now=300) == normal.report_envelope(now=300)


@pytest.mark.parametrize("bad", [None, "shape", "dtype", "count"])
def test_tflite_tensor_contract_and_io(monkeypatch, bad):
    import types
    import numpy as np
    from deskmate_hub.personalization.tflite_backend import TFLiteBackend

    class Interpreter:
        def __init__(self, **kwargs):
            self.input = None
            self.invoked = False
        def allocate_tensors(self):
            pass
        def get_input_details(self):
            return [{"shape": [1, 6, 17] if bad != "shape" else [1, 17, 6],
                     "dtype": np.float32 if bad != "dtype" else np.int8, "index": 0}]
        def get_output_details(self):
            output = {"shape": [1, 4], "dtype": np.float32, "index": 1}
            return [output] if bad != "count" else [output, output]
        def set_tensor(self, index, value):
            assert index == 0 and value.shape == (1, 6, 17) and value.dtype == np.float32
            self.input = value
        def invoke(self):
            assert self.input is not None
            self.invoked = True
        def get_tensor(self, index):
            assert index == 1 and self.invoked
            return np.asarray([[0.8, 0.1, 0.05, 0.05]], dtype=np.float32)

    monkeypatch.setitem(sys.modules, "tflite_runtime", types.ModuleType("tflite_runtime"))
    module = types.ModuleType("tflite_runtime.interpreter")
    module.Interpreter = Interpreter
    monkeypatch.setitem(sys.modules, "tflite_runtime.interpreter", module)
    if bad is not None:
        with pytest.raises(ValueError):
            TFLiteBackend("test", model_metadata(6, 10, "linear"))
    else:
        backend = TFLiteBackend("test", model_metadata(6, 10, "linear"))
        assert backend.predict([feature_values(frame(t)) for t in range(6)]) == pytest.approx([0.8, 0.1, 0.05, 0.05])


def test_packaged_personalization_config(tmp_path):
    import subprocess
    import zipfile
    import yaml
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "hub.elf"
    executable.write_bytes(b"ELF fixture")
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    config_dir = root / "hub/deskmate_hub/config"
    result = subprocess.run([sys.executable, str(root / "hub/atlas/tools/build_payload.py"),
                             "--executable", str(executable), "--hub", str(root / "hub/deskmate_hub"),
                             "--config", str(config_dir / "fsm.yaml"), "--ingest-config", str(config_dir / "ingest.yaml"),
                             "--stdlib", str(stdlib)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(executable) as archive:
        packaged = json.loads(archive.read("deskmate_hub/config/personalization.json"))
        privacy = json.loads(archive.read("deskmate_hub/config/privacy.json"))
        ondevice = json.loads(archive.read("deskmate_hub/config/ondevice.json"))
        assert "deskmate_hub/personalization/runtime.py" in archive.namelist()
    assert packaged == yaml.safe_load((config_dir / "personalization.yaml").read_text(encoding="utf-8"))
    assert privacy == yaml.safe_load((config_dir / "privacy.yaml").read_text(encoding="utf-8"))
    assert ondevice == yaml.safe_load((config_dir / "ondevice.yaml").read_text(encoding="utf-8"))
