"""Render one synthetic session to an mp4 with MuJoCo's own renderer (no web viewer).

    python record_session.py --seed 7 --seconds 120 --out session_7.mp4

The camera slowly orbits the desk. Dots show where the chosen sensor's 64 beams land
(red = near, blue = far, grey = no valid return); the sensor device is drawn as a box.
Set TOFSIM_SMPLX_DIR to the folder that contains `smplx/SMPLX_NEUTRAL.npz` for SMPL-X bodies.
"""

import argparse
import os
import time

import imageio
import mujoco
import numpy as np

from tofsim import LABELS, generate_frames, sample_session
from tofsim.mj_scene import MjDesk, sensor_frame
from tofsim.sensor import Sensor
from tofsim.session import DEVICE_HALF_M

VALID = (5, 6, 9, 10)


def beam_color(mm):
    t = float(np.clip((mm - 300) / 1700.0, 0, 1))
    return np.array([1 - t, 0.8 * (1 - abs(t - 0.5) * 2), t, 1.0], dtype=np.float32)


def add_geom(scn, kind, size, pos, mat, rgba):
    if scn.ngeom >= scn.maxgeom:
        return
    mujoco.mjv_initGeom(scn.geoms[scn.ngeom], kind, np.asarray(size, dtype=np.float64),
                        np.asarray(pos, dtype=np.float64), np.asarray(mat, dtype=np.float64).ravel(),
                        np.asarray(rgba, dtype=np.float32))
    scn.ngeom += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--out", default="session.mp4")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--placement", default="arc", help="arc | random | mixed (tofsim.session.PLACEMENT_MODES)")
    ap.add_argument("--clutter", type=int, default=None, help="boxes against the back wall, 0..N (default: session.CLUTTER_MAX)")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    cfg = sample_session(rng, "vl53l8cx", placement=args.placement, clutter_max=args.clutter)
    desk = MjDesk(cfg.room, cfg.person)
    origin, R = sensor_frame(cfg.sensor_pose)
    centers = Sensor(cfg.spec, cfg.model, np.random.default_rng(0)).zone_centers(R)
    az, c, psi = next(dv for dv in cfg.meta["devices"] if dv[0] == cfg.meta["azimuth_deg"])
    dev_pos = np.array([c[0], c[2], cfg.room.desk_height + 0.073])          # body (x, y up, z) -> MuJoCo (x, y, z up)
    pr = np.radians(psi)
    dev_mat = np.array([[np.cos(pr), np.sin(pr), 0], [-np.sin(pr), np.cos(pr), 0], [0, 0, 1]])
    hw, hd = DEVICE_HALF_M
    p = cfg.person
    print(f"seed {args.seed}: {p.smplx['gender'] if p.smplx else 'capsule'} body, height scale {p.scale:.2f}, "
          f"device at {az:+.0f} deg, sensor {cfg.meta['sensor_index']}, radius {cfg.meta['radius_m']:.2f} m")

    desk.model.vis.global_.offwidth = max(args.width, desk.model.vis.global_.offwidth)
    desk.model.vis.global_.offheight = max(args.height, desk.model.vis.global_.offheight)
    renderer = mujoco.Renderer(desk.model, height=args.height, width=args.width)
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    look = np.array([p.x_offset, cfg.room.desk_depth + p.hip_z - 0.25, p.seat_height + 0.45])
    opt = mujoco.MjvOption()
    writer = imageio.get_writer(args.out, fps=int(cfg.fps), codec="libx264", quality=8, macro_block_size=1)
    t0 = time.time()
    n = 0
    last_label = None
    for t_ms, dist, status, label, flags in generate_frames(cfg, args.seconds, rng, backend="mujoco", desk=desk):
        t = t_ms / 1000.0
        cam.lookat[:] = look
        cam.azimuth = 150 + 25 * np.sin(2 * np.pi * t / 60.0)         # slow sweep around the front-left
        cam.elevation = -18 + 6 * np.sin(2 * np.pi * t / 45.0)
        cam.distance = 2.6
        renderer.update_scene(desk.data, cam, opt)
        scn = renderer.scene
        add_geom(scn, mujoco.mjtGeom.mjGEOM_BOX, [hw, hd, 0.073], dev_pos, dev_mat, [0.85, 0.85, 0.88, 1])
        for z in range(centers.shape[0]):
            d = int(dist.flat[z])
            ok = d >= 0 and int(status.flat[z]) in VALID
            end = origin + centers[z] * ((d if ok else 600) / 1000.0)
            add_geom(scn, mujoco.mjtGeom.mjGEOM_SPHERE, [0.012, 0, 0], end, np.eye(3),
                     beam_color(d) if ok else [0.5, 0.5, 0.5, 0.5])
        frame = renderer.render()
        writer.append_data(frame)
        n += 1
        if label != last_label:
            print(f"{t:7.1f} s  {LABELS[label]}")
            last_label = label
    writer.close()
    print(f"wrote {args.out}: {n} frames at {cfg.fps:.0f} fps in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
