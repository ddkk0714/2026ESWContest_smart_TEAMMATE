"""fatigue_logger 세션 파일 → 5분 창 특징 + KSS 라벨.

라벨 시각은 질문이 뜬 시각(prompt_ts)이다 — 그때의 느낌을 물었기 때문. 세션 시작 응답은 창이 없어
학습 표본이 아니라 kss_start 특징으로만 쓴다. '졸려요'·놓친 질문은 회귀 라벨이 아니라 기록만 한다.

interpolate_sec 를 주면 응답 사이를 선형으로 이은 KSS 로 창을 더 만든다(학습 전용, kind="interp").
평가에는 실제 응답 창(kind="label")만 쓴다.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
from deskmate_hub.fatigue.features import BASELINE_SEC, FEATURES, WINDOW_SEC, window_features  # noqa: E402

LABEL_EVENTS = ("kss", "session_end")


@dataclass
class Session:
    path: Path
    start: dict
    ticks: list[dict]
    labels: list[dict]  # (label_ts, kss) 로 쓸 응답


def read_session(path: str | Path) -> Session | None:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    start = next((r for r in rows if r.get("kind") == "label" and r.get("event") == "session_start"), None)
    if start is None:
        return None  # 시작 답변(수면·KSS)이 없으면 문맥 특징을 만들 수 없다
    ticks = sorted((r for r in rows if r.get("kind") == "tick" and isinstance(r.get("ts"), (int, float))),
                   key=lambda r: r["ts"])
    labels = []
    for r in rows:
        if r.get("kind") == "label" and r.get("event") in LABEL_EVENTS and isinstance(r.get("kss"), int):
            at = r.get("prompt_ts") if isinstance(r.get("prompt_ts"), (int, float)) else r["ts"]
            labels.append({"ts": float(at), "kss": r["kss"]})
    labels.sort(key=lambda r: r["ts"])
    return Session(Path(path), start, ticks, labels)


def _usable(s: Session, end_ts: float, min_ticks: int) -> bool:
    return sum(1 for t in s.ticks if end_ts - WINDOW_SEC < t["ts"] <= end_ts) >= min_ticks


def build(raw_dir: str | Path, *, interpolate_sec: float | None = None, min_ticks: int = 10):
    """X(float, 없는 값 nan), y(KSS), meta(list[dict]: profile·session·ts·kind)."""
    xs, ys, meta = [], [], []

    def add(s: Session, end_ts: float, kss: float, kind: str) -> None:
        feats = window_features(s.ticks, end_ts, s.start, window_sec=WINDOW_SEC, baseline_sec=BASELINE_SEC)
        xs.append([np.nan if feats[k] is None else feats[k] for k in FEATURES])
        ys.append(kss)
        meta.append({"profile": s.start["profile_id"], "session": s.start["session_id"], "ts": end_ts, "kind": kind})

    for path in sorted(Path(raw_dir).glob("*/*.jsonl")):
        s = read_session(path)
        if s is None:
            continue
        for lab in s.labels:
            if _usable(s, lab["ts"], min_ticks):
                add(s, lab["ts"], float(lab["kss"]), "label")
        if interpolate_sec:
            anchors = [{"ts": float(s.start["ts"]), "kss": s.start.get("kss")}] + s.labels
            anchors = [a for a in anchors if isinstance(a["kss"], (int, float))]
            for a, b in zip(anchors, anchors[1:]):
                t = a["ts"] + interpolate_sec
                while t < b["ts"] - interpolate_sec / 2:
                    if _usable(s, t, min_ticks):
                        w = (t - a["ts"]) / (b["ts"] - a["ts"])
                        add(s, t, a["kss"] + w * (b["kss"] - a["kss"]), "interp")
                    t += interpolate_sec
    x = np.asarray(xs, dtype=np.float64).reshape(-1, len(FEATURES))
    return x, np.asarray(ys, dtype=np.float64), meta
