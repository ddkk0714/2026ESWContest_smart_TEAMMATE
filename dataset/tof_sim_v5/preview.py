"""Print a few synthetic frames per posture so the scene can be checked by eye.

    python preview.py                  # one session, one frame per segment kind
    python preview.py --sensor vl53l9cx_8x6 --seed 3
    python preview.py --bench 2000     # frames/s of one worker
"""

import argparse
import time

import numpy as np

from tofsim import LABELS, generate_frames, sample_session
from tofsim.session import FLAG_TRANSITION


def show(dist, status, title):
    print(title)
    for r in range(dist.shape[0]):
        cells = []
        for c in range(dist.shape[1]):
            d, s = int(dist[r, c]), int(status[r, c])
            cells.append("   .  " if d < 0 or s not in (5, 6, 9, 10) else f"{d:5d} ")
        print("".join(cells))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sensor", default="vl53l8cx")
    ap.add_argument("--backend", default="mujoco", choices=["mujoco", "numpy"])
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--bench", type=int, default=0, help="time this many frames instead of printing")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    cfg = sample_session(rng, args.sensor)
    print(f"sensor {cfg.spec.name} {cfg.spec.width}x{cfg.spec.height}, mount y={cfg.sensor_pose.position[1]:.2f} m "
          f"pitch {cfg.sensor_pose.pitch_down_deg:.0f} deg, person hip {cfg.room.desk_depth + cfg.person.hip_z:.2f} m, "
          f"wall {cfg.room.wall_z:.2f} m, ambient x{cfg.model.ambient:.1f}\n")

    if args.bench:
        t0 = time.perf_counter()
        n = 0
        for _ in generate_frames(cfg, args.bench / cfg.fps + 1, rng, backend=args.backend):
            n += 1
            if n >= args.bench:
                break
        dt = time.perf_counter() - t0
        print(f"{n} frames in {dt:.2f} s = {n / dt:.0f} frames/s per worker")
        return

    seen = set()
    counts = np.zeros(len(LABELS), dtype=int)
    last_label, steady = None, 0
    for t_ms, dist, status, label, flags in generate_frames(cfg, args.seconds, rng, backend=args.backend):
        counts[label] += 1
        steady = steady + 1 if label == last_label and not (flags & FLAG_TRANSITION) else 0
        last_label = label
        if label not in seen and steady == 45:      # 3 s into a settled segment
            seen.add(label)
            show(dist, status, f"t={t_ms / 1000:.1f}s  {LABELS[label]}")
    print("frames per label:", {LABELS[i]: int(c) for i, c in enumerate(counts)})


if __name__ == "__main__":
    main()
