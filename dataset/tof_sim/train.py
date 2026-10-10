"""Train the streaming posture classifier on endless synthetic sessions.

Made to run unattended for days: worker processes keep generating fresh
sessions, the trainer consumes and discards them, so disk use stays flat
(checkpoints, the fixed validation set and logs only).

    python train.py --run l8_v1                      # start (Ctrl+C saves and stops)
    python train.py --run l8_v1 --resume             # continue after a stop or crash
    python train.py --run l8_v1 --resume --hours 336 # stop by itself after two weeks
    python train.py --run l8_v1 --real data/real.npz # also score real labelled captures

Outputs in runs/<run>/: train.log, metrics.jsonl, status.json,
ckpt_last.pt (every --ckpt-min), ckpt_best.pt (best macro-F1 on the
validation set).
"""

from __future__ import annotations

import argparse
import collections
import json
import multiprocessing as mp
import os
import queue
import signal
import sys
import time
import traceback

import numpy as np
import torch
import torch.nn.functional as F

from tofsim.data import build_valset, load_valset, to_input, worker_main
from tofsim.model import PostureNet, count_params
from tofsim.sensor import PRESETS
from tofsim.session import FLAG_TRANSITION, LABELS

HERE = os.path.dirname(os.path.abspath(__file__))


class Logger:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def atomic_save(obj, path):
    tmp = path + ".tmp"
    torch.save(obj, tmp)
    os.replace(tmp, path)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Evaluation


@torch.no_grad()
def evaluate(model, data: dict, warmup_frames: int = 45, chunk: int = 600) -> dict:
    """Per-frame scores over whole sessions, each run from a fresh state.

    Frames in transitions and the first `warmup_frames` of each session are
    not scored (the label is ambiguous there).
    """
    model.eval()
    dist, status, label = data["dist"], data["status"], data["label"].astype(np.int64)
    flags = data.get("flags", np.zeros(len(label), dtype=np.uint8))
    starts = list(data.get("starts", np.array([0])))
    ends = starts[1:] + [len(label)]
    C = len(LABELS)
    conf = np.zeros((C, C), dtype=np.int64)
    for s, e in zip(starts, ends):
        x = torch.from_numpy(to_input(dist[s:e], status[s:e]))[None]
        h = None
        preds = []
        for i in range(0, x.shape[1], chunk):
            logits, h = model(x[:, i:i + chunk], h)
            preds.append(logits.argmax(-1)[0].numpy())
        pred = np.concatenate(preds)
        y = label[s:e]
        keep = (flags[s:e] & FLAG_TRANSITION) == 0
        keep[:warmup_frames] = False
        np.add.at(conf, (y[keep], pred[keep]), 1)
    model.train()
    tp = np.diag(conf).astype(float)
    recall = tp / np.maximum(conf.sum(1), 1)
    precision = tp / np.maximum(conf.sum(0), 1)
    f1 = 2 * recall * precision / np.maximum(recall + precision, 1e-9)
    present = conf.sum(1) > 0
    return {
        "accuracy": float(tp.sum() / max(conf.sum(), 1)),
        "macro_f1": float(f1[present].mean()) if present.any() else 0.0,
        "recall": {LABELS[i]: round(float(recall[i]), 4) for i in range(C) if present[i]},
        "precision": {LABELS[i]: round(float(precision[i]), 4) for i in range(C) if present[i]},
        "confusion": conf.tolist(),
        "frames": int(conf.sum()),
    }


