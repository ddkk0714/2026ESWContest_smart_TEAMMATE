"""배포 후보 체크포인트를 같은 시험 세션(한 장치의 두 센서)으로 채점해 Pi 4 에 올릴 모델을 고른다.

    python bench_deploy.py render --out runs/bench --placement random --sessions 32 --seconds 240 --workers 8
    python bench_deploy.py render --out runs/bench --placement arc --sessions 16 --seconds 240 --workers 8
    python bench_deploy.py score --data runs/bench --ckpt a/ckpt_best.pt b/ckpt_best.pt ... --out runs/bench/score.json

render: 세션마다 두 센서 뷰(dist, status)와 라벨·플래그를 npz 로 저장(sample_session 의 scene·clutter 그대로).
score : 체크포인트마다 센서별 모델(각자 GRU) → log-softmax 평균 융합(hub/atlas-tof 와 같은 규칙)으로 채점.
        채점 규칙은 tofsim/evaluate.py 와 같다(전환 구간·세션 첫 45프레임 제외). 모델 클래스 수가 시험 라벨보다
        적으면(8라벨 모델에 forward_head 구간) 세 가지로 본다: all(틀린 것으로), no_fh(그 구간 빼고),
        fh_as_upright(거북목을 바른자세로 답하면 맞음 — 8라벨 제품이 할 수 있는 최선).
"""

from __future__ import annotations

import argparse
import glob
import json
import multiprocessing as mp
import os
import sys
import time
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WARMUP = 45


def render_one(job):
    seed, placement, seconds, clutter, scene = job
    from tofsim.mj_scene import MjDesk
    from tofsim.session import generate_frames, sample_session
    kw = {"placement": placement, "scene": scene}
    if clutter is not None:
        kw["clutter_max"] = clutter
    cfg0 = sample_session(np.random.default_rng(seed), **kw)
    arc = cfg0.meta["arc"]
    a_i = next(i for i, (_, _, pose) in enumerate(arc) if pose is cfg0.sensor_pose) // 2
    pair = arc[a_i * 2: a_i * 2 + 2]
    desk = MjDesk(cfg0.room, cfg0.person)
    D, S, labels, flags = [], [], None, None
    for _, _, pose in pair:
        rng = np.random.default_rng(seed)
        cfg = replace(sample_session(rng, **kw), sensor_pose=pose)
        d, s, l, f = [], [], [], []
        for _, dist, status, label, fl in generate_frames(cfg, seconds, rng, backend="mujoco", desk=desk):
            d.append(dist.copy()); s.append(status.copy()); l.append(label); f.append(fl)
        l, f = np.array(l, np.int16), np.array(f, np.uint8)
        if labels is None:
            labels, flags = l, f
        else:
            assert np.array_equal(labels, l), f"seed {seed}: motion differs between sensors"
        D.append(np.stack(d)); S.append(np.stack(s))
    return seed, np.stack(D).astype(np.int16), np.stack(S).astype(np.uint8), labels, flags, \
        float(cfg0.meta["azimuth_deg"]), bool(cfg0.meta.get("misplaced", False))


def cmd_render(a):
    from tofsim.session import LABELS
    os.makedirs(a.out, exist_ok=True)
    jobs = [(a.seed + i, a.placement, a.seconds, a.clutter, a.scene) for i in range(a.sessions)]
    t0 = time.time()
    res = []
    with mp.Pool(a.workers) as pool:
        for n, r in enumerate(pool.imap_unordered(render_one, jobs), 1):
            res.append(r)
            print(f"[{n}/{len(jobs)}] seed {r[0]} az {r[5]:+.0f}{' MISPLACED' if r[6] else ''} ({time.time() - t0:.0f}s)", flush=True)
    res.sort(key=lambda r: r[0])
    lens = [len(r[3]) for r in res]
    path = os.path.join(a.out, f"bench_{a.placement}_{a.scene}_{a.sessions}x{int(a.seconds)}.npz")
    np.savez_compressed(path, dist=np.concatenate([r[1] for r in res], axis=1), status=np.concatenate([r[2] for r in res], axis=1),
                        label=np.concatenate([r[3] for r in res]), flags=np.concatenate([r[4] for r in res]),
                        starts=np.cumsum([0] + lens[:-1]), seeds=np.array([r[0] for r in res]),
                        az=np.array([r[5] for r in res], np.float32), misplaced=np.array([r[6] for r in res]),
                        labels=np.array(LABELS), placement=a.placement, scene=a.scene)
    counts = np.bincount(np.concatenate([r[3] for r in res]), minlength=len(LABELS))
    print(f"-> {path}  frames {sum(lens):,}  label frames " + " ".join(f"{LABELS[i]}:{c}" for i, c in enumerate(counts) if c))


def load_net(path):
    import torch
    from tofsim.model import PostureNet
    ck = torch.load(path, map_location="cpu", weights_only=False)
    sd = ck["model"] if "model" in ck else ck["state_dict"]
    classes = sd["head.weight"].shape[0]
    hidden = sd["gru.weight_hh_l0"].shape[1]
    net = PostureNet(8, 8, classes, hidden)
    net.load_state_dict(sd)
    net.eval()
    return net, classes, hidden, ck.get("state", {})


