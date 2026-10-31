"""Per-frame scoring of the streaming classifier (shared by train.py and export.py)."""

from __future__ import annotations

import numpy as np
import torch

from .data import to_input
from .session import FLAG_TRANSITION, LABELS


def evaluate(model, data: dict, warmup_frames: int = 45, chunk: int = 600) -> dict:
    """Per-frame scores over whole sessions, each run from a fresh state.

    Frames in transitions and the first `warmup_frames` of each session are
    not scored (the label is ambiguous there).
    """
    with torch.no_grad():
        return _evaluate(model, data, warmup_frames, chunk)


def _evaluate(model, data, warmup_frames, chunk):
    was_training = model.training
    dev = next(model.parameters()).device
    model.eval()
    dist, status, label = data["dist"], data["status"], data["label"].astype(np.int64)
    flags = data.get("flags", np.zeros(len(label), dtype=np.uint8))
    starts = list(data.get("starts", np.array([0])))
    ends = starts[1:] + [len(label)]
    C = len(LABELS)
    conf = np.zeros((C, C), dtype=np.int64)
    az = data.get("session_az")
    sector_conf = {name: np.zeros((C, C), dtype=np.int64) for name in ("left", "front", "right")}
    for idx, (s, e) in enumerate(zip(starts, ends)):
        x = torch.from_numpy(to_input(dist[s:e], status[s:e]))[None].to(dev)
        h = None
        preds = []
        for i in range(0, x.shape[1], chunk):
            logits, h = model(x[:, i:i + chunk], h)
            preds.append(logits.argmax(-1)[0].cpu().numpy())
        pred = np.concatenate(preds)
        y = label[s:e]
        keep = (flags[s:e] & FLAG_TRANSITION) == 0
        keep[:warmup_frames] = False
        np.add.at(conf, (y[keep], pred[keep]), 1)
        if az is not None:
            sec = "left" if az[idx] > 15 else "right" if az[idx] < -15 else "front"   # + = the user's left
            np.add.at(sector_conf[sec], (y[keep], pred[keep]), 1)
    if was_training:
        model.train()
    tp = np.diag(conf).astype(float)
    recall = tp / np.maximum(conf.sum(1), 1)
    precision = tp / np.maximum(conf.sum(0), 1)
    f1 = 2 * recall * precision / np.maximum(recall + precision, 1e-9)
    present = conf.sum(1) > 0

    def macro_f1(cm):
        tp_ = np.diag(cm).astype(float)
        r_, p_ = tp_ / np.maximum(cm.sum(1), 1), tp_ / np.maximum(cm.sum(0), 1)
        f_ = 2 * r_ * p_ / np.maximum(r_ + p_, 1e-9)
        pres = cm.sum(1) > 0
        return float(f_[pres].mean()) if pres.any() else None

    sectors = {k: macro_f1(v) for k, v in sector_conf.items() if v.sum() > 0} if az is not None else {}
    return {
        "sector_f1": sectors,
        "accuracy": float(tp.sum() / max(conf.sum(), 1)),
        "macro_f1": float(f1[present].mean()) if present.any() else 0.0,
        "recall": {LABELS[i]: round(float(recall[i]), 4) for i in range(C) if present[i]},
        "precision": {LABELS[i]: round(float(precision[i]), 4) for i in range(C) if present[i]},
        "f1": {LABELS[i]: round(float(f1[i]), 4) for i in range(C) if present[i]},
        "confusion": conf.tolist(),
        "frames": int(conf.sum()),
    }


def _format_eval_base(ev: dict) -> str:
    rec = " ".join(f"{k}:{v:.2f}" for k, v in ev["recall"].items())
    return f"acc {ev['accuracy']:.3f} macroF1 {ev['macro_f1']:.3f} | recall {rec}"


def format_eval(ev: dict) -> str:
    s = _format_eval_base(ev)
    if ev.get("sector_f1"):
        s += " | sector F1 " + " ".join(f"{k}:{v:.2f}" for k, v in ev["sector_f1"].items() if v is not None)
    return s
