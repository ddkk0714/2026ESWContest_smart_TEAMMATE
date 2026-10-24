"""시뮬 정답(sim_truth) 대비 학습 모델과 규칙 FSM 을 같은 test 세션에서 비교한다.

life_sim 잠재 피로 데이터(build_dataset --logs --truth)로 만든 데이터셋과 train_common 의 common.keras 를 받아
  - 4 클래스(focus·fatigue·rest·idle) 정확도·macro F1·혼동표: 학습 모델 vs FSM phase(약한 라벨과 같은 매핑)
  - 재실 중 피로 이진(fatigue vs focus): 정밀도·재현율, 정답 focus 1 시간당 헛 피로 판정
  - 피로 시작 → 감지 시간: 정답이 focus 에서 fatigue 로 바뀐 에피소드마다 처음 fatigue 라고 한 tick 까지
를 낸다. 결과는 시뮬 사람 모델(fatigue.py)의 가정을 기준으로 한 것이다 — 실사용 정확도가 아니다.

    python -m ml.training.eval_truth --dataset ml/datasets/<이름> --model ml/models/<이름> --out ml/models/<이름>/eval-truth.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ml.training import frames  # noqa: E402
from ml.training.train_common import metrics  # noqa: E402

FAT = frames.CLASS_INDEX["fatigue"]
FOC = frames.CLASS_INDEX["focus"]
TICK_S = 10.0


def _binary(truth: np.ndarray, pred: np.ndarray) -> dict:
    """재실(정답 focus·fatigue) tick 만: fatigue 를 양성으로."""
    keep = (truth == FAT) | (truth == FOC)
    t, p = truth[keep] == FAT, pred[keep] == FAT
    tp, fp, fn = int(np.sum(t & p)), int(np.sum(~t & p)), int(np.sum(t & ~p))
    focus_hours = float(np.sum(~t)) * TICK_S / 3600.0
    return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "false_fatigue_min_per_focus_hour": round(fp * TICK_S / 3600.0 / focus_hours * 60.0, 1) if focus_hours else None,
            "ticks": int(keep.sum())}


def _latency(seqs: dict[str, tuple[np.ndarray, np.ndarray]], *, min_prior: int = 6) -> dict:
    """정답이 focus 를 min_prior tick 이상 이어가다 fatigue 로 바뀐 에피소드마다 처음 fatigue 예측까지 초."""
    lat, missed = [], 0
    for truth, pred in seqs.values():
        i = min_prior
        while i < len(truth):
            if truth[i] == FAT and all(truth[i - k] == FOC for k in range(1, min_prior + 1)):
                j = i
                while j < len(truth) and truth[j] == FAT and pred[j] != FAT:
                    j += 1
                if j < len(truth) and truth[j] == FAT:
                    lat.append((j - i) * TICK_S)
                else:
                    missed += 1
                while i < len(truth) and truth[i] == FAT:
                    i += 1
            i += 1
    return {"episodes": len(lat) + missed, "detected": len(lat), "missed": missed,
            "median_s": float(np.median(lat)) if lat else None, "p90_s": float(np.percentile(lat, 90)) if lat else None}


def evaluate(dataset: str | Path, model_dir: str | Path) -> dict:
    dataset, model_dir = Path(dataset), Path(model_dir)
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    with np.load(dataset / "windows.npz", allow_pickle=False) as archive:
        x, y = archive["X_test"], archive["y_test"]
        meta = [json.loads(m) for m in archive["meta"]]
    meta = [m for m in meta if m["split"] == "test"]
    import tensorflow as tf
    model = tf.keras.models.load_model(model_dir / "common.keras")
    pred = model(x, training=False).numpy().argmax(axis=1)
    width = int(manifest["window"])
    # FSM phase → 클래스 (약한 라벨과 같은 매핑)를 세션별 state 로그에서
    states = {}
    for mat in manifest["materials"]:
        if mat.get("source") == "log" and mat["session_id"] in set(m["session_id"] for m in meta):
            st = frames.read_jsonl(mat["state"])
            states[mat["session_id"]] = [frames.phase_label((s.get("data") or {}).get("phase")) for s in st]
    fsm = np.asarray([states[m["session_id"]][m["start_tick"] + width - 1] for m in meta], dtype=object)
    ok = np.asarray([v is not None for v in fsm])
    fsm_pred = np.where(ok, fsm, -1).astype(int)
    per_session: dict[str, list] = defaultdict(list)
    for m, t, p, f in zip(meta, y, pred, fsm_pred):
        per_session[m["session_id"]].append((m["start_tick"], t, p, f))
    model_seqs, fsm_seqs = {}, {}
    for sid, items in per_session.items():
        items.sort()
        t = np.asarray([a[1] for a in items]); model_seqs[sid] = (t, np.asarray([a[2] for a in items]))
        fsm_seqs[sid] = (t, np.asarray([a[3] for a in items]))
    return {"test_sessions": len(per_session), "test_windows": int(len(y)),
            "truth_counts": {name: int(np.sum(y == i)) for name, i in frames.CLASS_INDEX.items()},
            "model": {**metrics(y, pred), "fatigue_binary": _binary(y, pred), "onset_latency": _latency(model_seqs)},
            "fsm": {**metrics(y[ok], fsm_pred[ok]), "fatigue_binary": _binary(y[ok], fsm_pred[ok]),
                    "onset_latency": _latency(fsm_seqs)},
            "note": "정답은 life_sim 잠재 피로(시뮬 가정)에서 나온 것 — 실사용 정확도가 아니다."}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    report = evaluate(args.dataset, args.model)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    short = {k: {kk: report[k][kk] for kk in ("accuracy", "macro_f1", "fatigue_binary", "onset_latency")} for k in ("model", "fsm")}
    print(json.dumps(short, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
