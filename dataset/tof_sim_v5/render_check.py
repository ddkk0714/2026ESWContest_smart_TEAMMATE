"""Render the MuJoCo desk scene in each posture to PNG (offscreen), with the
simulated 8×8 sensor reading beside it — a quick visual check of the model.

    python render_check.py --seed 1 --out runs/render
"""

import argparse
import os
import struct
import zlib

import mujoco
import numpy as np

from tofsim.body import Pose
from tofsim.mj_scene import MjDesk, sensor_frame
from tofsim.sensor import Sensor
from tofsim.session import _target_pose, sample_session


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[r].tobytes() for r in range(h))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as fp:
        fp.write(png)


def depth_image(dist, status, size):
    """8×8 reading -> colored image (near = warm), invalid = gray."""
    H, W = dist.shape
    img = np.zeros((H, W, 3), dtype=np.uint8)
    valid = np.isin(status, [5, 6, 9, 10]) & (dist >= 0)
    t = np.clip((dist - 300) / 1700.0, 0, 1)
    img[..., 0] = (255 * (1 - t)).astype(np.uint8)
    img[..., 1] = (200 * (1 - np.abs(t - 0.5) * 2)).astype(np.uint8)
    img[..., 2] = (255 * t).astype(np.uint8)
    img[~valid] = 70
    rep = size // max(H, W)
    return np.kron(img, np.ones((rep, rep, 1), dtype=np.uint8))


def free_cameras(cfg):
    """Side view and a view from behind the monitor (where the sensor is)."""
    target = [0.0, cfg.room.desk_depth + cfg.person.hip_z - 0.1, cfg.person.seat_height + 0.45]
    views = []
    for az, el, dist in ((0.0, -12.0, 2.3), (90.0, -18.0, 2.2)):
        cam = mujoco.MjvCamera()
        cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        cam.lookat[:] = target
        cam.azimuth, cam.elevation, cam.distance = az, el, dist
        views.append(cam)
    return views


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="runs/render")
    ap.add_argument("--recline-arms", nargs="*", default=["lap", "behind_head", "crossed", "phone", "hanging"],
                    help="also render a recline with each of these arm modes")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    cfg = sample_session(rng, "vl53l8cx")
    desk = MjDesk(cfg.room, cfg.person)
    origin, R = sensor_frame(cfg.sensor_pose)
    sensor = Sensor(cfg.spec, cfg.model, rng)
    renderer = mujoco.Renderer(desk.model, 480, 640)
    views = free_cameras(cfg)
    kinds = ["upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy_nod", "away"]
    kinds += [f"recline_{a}" for a in args.recline_arms]
    for kind in kinds:
        if kind == "away":
            pose = None
        elif kind == "drowsy_nod":
            pose = _target_pose("drowsy", rng)
            pose.neck_pitch += np.radians(35)
        elif kind.startswith("recline_"):
            pose = _target_pose("recline", rng)
            pose.arms = kind[len("recline_"):]
        else:
            pose = _target_pose(kind, rng)
        desk.set_pose(pose)
        dist, status = sensor.measure(desk.cast, origin, R)
        panels = []
        for cam in views:
            renderer.update_scene(desk.data, camera=cam)
            panels.append(renderer.render())
        grid = depth_image(dist, status, 480)
        pad = np.full((480, 480, 3), 255, dtype=np.uint8)
        pad[:grid.shape[0], :grid.shape[1]] = grid
        frame = np.concatenate(panels + [pad], axis=1)
        path = os.path.join(args.out, f"{kind}.png")
        write_png(path, frame)
        print(path, "valid zones", int((np.isin(status, [5, 6, 9, 10]) & (dist >= 0)).sum()))


if __name__ == "__main__":
    main()
