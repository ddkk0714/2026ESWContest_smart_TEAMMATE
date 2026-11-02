"""Where do the arm meshes end up versus the joints? Also close-up renders with the trunk hidden."""
import imageio
import mujoco
import numpy as np

from tofsim import sample_session
from tofsim.body import Pose
from tofsim.mj_scene import MjDesk

cfg = sample_session(np.random.default_rng(7))
desk = MjDesk(cfg.room, cfg.person)
m, d = desk.model, desk.data
desk.set_pose(Pose(arms="typing"))
gid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, n)
np.set_printoptions(precision=3, suppress=True)
for side in ("l", "r"):
    S = d.xpos[desk.body(f"uarm_{side}")]; E = d.xpos[desk.body(f"farm_{side}")]; W = d.xpos[desk.body(f"hand_{side}")]
    print(f"{side}: shoulder {S} elbow {E} wrist {W} | |SE| {np.linalg.norm(E - S):.3f} |EW| {np.linalg.norm(W - E):.3f}")
    for part, a, b in ((f"uarm_{side}", S, E), (f"farm_{side}", E, W)):
        g = gid(part); mid = m.geom_dataid[g]
        va, vn = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
        v = m.mesh_vert[va:va + vn]
        world = v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        axis = (b - a) / np.linalg.norm(b - a)
        t = (world - a) @ axis                       # position of each vertex along the joint->joint line
        print(f"   {part}: mesh along the limb line from {t.min():.3f} to {t.max():.3f} (limb length {np.linalg.norm(b - a):.3f}), "
              f"centre {world.mean(0)} vs limb midpoint {(a + b) / 2}")
    print(f"   geom_pos uarm {m.geom_pos[gid(f'uarm_{side}')]}  mesh_pos {m.mesh_pos[m.geom_dataid[gid(f'uarm_{side}')]]}")
# close-up renders with the trunk/pelvis hidden
for n in ("trunk", "pelvis", "chair_back"):
    m.geom_rgba[gid(n)][3] = 0.15
desk.model.vis.global_.offwidth = 1280
r = mujoco.Renderer(m, height=720, width=1280)
cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE
p = cfg.person
cam.lookat[:] = [p.x_offset, cfg.room.desk_depth + p.hip_z - 0.15, p.seat_height + 0.5]
for name, az, el, dist in (("left", 0, -10, 1.4), ("front3q", 220, -20, 1.5)):
    cam.azimuth, cam.elevation, cam.distance = az, el, dist
    r.update_scene(d, cam)
    imageio.imwrite(f"snaps/arms_{name}.png", r.render())
print("renders written")
