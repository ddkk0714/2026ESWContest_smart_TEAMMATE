"""Consent-gated, bounded local head learning. Never feeds the FSM."""
import json
import os
import pkgutil
import time
from collections import deque

from .portable import Head, PortableBackend

LABELS = {"FOCUS_PC": 0, "FATIGUE": 1, "REST": 2, "IDLE": 3}


def load_ondevice_config():
    try:
        raw = pkgutil.get_data("deskmate_hub", "config/ondevice.json")
    except OSError:
        raw = None
    if raw is not None:
        return json.loads(raw.decode("utf-8"))
    import yaml
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "ondevice.yaml"), encoding="utf-8") as source:
        return yaml.safe_load(source)


def metrics(head, samples):
    matrix = [[0] * 4 for _ in range(4)]
    for item in samples:
        probabilities = head.predict(item["embedding"])
        matrix[item["label"]][max(range(4), key=probabilities.__getitem__)] += 1
    recall, f1 = [], []
    for i in range(4):
        actual, predicted = sum(matrix[i]), sum(row[i] for row in matrix)
        recall.append(matrix[i][i] / actual if actual else 0.0)
        f1.append(2 * matrix[i][i] / (actual + predicted) if actual + predicted else 0.0)
    return {"macro_f1": sum(f1) / 4, "recall": recall}


