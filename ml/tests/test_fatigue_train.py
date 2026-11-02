"""합성 세션 → 창 → LOSO → JSON 내보내기 전 과정. 합성이라 수치는 동작 확인일 뿐이다."""
from __future__ import annotations

import json

import numpy as np

from ml.training.fatigue_dataset import build, read_session
from ml.training.fatigue_synth import write_synthetic
from ml.training.fatigue_train import auc, main, spearman


def test_dataset_uses_prompt_time_and_keeps_interp_out_of_labels(tmp_path):
    raw = write_synthetic(tmp_path / "raw", profiles=3, sessions=2, seed=3)
    session = read_session(next(raw.glob("*/*.jsonl")))
    assert session.labels and all(a["ts"] <= b["ts"] for a, b in zip(session.labels, session.labels[1:]))
    x, y, meta = build(raw, interpolate_sec=60)
    kinds = {m["kind"] for m in meta}
    assert kinds == {"label", "interp"}
    assert x.shape[0] == len(y) == len(meta)
    assert np.all((y >= 1) & (y <= 9))


def test_train_beats_trivial_baselines_on_synthetic_and_exports(tmp_path, capsys):
    out = tmp_path / "model.json"
    assert main(["--synthetic", "--out", str(out)]) == 0
    spec = json.loads(out.read_text(encoding="utf-8"))
    methods = spec["loso"]["methods"]
    assert methods["model"]["mae"] < methods["mean"]["mae"]
    assert methods["model"]["mae"] < methods["start"]["mae"]
    assert spec["trained_on"]["profiles"] == 6
    assert "loso" in capsys.readouterr().out


def test_rank_metrics():
    assert spearman(np.array([1.0, 2, 3, 4]), np.array([10.0, 20, 30, 40])) == 1.0
    assert auc(np.array([0.9, 0.8, 0.1]), np.array([True, True, False])) == 1.0
    assert auc(np.array([0.5]), np.array([True])) is None
