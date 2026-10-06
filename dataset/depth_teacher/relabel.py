"""저장된 3D 랜드마크(frames.csv)로 특징값과 라벨을 다시 계산.

config 임계값이나 geometry.py 의 특징 계산을 바꾼 뒤, 영상 재처리 없이 빠르게 반복한다.
(랜드마크 종류를 새로 추가한 경우에는 녹화본 raw.db3 를 run.py --bag 으로 재처리해야 한다.)

  python relabel.py data/sessions/<세션> [--config my.yaml] [--eval]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluate import evaluate
from posture.app import LABEL_COLS, load_config
from posture.geometry import FEATURES, compute_features
from posture.labeler import PostureLabeler, build_baseline
from posture.landmarks import POINT_NAMES


def _points(r):
    P = {}
    for n in POINT_NAMES:
        xyz = [r.get(f"{n}_{a}", np.nan) for a in "xyz"]
        if all(pd.notna(v) for v in xyz):
            P[n] = np.array(xyz, float)
    return P


def relabel(sdir, cfg):
    sdir = Path(sdir)
    df = pd.read_csv(sdir / "frames.csv")
    meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
    feats = [compute_features(_points(r)) for r in df.to_dict("records")]
    F = pd.DataFrame([f or {} for f in feats], index=df.index).reindex(columns=FEATURES)
    df[FEATURES] = F
    df["valid"] = [int(f is not None) for f in feats]

    calib = df[(df.phase == "calib") & (df.valid == 1)]
    if calib.empty:
        raise SystemExit("캘리브레이션 구간이 없는 세션입니다 (저장된 기준 자세로 실행된 세션).")
    baseline = build_baseline(cfg, [feats[i] for i in df.index[df.phase == "calib"]])
    if baseline is None:
        raise SystemExit("캘리브레이션의 유효 프레임 또는 고개 기준이 부족합니다. 다시 측정하세요.")
    times = (df.t_dev / 1000.0) if meta.get("bag_in") else df.t_host
    labeler = PostureLabeler(cfg, baseline)
    rows = []
    for t, phase, f in zip(times, df.phase, feats):
        if phase in ("prep", "calib"):
            rows.append({})
            continue
        rows.append(labeler.update(float(t), f))
    for c in LABEL_COLS:
        df[c] = [r.get(c) for r in rows]
    out = sdir / "frames_relabeled.csv"
    df.to_csv(out, index=False)
    print(f"재라벨링 저장: {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--config")
    ap.add_argument("--eval", action="store_true")
    a = ap.parse_args()
    out = relabel(a.session, load_config(a.config))
    if a.eval:
        evaluate(out)
