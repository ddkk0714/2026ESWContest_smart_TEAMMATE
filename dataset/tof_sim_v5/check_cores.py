"""Do the invisible inner cores ever poke through the skin? Cast dense rays from all around the
person in several poses and count first hits on core_* geoms."""
import collections

import mujoco
import numpy as np

from tofsim import sample_session
from tofsim.body import Pose
from tofsim.mj_scene import MjDesk
from tofsim.session import _target_pose

tot = collections.Counter()
person_hits = 0
for seed in range(4):
    rng = np.random.default_rng(300 + seed)
    cfg = sample_session(rng)
    desk = MjDesk(cfg.room, cfg.person)
    m, d = desk.model, desk.data
    p = cfg.person
    centre = np.array([p.x_offset, cfg.room.desk_depth + p.hip_z - 0.1, p.seat_height + 0.4])
    poses = [Pose(arms="typing"), Pose(arms="lap"), _target_pose("chin_rest", rng), _target_pose("face_down", rng),
             _target_pose("recline", rng), _target_pose("drowsy", rng)]
    for pose in poses:
        desk.set_pose(pose)
        for az in range(0, 360, 30):                       # cameras all around, slightly above
            a = np.radians(az)
            origin = centre + np.array([1.6 * np.cos(a), 1.6 * np.sin(a), 0.5])
            n = 40
            g = (np.arange(n) + 0.5) / n * 2 - 1
            fwd = centre - origin; fwd /= np.linalg.norm(fwd)
            right = np.cross(fwd, [0, 0, 1]); right /= np.linalg.norm(right)
            up = np.cross(right, fwd)
            gx, gy = np.meshgrid(g * 0.45, g * 0.45)
            dirs = fwd[None] + gx.ravel()[:, None] * right[None] + gy.ravel()[:, None] * up[None]
            dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
            geomid = np.zeros(n * n, dtype=np.int32); dist = np.zeros(n * n)
            mujoco.mj_multiRay(m, d, origin.astype(np.float64), dirs.ravel(), desk._geomgroup_all, 1, -1, geomid, dist, None, n * n, 10.0)
            for gi in geomid:
                if gi < 0 or m.geom_group[gi] != 1:
                    continue
                person_hits += 1
                name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, int(gi))
                if name.startswith("core_"):
                    tot[name] += 1
print(f"person hits {person_hits}; first hits on inner cores: {sum(tot.values())} ({sum(tot.values()) / person_hits * 100:.3f}%)")
print(dict(tot.most_common()))
