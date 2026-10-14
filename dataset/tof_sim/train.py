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

import os

# One math-library thread per process: ten generator workers each spinning up
# their own BLAS/OpenMP pool only fight over the cores. Must precede numpy.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import collections
import json
import multiprocessing as mp
import queue
import signal
import sys
import time
import traceback

import numpy as np

from tofsim.data import build_valset, load_valset, worker_main
from tofsim.sensor import PRESETS
from tofsim.session import LABELS, PLACEMENT_MODES, SCENE_MODES

# Windows starts worker processes by re-importing this file as "__mp_main__".
# They only generate frames, so keep PyTorch out of them.
if __name__ == "__main__":
    import torch
    import torch.nn.functional as F

    from tofsim.evaluate import evaluate, format_eval
    from tofsim.model import PostureNet, count_params

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


def load_weights(model, sd: dict, log) -> bool:
    """Load weights into the model. A head trained with fewer classes is widened: its rows are
    kept, the new classes keep their fresh initialisation. Returns False when that happened."""
    sd = dict(sd)
    own = model.state_dict()
    same = True
    for k in ("head.weight", "head.bias"):
        if k in sd and tuple(sd[k].shape) != tuple(own[k].shape):
            n = min(sd[k].shape[0], own[k].shape[0])
            widened = own[k].clone()
            widened[:n] = sd[k][:n].to(widened.device)
            log(f"{k}: {sd[k].shape[0]} classes in the checkpoint, {own[k].shape[0]} now (new: {', '.join(LABELS[n:])})")
            sd[k] = widened
            same = False
    model.load_state_dict(sd)
    return same


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--sensor", default="vl53l8cx", choices=list(PRESETS))
    ap.add_argument("--backend", default="mujoco", choices=["mujoco", "numpy"], help="scene/ray-cast engine")
    ap.add_argument("--placement", default="arc", choices=list(PLACEMENT_MODES),
                    help="where the device stands: arc (the demo arc, aimed at the user) | random (anywhere a user "
                         "might put it; unusable spots are labelled misplaced) | mixed (half and half)")
    ap.add_argument("--clutter", type=int, default=None,
                    help="boxes/shelves against the back wall, 0..N per session (default: session.CLUTTER_MAX)")
    ap.add_argument("--scene", default="rich", choices=list(SCENE_MODES),
                    help="rich: things on the desk and around the room, each by chance | plain: bare desk and wall")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--init", default="", help="start from the weights of this checkpoint (any run; a head with "
                                               "fewer classes is widened). Not used when --resume finds ckpt_last.pt")
    ap.add_argument("--hours", type=float, default=0.0, help="stop after this many hours (0 = run until stopped)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--streams", type=int, default=40, help="parallel sessions per batch")
    ap.add_argument("--chunk", type=int, default=64, help="frames per stream per step (truncated BPTT)")
    ap.add_argument("--threads", type=int, default=2, help="torch threads for the trainer")
    ap.add_argument("--device", default="auto", help="trainer device: auto (cuda if present) | cpu | cuda")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lr-half-life-h", type=float, default=48.0)
    ap.add_argument("--transition-weight", type=float, default=0.3)
    ap.add_argument("--replay", type=int, default=5, help="extra updates per fresh batch, drawn from recent batches")
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
    # Workers always generate on the CPU; only the trainer and evaluation move to the GPU.
    dev = torch.device(args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    spec = PRESETS[args.sensor]

    ctx = mp.get_context("spawn")

    # Validation sets: one drawn like the training sessions (it picks ckpt_best), plus the plain
    # arc when training elsewhere, so the cost on the demo placement stays visible.
    def val_file(placement):
        tag = (("" if placement == "arc" else f"{placement}_") + ("" if args.clutter is None else f"clutter{args.clutter}_")
               + ("rich_" if args.scene == "rich" else ""))
        return os.path.join(HERE, "runs", f"valset_{args.backend}_{args.sensor}_{tag}{args.val_sessions}x{int(args.val_seconds)}.npz")

    vals = {}
    for placement in dict.fromkeys([args.placement, "arc"]):
        path = val_file(placement)
        if not os.path.exists(path):
            log(f"building validation set ({placement}) -> {path}")
            with ctx.Pool(args.workers) as pool:
                build_valset(path, args.sensor, args.val_sessions, args.val_seconds, log=log, pool=pool,
                             backend=args.backend, placement=placement, clutter_max=args.clutter, scene=args.scene)
        vals[placement] = load_valset(path)
        log(f"validation set ({placement}): {len(vals[placement]['label'])} frames, {len(vals[placement]['starts'])} sessions")
    val = vals[args.placement]
    reals = {os.path.basename(p): load_valset(p) for p in args.real}

    model = PostureNet(spec.height, spec.width, len(LABELS)).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    state = {"step": 0, "frames": 0, "train_seconds": 0.0, "best_f1": -1.0,
             "label_counts": [1.0] * len(LABELS), "seed": args.seed}
    last_path = os.path.join(run_dir, "ckpt_last.pt")
    if args.resume and os.path.exists(last_path):
        ck = torch.load(last_path, map_location="cpu")
        if load_weights(model, ck["model"], log):
            opt.load_state_dict(ck["opt"])
        else:
            log("head widened: the optimizer state starts fresh")
        state.update(ck["state"])
        state["label_counts"] = (list(state["label_counts"]) + [1.0] * len(LABELS))[:len(LABELS)]
        log(f"resumed at step {state['step']}, {state['frames']:,} frames, "
            f"{state['train_seconds'] / 3600:.1f} h trained, best macro-F1 {state['best_f1']:.3f}")
    else:
        log(f"new run: {count_params(model):,} parameters, sensor {spec.name} {spec.width}x{spec.height}")
        if args.init:
            ck = torch.load(args.init, map_location="cpu")
            load_weights(model, ck["model"], log)
            state["init_from"] = os.path.abspath(args.init)
            st = ck.get("state", {})
            log(f"weights from {args.init} (step {st.get('step', '?')}, labels {list(ck.get('labels', []))})")
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
                              args.transition_weight, stop, args.backend, args.placement, args.clutter, args.scene))
        p.start()
        return p

    for w in range(W):
        procs.append(spawn(w))
    gpu = f" ({torch.cuda.get_device_name(dev)})" if dev.type == "cuda" else ""
    log(f"{W} workers, {B} streams x {T} frames per step, placement {args.placement}, clutter {args.clutter}, "
        f"scene {args.scene}, trainer on {dev}{gpu}")

    h = torch.zeros(1, B, model.gru.hidden_size, device=dev)
    interrupted = {"flag": False}

    def on_stop_signal(sig, _frm):
        interrupted["flag"] = True
        log(f"멈춤 요청({signal.Signals(sig).name}): 이번 단계를 마치고 저장")

    # Ctrl+C, 그리고 Slurm scancel·시간 제한이 보내는 SIGTERM 모두 저장하고 끝낸다
    signal.signal(signal.SIGINT, on_stop_signal)
    signal.signal(signal.SIGTERM, on_stop_signal)
    # 파일로도 조작할 수 있다 (서버에서 프로세스를 직접 건드리기 어려울 때)
    stop_file = os.path.join(run_dir, "STOP")          # 있으면 저장하고 종료
    snapshot_file = os.path.join(run_dir, "SNAPSHOT")  # 있으면 바로 저장하고 파일은 지움
    for leftover in (stop_file, snapshot_file):
        # 지난번 멈출 때 남은 표시는 지운다. train.py 를 새로 시작했다는 것 자체가 다시 돌리겠다는 뜻이다
        # (slurm_train.sh·run_forever.sh 는 STOP 이 있으면 train.py 를 아예 부르지 않는다)
        if os.path.exists(leftover):
            os.remove(leftover)
            log(f"지난번에 남은 {os.path.basename(leftover)} 파일을 지움")
    t_last_file_check = 0.0

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
            y_np = np.stack([c.y for c in batch])
            x = torch.from_numpy(np.stack([c.x for c in batch])).to(dev)
            y = torch.from_numpy(y_np).to(dev)
            wt = torch.from_numpy(np.stack([c.w for c in batch])).to(dev)
            reset = torch.tensor([c.reset for c in batch], device=dev)
            h[:, reset] = 0.0

            # Learning rate decays by wall-clock training time (resumable, no total needed)
            hours = (trained_at_start + time.time() - t_start) / 3600
            lr = args.lr * max(0.05, 0.5 ** (hours / args.lr_half_life_h))
            for g in opt.param_groups:
                g["lr"] = lr

            # Class weights ~ 1/sqrt(frequency), from running label counts
            counts = 0.999 * counts + np.bincount(y_np.ravel(), minlength=len(LABELS))
            cw = 1.0 / np.sqrt(counts / counts.sum() + 1e-3)
            cw = torch.tensor(cw / cw.mean(), dtype=torch.float32, device=dev)

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
            if now - t_last_file_check >= 5:
                t_last_file_check = now
                if os.path.exists(stop_file):
                    log("STOP 파일 발견: 저장하고 종료")
                    break
                if os.path.exists(snapshot_file):
                    save(last_path)
                    t_last_ckpt = now
                    try:
                        os.remove(snapshot_file)
                    except OSError:
                        pass
                    log(f"SNAPSHOT 요청: {last_path} 저장 (step {state['step']})")
            if now - t_last_ckpt >= args.ckpt_min * 60:
                save(last_path)
                t_last_ckpt = now
            if now - t_last_eval >= args.eval_min * 60:
                ev = evaluate(model, val)
                rec = {"kind": "eval", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "step": state["step"],
                       "frames": state["frames"], "val": ev}
                log("eval sim  " + format_eval(ev))
                for placement, data in vals.items():
                    if placement != args.placement:
                        pv = evaluate(model, data)
                        rec[f"val:{placement}"] = pv
                        log(f"eval sim ({placement}) " + format_eval(pv))
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
