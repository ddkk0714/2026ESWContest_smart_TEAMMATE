"""Recorded ToF lines -> the `--real` npz that train.py and evaluate score, and a quick model score on it.

    python real_from_record.py --labels real_protocol_v1.csv --out runs/real_20261015_a.npz rec.jsonl
    python real_from_record.py --labels real_protocol_v1.csv --out runs/real_a.npz rec.jsonl@COM5 rec.jsonl@COM6
    python real_from_record.py --out runs/replay.npz replay_s0.jsonl replay_s1.jsonl     # sim replays carry "label"
    python real_from_record.py --score ../tof-sim_ubai_random/export_20261014_2206_best/posture_net.pt runs/real_20261015_a.npz
    python train.py --run ... --real runs/real_20261015_a.npz                                  # scored at every eval

Inputs are `tof_live.py --record` files. Its lines are wrapped as {"host_t": <epoch s>, "port": "COM5", "raw": "<line>"};
every port in a file becomes its own sensor, sorted by port name (`file@COM5` takes that port only).
Unwrapped lines (older recordings, tools/sim_to_toflines.py) are one sensor per file. Both firmware line formats are
read with the same parser as tof_live.py: bring-up CSV `F,<ms>,<64 distances>[,<64 status>]` and tof_logger JSON.

Frames are taken the way the Pi 4 service takes them: every 100 ms the newest frame (the firmware ranges at 15 Hz,
the model runs at 10 Hz). A gap of more than 1 s or a clock going backwards starts a new session, as the service
then restarts the model state.

Labels come from a per-line "label" field (sim replays) or from --labels, a CSV `start_s,end_s,label[,what]`
(label = a name in tofsim.session.LABELS, or "skip" / "transition"). Its times are seconds from --t0 (the
protocol_start_*.txt that protocol_timer.py writes, or an epoch time), else from the start of the recording (the
earliest host time over all inputs when the lines carry one, else the first frame of each sensor).
Unlabelled time and --margin s on either side of every label change are flagged as transitions, which evaluate
does not score.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tof_live import Source, orient  # noqa: E402  same line parser and grid orientation as the live runner
from tofsim.session import FLAG_TRANSITION, LABELS  # noqa: E402

TICK_MS = 100            # the model's frame period (10 Hz)
GAP_MS = 1000            # silence that restarts the model state on the board


def split_arg(arg: str):
    """'rec.jsonl@COM5' -> ('rec.jsonl', 'COM5'); a plain path -> (path, None)."""
    if "@" in arg and not os.path.exists(arg):
        path, port = arg.rsplit("@", 1)
        return path, port
    return arg, None


def read_sensors(path: str, port_filter, flip_rows: bool, flip_cols: bool):
    """{port: [(ms, dist[8,8] int16, status[8,8] uint8, label or None, host_t or None)]}, has_status, first host_t.
    Ports keep the order they first appear in; unwrapped lines go under the port None. The first host time is over
    every wrapped line of the file, whatever the port filter: it is when the recording started."""
    sensors, has_status, first_host = {}, True, None
    with open(path, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            port, host_t = None, None
            if line.startswith('{"host_t"'):
                try:
                    w = json.loads(line)
                except ValueError:
                    continue
                port, host_t, line = w.get("port"), w.get("host_t"), w.get("raw", "")
                if host_t is not None and (first_host is None or host_t < first_host):
                    first_host = host_t
            if port_filter is not None and port != port_filter:
                continue
            obj = Source._parse(line)
            if not obj or obj.get("t") != "tof" or len(obj.get("d", [])) != 64 or len(obj.get("st", [])) != 64:
                continue
            if obj.get("has_status") is False:
                has_status = False
            d = orient(np.asarray(obj["d"], np.int16).reshape(8, 8), flip_rows, flip_cols)
            st = orient(np.asarray(obj["st"], np.uint8).reshape(8, 8), flip_rows, flip_cols)
            d = np.where(d > 0, d, -1).astype(np.int16)          # the npz convention: no target = -1
            sensors.setdefault(port, []).append((int(obj["ms"]), d, st, obj.get("label"), host_t))
    return sensors, has_status, first_host


def split_runs(frames):
    """Cut where the clock goes backwards or the sensor was silent for more than GAP_MS."""
    runs, cur = [], []
    for f in frames:
        if cur and (f[0] < cur[-1][0] or f[0] - cur[-1][0] > GAP_MS):
            runs.append(cur)
            cur = []
        cur.append(f)
    if cur:
        runs.append(cur)
    return runs


def resample(run):
    """Newest frame at every 100 ms tick from the first frame: [(t_s from the run start, frame)]."""
    ms0, ms1 = run[0][0], run[-1][0]
    out, j = [], 0
    for k in range((ms1 - ms0) // TICK_MS + 1):
        t = ms0 + k * TICK_MS
        while j + 1 < len(run) and run[j + 1][0] <= t:
            j += 1
        out.append(((t - ms0) / 1000.0, run[j]))
    return out


def read_labels(path: str):
    rows = []
    with open(path, encoding="utf-8-sig") as fp:
        for r in csv.reader(fp):
            if not r or r[0].strip().startswith("#") or r[0].strip() == "start_s":
                continue
            a, b, name = float(r[0]), float(r[1]), r[2].strip()
            name = "skip" if name == "transition" else name
            if name != "skip" and name not in LABELS:
                sys.exit(f"{path}: unknown label {name!r} (use one of {LABELS} or skip)")
            rows.append((a, b, name))
    return sorted(rows)


def label_frames(times, line_labels, rows, margin):
    """label int8 [T], flags uint8 [T]; `times` in the label file's seconds."""
    T = len(times)
    y = np.zeros(T, np.int8)
    known = np.zeros(T, bool)
    t = np.asarray(times)
    if rows is None:
        for i, name in enumerate(line_labels):
            if name in LABELS:
                y[i], known[i] = LABELS.index(name), True
    else:
        for a, b, name in rows:
            m = (t >= a) & (t < b)
            if name != "skip":
                y[m], known[m] = LABELS.index(name), True
            else:
                known[m] = False
    flags = np.where(known, 0, FLAG_TRANSITION).astype(np.uint8)
    if margin > 0:                                    # around every change of label, the operator's timing is loose
        for i in np.nonzero(np.diff(y.astype(int)) != 0)[0]:
            flags[np.abs(t - t[i + 1]) <= margin] |= FLAG_TRANSITION
    return y, flags


