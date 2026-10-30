"""Where do inside-surface hits happen, and how much longer are they than neighbouring rays?"""
import collections

import mujoco
import numpy as np

from tofsim import sample_session
from tofsim.mj_scene import MjDesk, sensor_frame
from tofsim.session import _target_pose

n = 48
tx = np.tan(np.radians(22.5))
g = (np.arange(n) + 0.5) / n * 2 - 1
gx, gy = np.meshgrid(g * tx, g * tx)
local = np.stack([gx.ravel(), -gy.ravel(), np.ones(n * n)], 1)
by_geom = collections.Counter()
excess = []
for seed in range(4):
    rng = np.random.default_rng(100 + seed)
    cfg = sample_session(rng)
    desk = MjDesk(cfg.room, cfg.person)
    m, d = desk.model, desk.data
    origin, R = sensor_frame(cfg.sensor_pose)
    dirs = local @ R.T
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    for kind in ("upright", "face_down", "chin_rest"):
        desk.set_pose(_target_pose(kind, rng, cfg.meta.get("limits")))
        geomid = np.zeros(n * n, dtype=np.int32); dist = np.zeros(n * n); normal = np.zeros(n * n * 3)
        mujoco.mj_multiRay(m, d, origin.astype(np.float64), dirs.ravel(), desk._geomgroup_all, 1, -1, geomid, dist, normal, n * n, 10.0)
        normal = normal.reshape(-1, 3)
        person = np.array([m.geom_group[int(i)] == 1 if i >= 0 else False for i in geomid])
        inside = person & (np.einsum("ij,ij->i", normal, dirs) > 0)
        D = dist.reshape(n, n); P = person.reshape(n, n); I = inside.reshape(n, n)
        for r, c in zip(*np.nonzero(I)):
            by_geom[mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, int(geomid[r * n + c]))] += 1
            nb = [D[rr, cc] for rr in range(max(0, r - 1), min(n, r + 2)) for cc in range(max(0, c - 1), min(n, c + 2))
                  if (rr, cc) != (r, c) and P[rr, cc] and not I[rr, cc]]
            if nb:
                excess.append((D[r, c] - np.median(nb)) * 100)
excess = np.array(excess)
print("inside hits by piece:", dict(by_geom.most_common()))
print(f"distance minus neighbours (cm): p10 {np.percentile(excess, 10):.1f}  p50 {np.percentile(excess, 50):.1f}  "
      f"p90 {np.percentile(excess, 90):.1f}  | > 3 cm: {np.mean(excess > 3) * 100:.0f}%  | < -3 cm: {np.mean(excess < -3) * 100:.0f}%")
