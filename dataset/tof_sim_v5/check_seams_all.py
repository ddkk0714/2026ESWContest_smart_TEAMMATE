"""Inside-surface (seam hole) hits for every posture and arm mode, several bodies and sensor spots."""
import numpy as np
import mujoco

from tofsim import sample_session
from tofsim.mj_scene import MjDesk, sensor_frame
from tofsim.session import _target_pose

n = 48
tx = np.tan(np.radians(22.5))
g = (np.arange(n) + 0.5) / n * 2 - 1
gx, gy = np.meshgrid(g * tx, g * tx)
local = np.stack([gx.ravel(), -gy.ravel(), np.ones(n * n)], 1)
tot_person = tot_inside = 0
rows = []
for seed in range(5):
    rng = np.random.default_rng(100 + seed)
    cfg = sample_session(rng)
    desk = MjDesk(cfg.room, cfg.person)
    m, d = desk.model, desk.data
    origin, R = sensor_frame(cfg.sensor_pose)
    dirs = local @ R.T
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    for kind in ("upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy"):
        for rep in range(3):
            pose = _target_pose(kind, rng, cfg.meta.get("limits"))
            desk.set_pose(pose)
            geomid = np.zeros(n * n, dtype=np.int32); dist = np.zeros(n * n); normal = np.zeros(n * n * 3)
            mujoco.mj_multiRay(m, d, origin.astype(np.float64), dirs.ravel(), desk._geomgroup_all, 1, -1, geomid, dist, normal, n * n, 10.0)
            normal = normal.reshape(-1, 3)
            person = np.array([m.geom_group[int(i)] == 1 if i >= 0 else False for i in geomid])
            inside = person & (np.einsum("ij,ij->i", normal, dirs) > 0)
            tot_person += person.sum(); tot_inside += inside.sum()
            rows.append((kind, pose.arms, int(person.sum()), int(inside.sum())))
by_kind = {}
for kind, arms, p, i in rows:
    a = by_kind.setdefault(kind, [0, 0]); a[0] += p; a[1] += i
print(f"all: {tot_inside} inside hits of {tot_person} person hits ({tot_inside / tot_person * 100:.2f}%)")
for kind, (p, i) in by_kind.items():
    print(f"  {kind:13s} {i:4d} / {p:6d}  ({i / max(p, 1) * 100:.2f}%)")
worst = sorted(rows, key=lambda r: -r[3])[:5]
print("worst poses:", [(k, a, i, p) for k, a, p, i in worst])