def format_eval(ev: dict) -> str:
    rec = " ".join(f"{k}:{v:.2f}" for k, v in ev["recall"].items())
    return f"acc {ev['accuracy']:.3f} macroF1 {ev['macro_f1']:.3f} | recall {rec}"


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--sensor", default="vl53l8cx", choices=list(PRESETS))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--hours", type=float, default=0.0, help="stop after this many hours (0 = run until stopped)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--streams", type=int, default=40, help="parallel sessions per batch")
    ap.add_argument("--chunk", type=int, default=64, help="frames per stream per step (truncated BPTT)")
    ap.add_argument("--threads", type=int, default=2, help="torch threads for the trainer")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lr-half-life-h", type=float, default=48.0)
    ap.add_argument("--transition-weight", type=float, default=0.3)
    ap.add_argument("--replay", type=int, default=3, help="extra updates per fresh batch, drawn from recent batches")
    ap.add_argument("--replay-size", type=int, default=64, help="recent batches kept for replay")
    ap.add_argument("--eval-min", type=float, default=30.0)
    ap.add_argument("--ckpt-min", type=float, default=10.0)
    ap.add_argument("--val-sessions", type=int, default=24)
    ap.add_argument("--val-seconds", type=float, default=300.0)
    ap.add_argument("--real", nargs="*", default=[], help="npz files of real labelled captures to score at each eval")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    run_dir = os.path.join(HERE, "runs", args.run)
    os.makedirs(run_dir, exist_ok=True)
    log = Logger(os.path.join(run_dir, "train.log"))
    torch.set_num_threads(args.threads)
    spec = PRESETS[args.sensor]

    ctx = mp.get_context("spawn")
    val_path = os.path.join(HERE, "runs", f"valset_{args.sensor}_{args.val_sessions}x{int(args.val_seconds)}.npz")
    if not os.path.exists(val_path):
        log(f"building validation set -> {val_path}")
        with ctx.Pool(args.workers) as pool:
            build_valset(val_path, args.sensor, args.val_sessions, args.val_seconds, log=log, pool=pool)
    val = load_valset(val_path)
    reals = {os.path.basename(p): load_valset(p) for p in args.real}
    log(f"validation set: {len(val['label'])} frames, {len(val['starts'])} sessions")

    model = PostureNet(spec.height, spec.width, len(LABELS))
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    state = {"step": 0, "frames": 0, "train_seconds": 0.0, "best_f1": -1.0,
             "label_counts": [1.0] * len(LABELS), "seed": args.seed}
    last_path = os.path.join(run_dir, "ckpt_last.pt")
    if args.resume and os.path.exists(last_path):
        ck = torch.load(last_path, map_location="cpu")
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        state.update(ck["state"])
        log(f"resumed at step {state['step']}, {state['frames']:,} frames, "
            f"{state['train_seconds'] / 3600:.1f} h trained, best macro-F1 {state['best_f1']:.3f}")
    else:
        log(f"new run: {count_params(model):,} parameters, sensor {spec.name} {spec.width}x{spec.height}")
    write_json(os.path.join(run_dir, "config.json"), vars(args))

    # Workers. A resumed run uses a new base seed so it does not replay the same sessions.
    base_seed = args.seed * 7919 + state["step"]
    B, T, W = args.streams, args.chunk, args.workers
    streams_of = [list(range(w, B, W)) for w in range(W)]
    stop = ctx.Event()
    queues = [ctx.Queue(maxsize=max(4, 3 * len(s))) for s in streams_of]
    procs = []

    def spawn(w):
        p = ctx.Process(target=worker_main, daemon=True,
                        args=(w, streams_of[w], queues[w], T, args.sensor, base_seed + 1_000 * w + int(time.time()) % 997,
                              args.transition_weight, stop))
        p.start()
        return p

    for w in range(W):
        procs.append(spawn(w))
    log(f"{W} workers, {B} streams x {T} frames per step")

    h = torch.zeros(1, B, model.gru.hidden_size)
    interrupted = {"flag": False}

    def on_sigint(_sig, _frm):
        interrupted["flag"] = True
        log("stop requested, finishing the step and saving")

    signal.signal(signal.SIGINT, on_sigint)

    def save(path):
        atomic_save({"model": model.state_dict(), "opt": opt.state_dict(), "state": state,
                     "labels": LABELS, "sensor": spec.__dict__}, path)

    t_start = time.time()
    t_last_ckpt = t_last_eval = t_last_status = time.time()
    trained_at_start = state["train_seconds"]
    window = {"loss": 0.0, "correct": 0.0, "weight": 0.0, "frames": 0, "t": time.time()}
    counts = np.array(state["label_counts"], dtype=np.float64)
    replay = collections.deque(maxlen=args.replay_size)
    pending = {s: collections.deque() for s in range(B)}
    exit_code = 0

    try:
        while not interrupted["flag"]:
            if args.hours and (time.time() - t_start) / 3600 >= args.hours:
                log(f"reached --hours {args.hours}")
                break
            # One chunk per stream, matched by stream id. Chunks that arrive early
            # (a stream's next chunk before another stream's current one) wait in
            # `pending` for the next step, so a batch never mixes up streams.
            for w in range(W):
                while any(not pending[s] for s in streams_of[w]):
                    try:
                        c = queues[w].get(timeout=5.0)
                        pending[c.stream].append(c)
                    except queue.Empty:
                        if not procs[w].is_alive():
                            log(f"worker {w} died (exit {procs[w].exitcode}), restarting")
                            procs[w] = spawn(w)
                        if interrupted["flag"]:
                            raise KeyboardInterrupt
            batch = [pending[s].popleft() for s in range(B)]
            x = torch.from_numpy(np.stack([c.x for c in batch]))
            y = torch.from_numpy(np.stack([c.y for c in batch]))
            wt = torch.from_numpy(np.stack([c.w for c in batch]))
            reset = torch.tensor([c.reset for c in batch])
            h[:, reset] = 0.0

            # Learning rate decays by wall-clock training time (resumable, no total needed)
            hours = (trained_at_start + time.time() - t_start) / 3600
            lr = args.lr * max(0.05, 0.5 ** (hours / args.lr_half_life_h))
            for g in opt.param_groups:
                g["lr"] = lr

            # Class weights ~ 1/sqrt(frequency), from running label counts
            counts = 0.999 * counts + np.bincount(y.numpy().ravel(), minlength=len(LABELS))
            cw = 1.0 / np.sqrt(counts / counts.sum() + 1e-3)
            cw = torch.tensor(cw / cw.mean(), dtype=torch.float32)

            def update(xb, yb, wb, hb):
                logits_b, h_out = model(xb, hb)
                ce = F.cross_entropy(logits_b.reshape(-1, len(LABELS)), yb.reshape(-1), reduction="none")
                wflat = wb.reshape(-1) * cw[yb.reshape(-1)]
                loss_b = (ce * wflat).sum() / wflat.sum().clamp_min(1e-6)
                opt.zero_grad(set_to_none=True)
                loss_b.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                return logits_b, h_out, loss_b

            h_in = h.clone()
            logits, h_new, loss = update(x, y, wt, h)
            h = h_new.detach()
            # Generation is the bottleneck; reuse recent batches for extra updates.
            # Each keeps the recurrent state it started from, so replays stay in context.
            replay.append((x, y, wt, h_in))
            for _ in range(args.replay if len(replay) >= 8 else 0):
                xb, yb, wb, hb = replay[int(np.random.randint(len(replay)))]
                update(xb, yb, wb, hb)
            state["updates"] = state.get("updates", 0) + 1 + (args.replay if len(replay) >= 8 else 0)

            state["step"] += 1
            state["frames"] += B * T
            state["label_counts"] = counts.tolist()
            with torch.no_grad():
                pred = logits.argmax(-1)
                m = wt > 0
                window["correct"] += float((pred[m] == y[m]).sum())
                window["weight"] += float(m.sum())
                window["loss"] += float(loss) * float(m.sum())
                window["frames"] += B * T

            now = time.time()
            state["train_seconds"] = trained_at_start + now - t_start
            if now - t_last_status >= 60:
                dt = now - window["t"]
                fps = window["frames"] / max(dt, 1e-6)
                msg = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "step": state["step"], "frames": state["frames"],
                       "fps": round(fps), "lr": lr, "train_loss": window["loss"] / max(window["weight"], 1),
                       "train_acc": window["correct"] / max(window["weight"], 1),
                       "hours": round(state["train_seconds"] / 3600, 2), "best_f1": state["best_f1"]}
                log(f"step {msg['step']} | {msg['frames']:,} frames ({msg['fps']} f/s) | loss {msg['train_loss']:.3f} "
                    f"acc {msg['train_acc']:.3f} | lr {lr:.2e} | {msg['hours']} h")
                with open(os.path.join(run_dir, "metrics.jsonl"), "a", encoding="utf-8") as fp:
                    fp.write(json.dumps({"kind": "train", **msg}) + "\n")
                write_json(os.path.join(run_dir, "status.json"), msg)
                window = {"loss": 0.0, "correct": 0.0, "weight": 0.0, "frames": 0, "t": now}
                t_last_status = now
            if now - t_last_ckpt >= args.ckpt_min * 60:
                save(last_path)
                t_last_ckpt = now
            if now - t_last_eval >= args.eval_min * 60:
                ev = evaluate(model, val)
                rec = {"kind": "eval", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "step": state["step"],
                       "frames": state["frames"], "val": ev}
                log("eval sim  " + format_eval(ev))
                for name, data in reals.items():
                    rv = evaluate(model, data)
                    rec[f"real:{name}"] = rv
                    log(f"eval real {name} " + format_eval(rv))
                with open(os.path.join(run_dir, "metrics.jsonl"), "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(rec) + "\n")
                if ev["macro_f1"] > state["best_f1"]:
                    state["best_f1"] = ev["macro_f1"]
                    save(os.path.join(run_dir, "ckpt_best.pt"))
                    log(f"new best macro-F1 {ev['macro_f1']:.3f}")
                save(last_path)
                t_last_eval = time.time()
    except KeyboardInterrupt:
        log("stopped by request")
    except Exception:
        # Log the traceback to train.log as well: stderr of a background run is easy to lose.
        log("crashed:\n" + traceback.format_exc())
        exit_code = 1
    finally:
        save(last_path)
        log(f"saved {last_path} at step {state['step']}")
        stop.set()
        for p in procs:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
    return exit_code   # non-zero makes run_forever.ps1 restart with --resume


if __name__ == "__main__":
    sys.exit(main())