def read_t0(arg: str) -> float:
    """An epoch time, or the first line of protocol_timer.py's protocol_start_*.txt."""
    if os.path.exists(arg):
        with open(arg, encoding="utf-8") as fp:
            return float(fp.readline().split()[0])
    return float(arg)


def convert(args):
    rows = read_labels(args.labels) if args.labels else None
    sensors, starts_host = [], []                      # (name, frames), each file's recording start
    for arg in args.files:
        path, port_filter = split_arg(arg)
        got, has_status, first_host = read_sensors(path, port_filter, args.flip_rows, args.flip_cols)
        starts_host.append(first_host)
        if not got:
            print(f"{arg}: no frames")
        if got and not has_status:
            print(f"{arg}: the firmware sent no status values; valid = distance > 0 (the model was trained on status)")
        for port, frames in sorted(got.items(), key=lambda kv: str(kv[0])):
            sensors.append((os.path.basename(path) + (f"@{port}" if port else ""), frames))
    runs = [(name, r, run) for name, frames in sensors for r, run in enumerate(split_runs(frames)) if len(run) >= 2]
    if not runs:
        sys.exit("nothing converted")
    with_host = all(run[0][4] is not None for _, _, run in runs)
    T0 = min(h for h in starts_host if h is not None) if with_host else None
    if args.t0:
        if not with_host:
            sys.exit("--t0 needs recordings with host times (tof_live.py --record of 2026-10-15 or later)")
        T0 = read_t0(args.t0)
        print(f"label times from --t0 ({T0:.3f}; the recording started {min(h for h in starts_host if h is not None) - T0:+.1f} s from it)")
    else:
        print("label times from " + ("the recording start (host clock)" if with_host else "each sensor's first frame"))
    dists, stats, ys, fls, starts, names, host0 = [], [], [], [], [], [], []
    pos = 0
    for name, r, run in runs:
        samp = resample(run)
        off = (run[0][4] - T0) if with_host else 0.0
        times = [t + off for t, _ in samp]
        if rows is None and not any(f[3] for _, f in samp):
            sys.exit(f"{name}: no per-line labels; give --labels")
        y, fl = label_frames(times, [f[3] for _, f in samp], rows, args.margin)
        dists.append(np.stack([f[1] for _, f in samp]))
        stats.append(np.stack([f[2] for _, f in samp]))
        ys.append(y); fls.append(fl); starts.append(pos); names.append(f"{name}#{r}")
        host0.append(run[0][4] if with_host else np.nan)
        pos += len(y)
        kept = (fl & FLAG_TRANSITION) == 0
        share = {LABELS[i]: round(float(np.mean(y[kept] == i)), 3) for i in np.unique(y[kept])}
        hz = (len(run) - 1) / max(1e-6, (run[-1][0] - run[0][0]) / 1000.0)
        print(f"{names[-1]}: {len(run)} frames at {hz:.1f} Hz from t={off:.1f} s -> {len(y)} at 10 Hz ({len(y) / 10:.0f} s), "
              f"scored {kept.sum()} | label share {share}")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, dist=np.concatenate(dists), status=np.concatenate(stats), label=np.concatenate(ys),
                        flags=np.concatenate(fls), starts=np.array(starts), labels=np.array(LABELS),
                        sources=np.array(names), session_host_t0=np.array(host0, dtype=np.float64))
    print(f"-> {args.out}: {pos} frames, {len(starts)} sessions")