class OnDeviceLearner:
    def __init__(self, config, privacy):
        self.config, self.privacy = config, privacy
        self.status = "disabled"
        self.samples = deque()
        self.latest = None
        self.backend = None
        self.job = None
        self.audited = set()
        self.session = 0
        self.previous_phase = None
        self.phase = None
        self.loaded = False
        self.head_source = "common"
        self.valid = False
        if config.get("enabled") is not True:
            return
        try:
            import math
            for key in ("max_samples", "min_train_sessions", "min_per_class", "epochs", "updates_per_tick"):
                if type(config[key]) is not int or config[key] < 1:
                    raise ValueError("invalid limits")
            for key in ("max_label_age_sec", "learning_rate", "tick_budget_ms"):
                if type(config[key]) not in (int, float) or not math.isfinite(config[key]) or config[key] <= 0:
                    raise ValueError("invalid limits")
            for key in ("l2", "min_macro_f1_gain", "max_recall_regression"):
                if type(config[key]) not in (int, float) or not math.isfinite(config[key]) or config[key] < 0:
                    raise ValueError("invalid policy")
            # Persistent head must be explicitly included in the consent/delete scope.
            path = privacy._path(config["head_path"])
            managed = [privacy._path(p) for p in privacy.config["personal_model_files"]]
            if not privacy.enabled or not privacy.valid or path not in managed or privacy._path(path + ".tmp") not in managed:
                raise ValueError("unmanaged head file")
            self.path = path
            self.samples = deque(maxlen=config["max_samples"])
            self.valid = True
            self.status = "awaiting_consent"
        except (ValueError, TypeError, KeyError, AttributeError):
            self.status = "unavailable"

    def reset(self):
        self.samples.clear()
        self.latest = self.job = None
        self.audited.clear()
        if self.backend is not None:
            self.backend.head = self.backend.common_head.copy()
        self.backend = None
        self.loaded = False
        self.head_source = "common"
        self.status = "awaiting_consent" if self.valid else self.status

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.privacy._path(self.path), encoding="utf-8") as source:
            record = json.load(source)
        if record.get("schema") != "deskmate-personal-head/1" or record.get("backbone_sha256") != self.backend.fingerprint:
            raise ValueError("head belongs to a different backbone")
        self.backend.head = Head(**record["head"])
        self.head_source = "personal"

    def bind(self, runtime):
        if not self.valid:
            return
        if not self.privacy.consented:
            self.reset()
            return
        backend = runtime.backend
        if not isinstance(backend, PortableBackend):
            self.latest = None
            self.backend = None
            self.job = None
            self.loaded = False
            self.status = "awaiting_portable_model"
            return
        if backend is not self.backend:
            self.loaded = False
            self.job = None
            self.head_source = "common"
        self.backend = backend
        if not self.loaded:
            self.loaded = True
            try:
                self._load()
            except (OSError, ValueError, KeyError, TypeError):
                # A corrupt personal artifact cannot affect the frozen common head.
                self.status = "head_load_failed"
        return True

    def observe(self, runtime, phase, now):
        self.phase = phase
        if not self.bind(runtime):
            return
        backend = self.backend
        if phase == "START" and self.previous_phase != "START":
            self.session += 1
        self.previous_phase = phase
        self.latest = (now, self.session, list(backend.last_embedding)) if runtime.status == "predicted" and backend.last_embedding is not None else None
        if self.job is None and self.status not in ("head_load_failed", "accepted", "rejected", "storage_error"):
            self.status = "collecting"

    def correct(self, record):
        if not self.valid or not self.privacy.consented or self.latest is None or record.get("verdict") != "correct":
            return False
        label = LABELS.get(record.get("corrected_state"))
        tick, session, embedding = self.latest
        age = record["ts"] - tick
        if label is None or not 0 <= age <= self.config["max_label_age_sec"] or not record["target_window_start_ms"] <= tick * 1000 <= record["target_window_end_ms"]:
            return False
        # The latest explicit correction replaces the previous label for the same tick.
        self.samples = deque((s for s in self.samples if s["tick"] != tick), maxlen=self.config["max_samples"])
        self.samples.append({"tick": tick, "session": session, "embedding": embedding, "label": label})
        return True

    def _prepare(self):
        sessions = sorted({s["session"] for s in self.samples})
        self.audited.intersection_update(sessions)
        if len(sessions) < self.config["min_train_sessions"] + 2 or sessions[-1] in self.audited:
            return
        train = [s for s in self.samples if s["session"] in sessions[:-2]]
        validation = [s for s in self.samples if s["session"] == sessions[-2]]
        audit = [s for s in self.samples if s["session"] == sessions[-1]]
        for split in (train, validation, audit):
            if any(sum(s["label"] == i for s in split) < self.config["min_per_class"] for i in range(4)):
                return
        self.job = {"train": train, "validation": validation, "audit": audit,
                    "audit_session": sessions[-1], "head": self.backend.head.copy(), "step": 0}
        self.status = "training"

    def advance(self, phase):
        if not self.valid or not self.privacy.consented or self.backend is None or phase not in ("IDLE", "END"):
            return
        try:
            if self.job is None:
                self._prepare()
            if self.job is None:
                return
            job = self.job
            started = time.perf_counter()
            total = len(job["train"]) * self.config["epochs"]
            for _ in range(self.config["updates_per_tick"]):
                sample = job["train"][job["step"] % len(job["train"])]
                job["head"].update(sample["embedding"], sample["label"], rate=self.config["learning_rate"], l2=self.config["l2"])
                job["step"] += 1
                if job["step"] >= total:
                    self._finish()
                    break
                if (time.perf_counter() - started) * 1000 >= self.config["tick_budget_ms"]:
                    break
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            self.status = "storage_error"
            self.job = None

    def _finish(self):
        job = self.job
        self.audited.add(job["audit_session"])
        accepted = True
        # Both previous personal and original common head must be preserved or improved.
        for split in (job["validation"], job["audit"]):
            candidate = metrics(job["head"], split)
            for incumbent in (self.backend.common_head, self.backend.head):
                baseline = metrics(incumbent, split)
                accepted &= candidate["macro_f1"] > baseline["macro_f1"] + self.config["min_macro_f1_gain"]
                accepted &= all(a + self.config["max_recall_regression"] >= b for a, b in zip(candidate["recall"], baseline["recall"]))
        if accepted:
            path = self.privacy._path(self.path)
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            temporary = self.privacy._path(path + ".tmp")
            try:
                fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump({"schema": "deskmate-personal-head/1", "backbone_sha256": self.backend.fingerprint,
                               "head": job["head"].record()}, output, allow_nan=False)
                os.replace(temporary, path)
            finally:
                if os.path.isfile(temporary):
                    os.unlink(temporary)
            self.backend.head = job["head"]
            self.head_source = "personal"
        self.status = "accepted" if accepted else "rejected"
        self.job = None

    def snapshot(self):
        status = "paused" if self.job is not None and self.phase not in ("IDLE", "END") else self.status
        return {"status": status, "execution": "local", "decision_source": "fsm",
                "head_source": self.head_source, "samples": len(self.samples),
                "sessions": len({s["session"] for s in self.samples}),
                "training_step": self.job["step"] if self.job else None}
