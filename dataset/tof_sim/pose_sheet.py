"""Render the seated person in every posture / arm mode from two angles into one image.

    python pose_sheet.py --seed 7 --out pose_sheet.png
"""
import argparse

import imageio
import mujoco
import numpy as np

from tofsim import sample_session
from tofsim.body import Pose
from tofsim.mj_scene import MjDesk
from tofsim.session import _target_pose

POSES = [
    ("upright / typing", lambda rng: Pose(arms="typing")),
    ("upright / writing", lambda rng: Pose(arms="writing")),
    ("upright / lap", lambda rng: Pose(arms="lap")),
    ("upright / crossed", lambda rng: Pose(arms="crossed")),
    ("upright / phone", lambda rng: Pose(arms="phone")),
    ("recline / behind_head", lambda rng: Pose(trunk_pitch=np.radians(-25), arms="behind_head")),
    ("lean_forward", lambda rng: _target_pose("lean_forward", rng)),
    ("chin_rest", lambda rng: _target_pose("chin_rest", rng)),
    ("face_down", lambda rng: _target_pose("face_down", rng)),
    ("drowsy", lambda rng: _target_pose("drowsy", rng)),
    ("mouse", lambda rng: Pose(arms="mouse")),
    ("book", lambda rng: Pose(trunk_pitch=np.radians(-8), neck_pitch=np.radians(20), arms="book")),
    ("phone_call / other typing", lambda rng: Pose(arms="phone_call", other_arms="typing")),
    ("eat (phase 0.5) / other lap", lambda rng: Pose(arms="eat", other_arms="lap", arm_phase=0.5)),
    ("bag (lean right)", lambda rng: Pose(trunk_roll=np.radians(-22), trunk_pitch=np.radians(10), arms="bag", other_arms="lap")),
]
VIEWS = [("front", 90, -8, 1.5), ("left side", 180, -6, 1.5), ("back-left 3/4", 240, -18, 1.7)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="pose_sheet.png")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    cfg = sample_session(rng, scene="plain")      # no desk/room furniture on the sheet
    desk = MjDesk(cfg.room, cfg.person)
    m = desk.model
    m.geom_rgba[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "monitor")][3] = 0.0   # the monitor would hide the front view
    m.vis.global_.offwidth = 640
    r = mujoco.Renderer(m, height=480, width=640)
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    opt = mujoco.MjvOption()
    opt.geomgroup[3] = 1            # props / bystanders live in group 3 (rays see them, the default render does not)
    p = cfg.person
    cam.lookat[:] = [p.x_offset, cfg.room.desk_depth + p.hip_z - 0.15, p.seat_height + 0.5]
    rows = []
    for name, make in POSES:
        desk.set_pose(make(rng))
        tiles = []
        for _, az, el, dist in VIEWS:
            cam.azimuth, cam.elevation, cam.distance = az, el, dist
            r.update_scene(desk.data, cam, opt)
            tiles.append(r.render())
        rows.append(np.concatenate(tiles, axis=1))
        print("rendered", name)
    grid = np.concatenate([np.concatenate(rows[i:i + 2], axis=0) for i in range(0, len(rows), 2)], axis=1) if False else None
    # 5 rows x 2 poses: each cell = two views side by side
    cells = [np.concatenate([rows[i], rows[i + 1] if i + 1 < len(rows) else np.zeros_like(rows[i])], axis=1)
             for i in range(0, len(rows), 2)]
    sheet = np.concatenate(cells, axis=0)
    imageio.imwrite(args.out, sheet)
    print(f"wrote {args.out} {sheet.shape[1]}x{sheet.shape[0]} | body: {p.smplx['gender'] if p.smplx else 'capsule'}, "
          f"scale {p.scale:.2f} | order: " + "; ".join(n for n, _ in POSES))


if __name__ == "__main__":
    main()
