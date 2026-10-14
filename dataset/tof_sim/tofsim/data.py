"""Turning frames into model input, streaming them from worker processes,
and the fixed validation set.

Model input per frame (also what the board would compute):
    channel 0: distance / 4000 mm where the zone is valid, else 0
    channel 1: 1 where the zone is valid (status 5, 6, 9, 10), else 0
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass

import numpy as np

from .session import FLAG_TRANSITION, LABELS, generate_frames, sample_session

VALID_STATUS = np.array([5, 6, 9, 10])
MAX_MM = 4000.0
SESSION_SECONDS = (240.0, 900.0)


def to_input(dist: np.ndarray, status: np.ndarray) -> np.ndarray:
    """(…,H,W) int16 / uint8 -> (…,2,H,W) float32."""
    valid = np.isin(status, VALID_STATUS) & (dist >= 0)
    x = np.where(valid, dist.astype(np.float32) / MAX_MM, 0.0).astype(np.float32)
    return np.stack([x, valid.astype(np.float32)], axis=-3)


@dataclass
class Chunk:
    stream: int
    reset: bool          # a new session starts with this chunk: clear the recurrent state
    x: np.ndarray        # (T,2,H,W) float32
    y: np.ndarray        # (T,) int64
    w: np.ndarray        # (T,) float32 per-frame loss weight


def _session_iter(seed: int, sensor: str, backend: str = "mujoco", placement: str = "arc", clutter_max=None):
    rng = np.random.default_rng(seed)
    cfg = sample_session(rng, sensor, placement=placement, clutter_max=clutter_max)
    total = float(rng.uniform(*SESSION_SECONDS))
    yield from generate_frames(cfg, total, rng, backend=backend)


def worker_main(worker: int, streams: list[int], q, chunk_len: int, sensor: str, base_seed: int,
                transition_weight: float, stop_event, backend: str = "mujoco", placement: str = "arc",
                clutter_max=None):
    """Runs forever: round-robin over its streams, one chunk per stream per round."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # the trainer handles Ctrl+C
    session_no = {s: 0 for s in streams}
    its, fresh = {}, {}
    for s in streams:
        its[s] = _session_iter(_seed(base_seed, s, 0), sensor, backend, placement, clutter_max)
        fresh[s] = True
    parent = mp.parent_process()
    while not stop_event.is_set():
        if parent is not None and not parent.is_alive():
            return  # the trainer is gone; do not linger as an orphan
        for s in streams:
            # Exactly one chunk per stream per round — the trainer relies on it.
            frames = []
            while True:
                while len(frames) < chunk_len:
                    try:
                        frames.append(next(its[s]))
                    except StopIteration:
                        break
                if frames:
                    break
                # The session ended exactly on a chunk boundary: start the next one now.
                session_no[s] += 1
                its[s] = _session_iter(_seed(base_seed, s, session_no[s]), sensor, backend, placement, clutter_max)
                fresh[s] = True
            reset = fresh[s]
            fresh[s] = False
            n_real = len(frames)
            if n_real < chunk_len:
                # Session over mid-chunk: pad with weight 0; the next chunk of this
                # stream starts a new session and resets the recurrent state.
                session_no[s] += 1
                its[s] = _session_iter(_seed(base_seed, s, session_no[s]), sensor, backend, placement, clutter_max)
                fresh[s] = True
                frames = frames + [frames[-1]] * (chunk_len - n_real)
            dist = np.stack([f[1] for f in frames])
            status = np.stack([f[2] for f in frames])
            y = np.array([f[3] for f in frames], dtype=np.int64)
            flags = np.array([f[4] for f in frames])
            w = np.where(flags & FLAG_TRANSITION, transition_weight, 1.0).astype(np.float32)
            w[n_real:] = 0.0
            chunk = Chunk(s, reset, to_input(dist, status), y, w)
            while not stop_event.is_set():
                try:
                    q.put(chunk, timeout=1.0)
                    break
                except queue.Full:
                    continue


def _seed(base: int, stream: int, session: int) -> int:
    return (base * 1_000_003 + stream * 10_007 + session) % (2 ** 63)


# ---------------------------------------------------------------------------
# Validation set: fixed seeds, generated once and cached


def _val_session(args):
    seed, sensor, seconds, backend, placement, clutter_max = args
    rng = np.random.default_rng(seed)
    cfg = sample_session(rng, sensor, placement=placement, clutter_max=clutter_max)
    frames = list(generate_frames(cfg, seconds, rng, backend=backend))
    return (np.stack([f[1] for f in frames]), np.stack([f[2] for f in frames]),
            np.array([f[3] for f in frames], dtype=np.int8), np.array([f[4] for f in frames], dtype=np.uint8),
            float(cfg.meta.get("azimuth_deg", 0.0)), int(cfg.meta.get("sensor_index", 0)))


def build_valset(path: str, sensor: str, sessions: int, seconds: float, seed: int = 424242, log=print, pool=None,
                 backend: str = "mujoco", placement: str = "arc", clutter_max=None):
    if os.path.exists(path):
        return load_valset(path)
    dists, stats, labels, flagss, starts, azs, sis = [], [], [], [], [], [], []
    t0 = time.time()
    pos = 0
    jobs = [(seed + i, sensor, seconds, backend, placement, clutter_max) for i in range(sessions)]
    results = pool.imap(_val_session, jobs) if pool is not None else map(_val_session, jobs)
    for i, (d, s, y, f, az, si) in enumerate(results):
        azs.append(az)
        sis.append(si)
        dists.append(d)
        stats.append(s)
        labels.append(y)
        flagss.append(f)
        starts.append(pos)
        pos += len(y)
        log(f"valset {i + 1}/{sessions} ({time.time() - t0:.0f} s)")
    dist = np.concatenate(dists)
    status = np.concatenate(stats)
    flags = np.concatenate(flagss)
    label = np.concatenate(labels)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, dist=dist, status=status, label=label, flags=flags,
                        starts=np.array(starts), labels=np.array(LABELS), placement=np.array(placement),
                        session_az=np.array(azs), session_si=np.array(sis))
    os.replace(tmp, path)
    return load_valset(path)


def load_valset(path: str) -> dict:
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}
