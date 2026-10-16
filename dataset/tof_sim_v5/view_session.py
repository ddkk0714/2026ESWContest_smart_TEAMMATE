"""Watch a synthetic session in the MuJoCo viewer.

The person moves through the posture timeline in real time. Each of the 8×8
zones is drawn as a dot where its beam lands (red = near, blue = far, gray =
no valid return), so you can see what the sensor sees. The ground-truth label
is printed in this terminal whenever it changes.

    python view_session.py                 # random session (seed 1)
    python view_session.py --seed 7 --speed 2
"""

import argparse
import time

import mujoco
import mujoco.viewer
import numpy as np

from tofsim import LABELS, generate_frames, sample_session
from tofsim.mj_scene import MjDesk, sensor_frame
from tofsim.sensor import Sensor

VALID = np.array([5, 6, 9, 10])


def color(mm):
    t = float(np.clip((mm - 300) / 1700.0, 0, 1))
    return np.array([1 - t, 0.8 * (1 - abs(t - 0.5) * 2), t, 1.0], dtype=np.float32)


def draw_beams(scn, origin, dirs, dist, status):
    scn.ngeom = 0
    for z in range(dirs.shape[0]):
        if scn.ngeom >= scn.maxgeom:
            break
        d = int(dist.flat[z])
        ok = d >= 0 and int(status.flat[z]) in VALID
        rng = (d if ok else 600) / 1000.0
        pos = origin + dirs[z] * rng
        rgba = color(d) if ok else np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
        mujoco.mjv_initGeom(scn.geoms[scn.ngeom], mujoco.mjtGeom.mjGEOM_SPHERE,
                            np.array([0.012, 0, 0]), pos, np.eye(3).ravel(), rgba)
        scn.ngeom += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    cfg = sample_session(rng, "vl53l8cx")
    desk = MjDesk(cfg.room, cfg.person)
    origin, R = sensor_frame(cfg.sensor_pose)
    centers = Sensor(cfg.spec, cfg.model, np.random.default_rng(0)).zone_centers(R)
    period = 1.0 / (cfg.fps * args.speed)

    with mujoco.viewer.launch_passive(desk.model, desk.data) as v:
        v.cam.lookat[:] = [0.0, cfg.room.desk_depth + cfg.person.hip_z - 0.2, cfg.person.seat_height + 0.4]
        v.cam.azimuth, v.cam.elevation, v.cam.distance = 35.0, -18.0, 2.4
        last = None
        t_next = time.perf_counter()
        for t_ms, dist, status, label, flags in generate_frames(cfg, args.seconds, rng, backend="mujoco", desk=desk):
            if not v.is_running():
                break
            with v.lock():
                draw_beams(v.user_scn, origin, centers, dist, status)
            v.sync()
            if label != last:
                print(f"{t_ms / 1000:7.1f} s  {LABELS[label]}")
                last = label
            t_next += period
            time.sleep(max(0.0, t_next - time.perf_counter()))


if __name__ == "__main__":
    main()