def score(args):
    import torch

    from tofsim.data import to_input
    from tofsim.evaluate import evaluate, format_eval
    from tofsim.model import PostureNet

    ck = torch.load(args.score, map_location="cpu", weights_only=False)
    sd = ck.get("model") or ck.get("state_dict")
    labels = list(ck.get("labels", LABELS))
    net = PostureNet(8, 8, len(labels), hidden=int(sd["gru.weight_hh_l0"].shape[1]))
    net.load_state_dict(sd)
    net.eval()
    with np.load(args.files[0], allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    if "forward_head" in LABELS and "forward_head" not in labels:
        # a model without forward_head can do no better than "upright" there: score it that way
        data["label"] = np.where(data["label"] == LABELS.index("forward_head"), LABELS.index("upright"), data["label"])
    names = [str(s) for s in data.get("sources", [])]
    print(f"model {args.score}: {len(labels)} labels, hidden {net.gru.hidden_size}")
    print("per sensor:", format_eval(evaluate(net, data)))
    starts = list(data["starts"]) + [len(data["label"])]
    if len(starts) != 3:
        return
    # two sessions: also the board's fusion (sum of log-probabilities), lined up on the host clock when known
    h0 = data.get("session_host_t0", np.array([np.nan, np.nan]))
    shift = int(round((h0[1] - h0[0]) * 10)) if np.all(np.isfinite(h0)) else 0     # frames sensor 1 starts later
    a0, b0 = starts[0] + max(0, shift), starts[1] + max(0, -shift)
    T = min(starts[1] - a0, starts[2] - b0)
    if T <= 45:
        print("both sensors: too little overlap to fuse"); return
    logp = []
    with torch.no_grad():
        for s0, s in ((starts[0], a0), (starts[1], b0)):            # run each from its own start, keep the overlap
            x = torch.from_numpy(to_input(data["dist"][s0:s + T], data["status"][s0:s + T]))[None]
            logp.append(torch.log_softmax(net(x)[0][0], -1).numpy()[s - s0:])
    pred = (logp[0] + logp[1]).argmax(-1)
    y = data["label"][a0:a0 + T].astype(int)
    keep = ((data["flags"][a0:a0 + T] | data["flags"][b0:b0 + T]) & FLAG_TRANSITION) == 0
    keep[:45] = False
    acc = float((pred[keep] == y[keep]).mean()) if keep.any() else float("nan")
    rec = {LABELS[c]: round(float((pred[keep & (y == c)] == c).mean()), 2) for c in np.unique(y[keep])}
    print(f"both sensors ({' + '.join(names[:2])}, offset {shift} frames): acc {acc:.3f} | recall {rec}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="recorded line files (file or file@PORT), or with --score one npz")
    ap.add_argument("--out", help="npz to write")
    ap.add_argument("--labels", help="CSV start_s,end_s,label[,what], seconds from the start of the recording")
    ap.add_argument("--margin", type=float, default=2.0, help="seconds around each label change left unscored")
    ap.add_argument("--t0", help="protocol_start_*.txt from protocol_timer.py (or an epoch time): label time zero")
    ap.add_argument("--flip-rows", action="store_true", help="as tof_live.py --flip-rows")
    ap.add_argument("--flip-cols", action="store_true", help="as tof_live.py --flip-cols")
    ap.add_argument("--score", help="checkpoint or export posture_net.pt to score on the npz given as the file")
    args = ap.parse_args()
    if args.score:
        score(args)
    elif args.out:
        convert(args)
    else:
        ap.error("give --out (convert) or --score (score an npz)")


if __name__ == "__main__":
    main()
