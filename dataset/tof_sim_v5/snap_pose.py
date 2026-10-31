"""Still renders of one pose from several angles, for checking the body. usage: snap_pose.py <arms mode> <out prefix>"""
import sys

import imageio
import mujoco
import numpy as np

from tofsim import sample_session
from tofsim.body import Pose
from tofsim.mj_scene import MjDesk

mode, out = sys.argv[1], sys.argv[2]
cfg = sample_session(np.random.default_rng(7))
desk = MjDesk(cfg.room, cfg.person)
desk.model.vis.global_.offwidth = 1280
desk.set_pose(Pose(arms=mode))
r = mujoco.Renderer(desk.model, height=720, width=1280)
cam = mujoco.MjvCamera()
cam.type = mujoco.mjtCamera.mjCAMERA_FREE
p = cfg.person
cam.lookat[:] = [p.x_offset, cfg.room.desk_depth + p.hip_z - 0.2, p.seat_height + 0.45]
for name, az, el, dist in (("side", 90, -10, 1.9), ("front", 180, -10, 1.9), ("top", 180, -75, 2.2), ("back3q", 40, -20, 2.2)):
    cam.azimuth, cam.elevation, cam.distance = az, el, dist
    r.update_scene(desk.data, cam)
    imageio.imwrite(f"{out}_{name}.png", r.render())
print("ok", mode, [f"{out}_{n}.png" for n in ("side", "front", "top", "back3q")])
