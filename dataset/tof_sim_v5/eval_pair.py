"""한 장치의 두 센서(좌·우 VL53L8CX)로 같은 세션을 보고, 센서별 추론을 융합했을 때 점수를 잰다.

    python eval_pair.py --ckpt runs/ubai_random/ckpt_best.pt --placement random --sessions 24 --seconds 300 --workers 8
    python eval_pair.py --ckpt runs/ubai_v3/ckpt_best.pt --placement arc --out runs/pair_arc.json

모델은 센서 하나의 8×8 을 보도록 학습됐다(세션마다 두 센서 중 하나를 무작위로 골라 학습). 보드에서는 센서마다
모델을 하나씩 돌리고 결과를 합치므로, 여기서 어떤 합치기 규칙이 나은지 같은 세션·같은 동작으로 비교한다.

세션 생성은 export_arc.py 와 같다: 센서마다 같은 seed 로 세션을 다시 뽑고 sensor_pose 만 바꾼다(동작이 같은지
라벨로 확인). 채점은 tofsim/evaluate.py 와 같은 규칙(전환 구간·세션 첫 45프레임 제외).

규칙:
    left / right     센서 하나만 (학습 때와 같은 조건)
    mean_logit       logits 평균
    sum_logprob      log-softmax 합 (확률 곱)
    mean_prob        softmax 평균
    max_conf         프레임마다 더 확신하는 센서 쪽을 택함
    misplaced_aware  한쪽만 misplaced 라 하면 다른 쪽을 믿고, 아니면 sum_logprob
    either_correct   상한: 둘 중 하나라도 맞으면 맞음 (실제 규칙이 아님)
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tofsim.session import FLAG_TRANSITION, LABELS, generate_frames, sample_session  # noqa: E402

WARMUP = 45


def render_pair(job):
    """seed 하나 -> 두 센서가 본 같은 세션. (dist[2,T,H,W], status[2,T,H,W], label[T], flags[T], meta)"""
    seed, placement, seconds, clutter_max = job
    from tofsim.mj_scene import MjDesk

    kw = {"placement": placement}
    if clutter_max is not None:
        kw["clutter_max"] = clutter_max
    cfg0 = sample_session(np.random.default_rng(seed), **kw)
    arc = cfg0.meta["arc"]
    a_i = next(i for i, (_, _, pose) in enumerate(arc) if pose is cfg0.sensor_pose) // 2
    pair = arc[a_i * 2: a_i * 2 + 2]                      # (az, sensor_index, pose) for the device's two sensors
    desk = MjDesk(cfg0.room, cfg0.person)
    dists, stats, labels, flags = [], [], None, None
    for az, si, pose in pair:
        rng = np.random.default_rng(seed)
        cfg = replace(sample_session(rng, **kw), sensor_pose=pose)   # same draws as cfg0, then this sensor
        d, s, l, f = [], [], [], []
        for _, dist, status, label, fl in generate_frames(cfg, seconds, rng, backend="mujoco", desk=desk):
            d.append(dist.copy()); s.append(status.copy()); l.append(label); f.append(fl)
        l, f = np.array(l, dtype=np.int64), np.array(f, dtype=np.uint8)
        if labels is None:
            labels, flags = l, f
        else:
            assert np.array_equal(labels, l), f"seed {seed}: motion differs between the two sensors"
        dists.append(np.stack(d)); stats.append(np.stack(s))
    meta = {"seed": seed, "azimuth_deg": float(cfg0.meta["azimuth_deg"]), "misplaced": bool(cfg0.meta.get("misplaced", False)),
            "drawn_sensor": int(cfg0.meta["sensor_index"])}
    return np.stack(dists), np.stack(stats), labels, flags, meta


def infer(net, dist, status, chunk=600):
    """(T,H,W) -> logits (T,C), 세션 처음부터 상태 0."""
    import torch
    from tofsim.data import to_input
    x = torch.from_numpy(to_input(dist, status))[None]
    out, h = [], None
    with torch.no_grad():
        for i in range(0, x.shape[1], chunk):
            logits, h = net(x[:, i:i + chunk], h)
            out.append(logits[0].numpy())
    return np.concatenate(out)


def log_softmax(z):
    z = z - z.max(-1, keepdims=True)
    return z - np.log(np.exp(z).sum(-1, keepdims=True))


def rule_preds(l0, l1, misplaced_idx):
    """logits (T,C) 두 개 -> 규칙별 예측 (T,)."""
    lp0, lp1 = log_softmax(l0), log_softmax(l1)
    p0, p1 = np.exp(lp0), np.exp(lp1)
    a0, a1 = l0.argmax(-1), l1.argmax(-1)
    preds = {
        "left": a0, "right": a1,
        "mean_logit": ((l0 + l1) / 2).argmax(-1),
        "sum_logprob": (lp0 + lp1).argmax(-1),
        "mean_prob": (p0 + p1).argmax(-1),
        "max_conf": np.where(p0.max(-1) >= p1.max(-1), a0, a1),
    }
    if misplaced_idx is not None:
        m0, m1 = a0 == misplaced_idx, a1 == misplaced_idx
        fused = (lp0 + lp1).argmax(-1)
        preds["misplaced_aware"] = np.where(m0 & ~m1, a1, np.where(m1 & ~m0, a0, fused))
    return preds


def scores(conf, labels):
    tp = np.diag(conf).astype(float)
    recall = tp / np.maximum(conf.sum(1), 1)
    precision = tp / np.maximum(conf.sum(0), 1)
    f1 = 2 * recall * precision / np.maximum(recall + precision, 1e-9)
    present = conf.sum(1) > 0
    return {"accuracy": float(tp.sum() / max(conf.sum(), 1)), "macro_f1": float(f1[present].mean()),
            "recall": {labels[i]: round(float(recall[i]), 3) for i in range(len(labels)) if present[i]},
            "frames": int(conf.sum())}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--placement", default="random", choices=["arc", "random", "mixed"])
    ap.add_argument("--sessions", type=int, default=24)
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--seed", type=int, default=7_000)
    ap.add_argument("--clutter", type=int, default=None)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default=None, help="결과 JSON")
    ap.add_argument("--dump", default=None, help="프레임별 두 센서 logits·라벨을 npz 로 (융합 규칙 학습용)")
    args = ap.parse_args()

    import torch
    from tofsim.model import PostureNet
    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    labels = list(ck.get("labels", LABELS))
    sensor = ck.get("sensor", {"height": 8, "width": 8})
    net = PostureNet(sensor["height"], sensor["width"], len(labels))
    net.load_state_dict(ck["model"])
    net.eval()
    C = len(labels)
    misplaced_idx = labels.index("misplaced") if "misplaced" in labels else None

    jobs = [(args.seed + i, args.placement, args.seconds, args.clutter) for i in range(args.sessions)]
    t0 = time.time()
    confs, sessions, dump = {}, [], []
    with mp.Pool(args.workers) as pool:
        for n, (dist, status, label, flags, meta) in enumerate(pool.imap_unordered(render_pair, jobs), 1):
            l0, l1 = infer(net, dist[0], status[0]), infer(net, dist[1], status[1])
            keep = (flags & FLAG_TRANSITION) == 0
            keep[:WARMUP] = False
            keep &= label < C
            if args.dump:
                dump.append((l0.astype(np.float32), l1.astype(np.float32), label.astype(np.int16), keep,
                             np.full(len(label), n - 1, dtype=np.int16), meta))
            preds = rule_preds(l0, l1, misplaced_idx)
            preds["either_correct"] = np.where(preds["left"] == label, preds["left"],
                                               np.where(preds["right"] == label, preds["right"], preds["left"]))
            per = {}
            for name, p in preds.items():
                cm = confs.setdefault(name, np.zeros((C, C), dtype=np.int64))
                np.add.at(cm, (label[keep], p[keep]), 1)
                per[name] = round(float((p[keep] == label[keep]).mean()), 3)
            meta.update(frames=int(keep.sum()), acc=per)
            sessions.append(meta)
            print(f"[{n}/{args.sessions}] seed {meta['seed']} az {meta['azimuth_deg']:+.0f} "
                  f"{'MISPLACED ' if meta['misplaced'] else ''}left {per['left']:.3f} right {per['right']:.3f} "
                  f"sum_logprob {per['sum_logprob']:.3f}  ({time.time() - t0:.0f}s)", flush=True)

    result = {"ckpt": args.ckpt, "placement": args.placement, "sessions": args.sessions, "seconds": args.seconds,
              "labels": labels, "rules": {k: scores(v, labels) for k, v in confs.items()},
              "confusion": {k: v.tolist() for k, v in confs.items()}, "per_session": sessions}
    print()
    print(f"{'rule':16s} {'acc':>6s} {'macroF1':>8s}  recall")
    for k, sc in result["rules"].items():
        rec = " ".join(f"{n[:5]}:{v:.2f}" for n, v in sc["recall"].items())
        print(f"{k:16s} {sc['accuracy']:6.3f} {sc['macro_f1']:8.3f}  {rec}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=1)
        print(f"-> {args.out}")
    if args.dump:
        np.savez_compressed(args.dump, l0=np.concatenate([d[0] for d in dump]), l1=np.concatenate([d[1] for d in dump]),
                            label=np.concatenate([d[2] for d in dump]), keep=np.concatenate([d[3] for d in dump]),
                            session=np.concatenate([d[4] for d in dump]),
                            az=np.array([d[5]["azimuth_deg"] for d in dump], dtype=np.float32),
                            misplaced=np.array([d[5]["misplaced"] for d in dump]), labels=np.array(labels))
        print(f"-> {args.dump}")


if __name__ == "__main__":
    main()