def infer(net, dist, status, chunk=600):
    import torch
    from tofsim.data import to_input
    x = torch.from_numpy(to_input(dist, status))[None]
    out, h = [], None
    with torch.no_grad():
        for i in range(0, x.shape[1], chunk):
            lo, h = net(x[:, i:i + chunk], h)
            out.append(lo[0].numpy())
    return np.concatenate(out)


def log_softmax(z):
    z = z - z.max(-1, keepdims=True)
    return z - np.log(np.exp(z).sum(-1, keepdims=True))


def metrics(y, p, C, names):
    cm = np.zeros((C, C), np.int64)
    np.add.at(cm, (y, p), 1)
    tp = np.diag(cm).astype(float)
    r = tp / np.maximum(cm.sum(1), 1)
    pr = tp / np.maximum(cm.sum(0), 1)
    f = 2 * r * pr / np.maximum(r + pr, 1e-9)
    present = cm.sum(1) > 0
    return {"acc": round(float(tp.sum() / max(cm.sum(), 1)), 4), "macro_f1": round(float(f[present].mean()), 4),
            "recall": {names[i]: round(float(r[i]), 3) for i in range(C) if present[i]}, "frames": int(cm.sum()),
            "confusion": cm.tolist()}


_DATA = None


def _load_data(data_dir):
    global _DATA
    if _DATA is None:
        files = sorted(glob.glob(os.path.join(data_dir, "bench_*.npz")))
        _DATA = {os.path.basename(f)[6:-4]: dict(np.load(f, allow_pickle=False)) for f in files}
    return _DATA


def score_one(job):
    """체크포인트 하나 채점 (워커 프로세스, torch 스레드 1개)."""
    ck_path, name, data_dir = job
    import torch
    torch.set_num_threads(1)
    data = _load_data(data_dir)
    net, classes, hidden, st = load_net(ck_path)
    res = {"ckpt": ck_path, "classes": classes, "hidden": hidden, "step": st.get("step"), "frames_trained": st.get("frames")}
    for tag, d in data.items():
        names = [str(s) for s in d["labels"]]
        FH = names.index("forward_head") if "forward_head" in names else None
        Cl = len(names)
        starts = list(d["starts"]); ends = starts[1:] + [len(d["label"])]
        ys, pf, p0, p1, keep_all = [], [], [], [], []
        for s, e in zip(starts, ends):
            l0 = infer(net, d["dist"][0, s:e], d["status"][0, s:e])
            l1 = infer(net, d["dist"][1, s:e], d["status"][1, s:e])
            fused = (log_softmax(l0) + log_softmax(l1)).argmax(-1)
            keep = (d["flags"][s:e] & 1) == 0
            keep[:WARMUP] = False
            ys.append(d["label"][s:e][keep]); pf.append(fused[keep]); p0.append(l0.argmax(-1)[keep]); p1.append(l1.argmax(-1)[keep])
        y = np.concatenate(ys).astype(np.int64)
        pf, p0, p1 = (np.concatenate(v).astype(np.int64) for v in (pf, p0, p1))
        out = {"fused_all": metrics(y, pf, Cl, names), "sensor0": metrics(y, p0, Cl, names)["acc"],
               "sensor1": metrics(y, p1, Cl, names)["acc"]}
        if FH is not None and classes <= FH:
            m = y != FH
            out["fused_no_fh"] = metrics(y[m], pf[m], Cl, names)
            pfu = pf.copy(); yu = y.copy(); yu[yu == FH] = names.index("upright")
            out["fused_fh_as_upright"] = metrics(yu, pfu, Cl, names)
        res[tag] = out
    return name, res


def cmd_score(a):
    names = a.names or a.ckpt
    jobs = [(ck, nm, a.data) for ck, nm in zip(a.ckpt, names)]
    results = {}
    with mp.Pool(min(a.procs, len(jobs))) as pool:
        for name, res in pool.imap_unordered(score_one, jobs):
            results[name] = res
            line = f"{name:16s} C={res['classes']} h={res['hidden']}"
            for tag, out in res.items():
                if not isinstance(out, dict) or "fused_all" not in out:
                    continue
                fa = out["fused_all"]
                line += f" | {tag}: all {fa['acc']:.3f}/{fa['macro_f1']:.3f}"
                if "fused_fh_as_upright" in out:
                    fu = out["fused_fh_as_upright"]
                    line += f" fh→up {fu['acc']:.3f}/{fu['macro_f1']:.3f}"
                line += f" s0 {out['sensor0']:.3f} s1 {out['sensor1']:.3f}"
            print(line, flush=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=1)
        print(f"-> {a.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", required=True)
    r.add_argument("--placement", default="random", choices=["arc", "random", "mixed"])
    r.add_argument("--scene", default="rich")
    r.add_argument("--clutter", type=int, default=4)
    r.add_argument("--sessions", type=int, default=32)
    r.add_argument("--seconds", type=float, default=240.0)
    r.add_argument("--seed", type=int, default=31_000)
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    s = sub.add_parser("score")
    s.add_argument("--data", required=True)
    s.add_argument("--ckpt", nargs="+", required=True)
    s.add_argument("--names", nargs="+", default=None)
    s.add_argument("--out", default=None)
    s.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="체크포인트를 동시에 채점할 프로세스 수")
    a = ap.parse_args()
    cmd_render(a) if a.cmd == "render" else cmd_score(a)


if __name__ == "__main__":
    main()
