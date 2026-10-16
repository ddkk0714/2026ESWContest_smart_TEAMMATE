"""Do rays slip through seams between body pieces? Cast a dense grid from the sensor at the person
and count hits whose surface normal faces away from the ray (= hit the inside of a piece).
Also renders the pose at normal opacity. usage: check_seams.py <mode>"""
import sys

import imageio
import mujoco
import numpy as np

from tofsim import sample_session
from tofsim.body import Pose
from tofsim.mj_scene import MjDesk, sensor_frame

mode = sys.argv[1] if len(sys.argv) > 1 else "typing"
cfg = sample_session(np.random.default_rng(7))
desk = MjDesk(cfg.room, cfg.person)
m, d = desk.model, desk.data
desk.set_pose(Pose(arms=mode))
origin, R = sensor_frame(cfg.sensor_pose)
n = 64
tx = np.tan(np.radians(22.5))
g = (np.arange(n) + 0.5) / n * 2 - 1
gx, gy = np.meshgrid(g * tx, g * tx)
local = np.stack([gx.ravel(), -gy.ravel(), np.ones(n * n)], 1)
dirs = local @ R.T
dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
geomid = np.zeros(n * n, dtype=np.int32); dist = np.zeros(n * n); normal = np.zeros(n * n * 3)
mujoco.mj_multiRay(m, d, origin.astype(np.float64), dirs.ravel(), desk._geomgroup_all, 1, -1, geomid, dist, normal, n * n, 10.0)
normal = normal.reshape(-1, 3)
names = np.array([mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, int(i)) if i >= 0 else "" for i in geomid])
person = np.array([m.geom_group[int(i)] == 1 if i >= 0 else False for i in geomid])
back = person & (np.einsum("ij,ij->i", normal, dirs) > 0)
print(f"{mode}: rays hitting the person {person.sum()} / {n * n}; of those, inside-surface hits {back.sum()} "
      f"({back.sum() / max(person.sum(), 1) * 100:.1f}%)")
if back.any():
    u, c = np.unique(names[back], return_counts=True)
    print("  inside hits by piece:", dict(zip(u, c)))
    print("  extra distance on those rays (cm) p50/p90:", np.round(np.percentile(dist[back] * 100 - 0, [50, 90]), 1), "(absolute distance; compare with neighbours)")
desk.model.vis.global_.offwidth = 1280
r = mujoco.Renderer(m, height=720, width=1280)
cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE
p = cfg.person
cam.lookat[:] = [p.x_offset, cfg.room.desk_depth + p.hip_z - 0.1, p.seat_height + 0.55]
for name, az, el, dist_ in (("shoulder_side", 180, -8, 1.1), ("shoulder_front", 250, -15, 1.2)):
    cam.azimuth, cam.elevation, cam.distance = az, el, dist_
    r.update_scene(d, cam)
    imageio.imwrite(f"snaps/seam_{name}.png", r.render())
print("renders written")
