"""SMPL-X body split into rigid parts for the MuJoCo desk scene (tofsim/mj_scene.py).

The articulated skeleton of mj_scene stays as it is (pelvis -> trunk -> head / arms, legs fixed
to the pelvis in a seated pose). This module replaces its capsule geoms with pieces of an SMPL-X
mesh: every vertex goes to the body part whose skinning weight is largest, each piece is
expressed in that part's local frame, and the pieces are written as inline MuJoCo meshes.

Model files (SMPLX_NEUTRAL.npz etc.) are licensed separately and are read from the directory
in $TOFSIM_SMPLX_DIR/smplx (default ~/tof-sim-v5/models). Frames: SMPL-X has x = the person's
left, y up, z forward; MuJoCo here has x = the person's left, y toward the user's back, z up,
so (x, y, z) -> (x, -z, y).
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

import numpy as np

MODEL_DIR = os.environ.get("TOFSIM_SMPLX_DIR", os.path.expanduser("~/tof-sim-v5/models"))

# MuJoCo body <- SMPL-X joints whose skin it carries (joint ids from smplx.joint_names)
PART_JOINTS = {
    "pelvis": [0],
    "thigh_l": [1], "thigh_r": [2],
    "shin_l": [4, 7, 10], "shin_r": [5, 8, 11],
    "trunk": [3, 6, 9, 13, 14],
    "head": [12, 15, 22, 23, 24],
    "uarm_l": [16], "uarm_r": [17],
    "farm_l": [18], "farm_r": [19],
    "hand_l": [20] + list(range(25, 40)), "hand_r": [21] + list(range(40, 55)),
}
# joint that is each part's origin, and the child joint giving its long axis (None = no re-orientation)
PART_FRAME = {
    "pelvis": (0, None), "thigh_l": (1, 4), "thigh_r": (2, 5), "shin_l": (4, 7), "shin_r": (5, 8),
    "trunk": (0, None), "head": (12, None),
    "uarm_l": (16, 18), "uarm_r": (17, 19), "farm_l": (18, 20), "farm_r": (19, 21),
    "hand_l": (20, 28), "hand_r": (21, 43),
}
# where each part's long axis must point in its MuJoCo body frame (seated pose, arms hanging)
PART_AXIS = {
    "thigh_l": (0, -1, -0.05), "thigh_r": (0, -1, -0.05), "shin_l": (0, 0, -1), "shin_r": (0, 0, -1),
    "uarm_l": (0, 0, -1), "uarm_r": (0, 0, -1), "farm_l": (0, 0, -1), "farm_r": (0, 0, -1),
    "hand_l": (0, 0, -1), "hand_r": (0, 0, -1),
}

_models = {}
# fraction of triangles removed per part (ray casting cost grows with the triangle count)
DECIMATE = {}                       # full-resolution SMPL-X: decimation gave no measurable ray-cast speed-up


def available() -> bool:
    return os.path.isfile(os.path.join(MODEL_DIR, "smplx", "SMPLX_NEUTRAL.npz"))


def _model(gender: str):
    if gender not in _models:
        import smplx
        _models[gender] = smplx.create(MODEL_DIR, model_type="smplx", gender=gender, use_pca=False,
                                       num_betas=10, batch_size=1, ext="npz")
    return _models[gender]


def _to_mj(v: np.ndarray) -> np.ndarray:
    return np.stack([v[..., 0], -v[..., 2], v[..., 1]], axis=-1)


def _rot_x(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _axis_angle(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> axis-angle vector (what SMPL-X body_pose expects)."""
    angle = np.arccos(np.clip((np.trace(R) - 1) / 2, -1.0, 1.0))
    if angle < 1e-8:
        return np.zeros(3)
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * np.sin(angle))
    return axis * angle


def _close_holes(verts: np.ndarray, faces: np.ndarray):
    """Cap every open boundary loop of a piece with a fan of triangles, so rays cannot enter the
    piece through its cut ends. Returns (verts, faces) with the cap centres appended."""
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    e, cnt = np.unique(edges, axis=0, return_counts=True)
    bnd = e[cnt == 1]
    if len(bnd) == 0:
        return verts, faces
    # connected components of the boundary edges (union-find); every edge of a component gets a
    # triangle to that component's centre, so no loop ordering is needed and nothing is missed
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in bnd:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    comp = {}
    for a, b in bnd:
        comp.setdefault(find(int(a)), []).append((int(a), int(b)))
    verts = list(verts)
    new = []
    for edges in comp.values():
        ids = sorted({i for e in edges for i in e})
        c = np.mean([verts[i] for i in ids], axis=0)
        ci = len(verts)
        verts.append(c)
        new.extend([a, b, ci] for a, b in edges)
    return np.asarray(verts, dtype=np.float32), np.concatenate([faces, np.asarray(new, dtype=np.int32)])


def _rot_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation matrix taking unit vector a onto unit vector b."""
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v, c = np.cross(a, b), float(a @ b)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


@dataclass
class Part:
    name: str
    verts: np.ndarray      # (N,3) in the part's MuJoCo body frame
    faces: np.ndarray      # (M,3)
    offset: np.ndarray     # part origin, in its parent MuJoCo body's frame
    R: np.ndarray          # part frame -> parent frame (identity except for limbs)


@dataclass
class Body:
    parts: dict            # name -> Part
    joints: np.ndarray     # (55,3) T-pose joints in MuJoCo axes, pelvis at the origin
    height: float          # standing stature (m)
    ua: float              # upper arm length
    fa: float              # forearm length
    spine: float           # pelvis -> neck
    sh: float              # shoulder half width
    knee_l: np.ndarray     # knee position in the pelvis frame (seated)
    knee_r: np.ndarray


def make(gender: str, betas, hair_cover: float = 0.8) -> Body:
    """Build the rigid parts for one body shape. gender: neutral | male | female.
    hair_cover (0..1) sets how far down the forehead and neck the hair reaches."""
    import torch
    m = _model(gender)
    # Pose the body with SMPL-X's own skinning into the scene's rest pose (seated, arms hanging)
    # before cutting it into rigid pieces, so shoulders and hips are skinned for that pose and
    # the pieces meet without holes. body_pose rows follow joints 1..21 (axis-angle, parent frame).
    pose = np.zeros((21, 3), dtype=np.float32)
    pose[0] = pose[1] = (-np.pi / 2, 0.0, 0.0)                  # hips: thighs forward
    pose[3] = pose[4] = (np.pi / 2, 0.0, 0.0)                    # knees: shins down
    pose[12], pose[13] = (0.0, 0.0, -np.radians(8)), (0.0, 0.0, np.radians(8))      # collars
    # shoulders: arms down and 35 deg forward, the middle of the desk-work range, so later IK
    # poses (keyboard, lap, chin) stay close to the pose the skin was cut in
    pose[15] = _axis_angle(_rot_x(-np.radians(35)) @ _rot_z(-np.radians(80)))
    pose[16] = _axis_angle(_rot_x(-np.radians(35)) @ _rot_z(np.radians(80)))
    # elbows: forearms pronated (palms turned to face backwards while hanging), so that when the
    # forearm swings up to the desk the palms face down; the scene has no forearm-twist joint
    pose[17] = pose[18] = (np.radians(80), 0.0, 0.0)
    with torch.no_grad():
        out = m(betas=torch.tensor(np.asarray(betas, dtype=np.float32))[None],
                body_pose=torch.tensor(pose.reshape(1, -1)), return_verts=True)
    V = _to_mj(out.vertices[0].numpy())
    J = _to_mj(out.joints[0].numpy()[:55])
    F = m.faces.astype(np.int64)
    W = m.lbs_weights.numpy()[:, :55]
    V, J = V - J[0], J - J[0]                                 # pelvis joint at the origin
    lut = np.zeros(55, dtype=int)                               # joint -> part index
    for pi, (name, js) in enumerate(PART_JOINTS.items()):
        lut[js] = pi
    best = W.argmax(1)
    owner = lut[best]
    # "Socket" cut: skin that only partly follows a limb (shoulder caps, hip creases) stays with
    # the torso / pelvis, so a swinging limb turns inside a fixed socket instead of tearing open.
    names = list(PART_JOINTS)
    for limb, parent in (("uarm_l", "trunk"), ("uarm_r", "trunk"), ("thigh_l", "pelvis"), ("thigh_r", "pelvis")):
        li, pi_ = names.index(limb), names.index(parent)
        weak = (owner == li) & (W[np.arange(len(V)), best] < 0.6)
        owner[weak] = pi_
    # Smooth the cut lines: a few rounds of "take the majority label of your neighbours" turn the
    # saw-toothed weight boundaries into even rings without moving them much
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    nbr_a = np.concatenate([E[:, 0], E[:, 1]])
    nbr_b = np.concatenate([E[:, 1], E[:, 0]])
    nparts = len(PART_JOINTS)
    for _ in range(4):
        votes_ = np.zeros((len(V), nparts))
        np.add.at(votes_, (nbr_a, owner[nbr_b]), 1.0)
        votes_[np.arange(len(V)), owner] += 1.5                          # own label counts a little more
        owner = votes_.argmax(1)
    fo = owner[F]                                               # (M,3) owners of each face's corners
    parts = {}
    for pi, name in enumerate(names):
        hit = (fo == pi).sum(1) >= 2                            # majority of corners: clean cuts, every piece capped below
        fsel = F[hit]
        if len(fsel) == 0:
            continue
        used, inv = np.unique(fsel, return_inverse=True)
        origin_j, axis_j = PART_FRAME[name]
        R = np.eye(3)
        if axis_j is not None:
            R = _rot_a_to_b(J[axis_j] - J[origin_j], np.array(PART_AXIS[name], dtype=float))
        local = (V[used] - J[origin_j]) @ R.T
        faces = inv.reshape(-1, 3)
        if DECIMATE.get(name, 0.0) > 0:
            try:
                import fast_simplification
                local, faces = fast_simplification.simplify(local.astype(np.float32), faces.astype(np.int64),
                                                            target_reduction=DECIMATE.get(name, 0.0))
            except ImportError:
                pass
        local, faces = _close_holes(np.asarray(local, dtype=np.float32), np.asarray(faces, dtype=np.int32))
        parts[name] = Part(name, local, faces, J[origin_j].copy(), R)
    # hair: the scalp part of the head surface becomes its own (dark) piece
    head = parts["head"]
    hv, hf = head.verts, head.faces
    top, bot = hv[:, 2].max(), hv[:, 2].min()
    H = top - bot
    yc = 0.5 * (hv[:, 1].min() + hv[:, 1].max())
    front_line = top - H * 0.25 * hair_cover                          # hairline on the forehead (above the brows)
    back_line = top - H * min(0.9, 0.25 * hair_cover + 0.45)          # hair reaches further down behind
    is_hair = hv[:, 2] > np.where(hv[:, 1] > yc + 0.01, back_line, front_line)   # the person faces -y
    votes = is_hair[hf].sum(1)

    def piece(faces):
        used, inv = np.unique(faces, return_inverse=True)
        return hv[used], inv.reshape(-1, 3).astype(np.int32)

    v1, f1 = piece(hf[votes < 2])
    v2, f2 = piece(hf[votes >= 2])
    parts["head"] = Part("head", *_close_holes(v1, f1), head.offset, head.R)
    parts["hair"] = Part("hair", *_close_holes(v2, f2), head.offset, head.R)
    # parent offsets: limbs hang from the seated skeleton
    ua = float(np.linalg.norm(J[18] - J[16]))
    fa = float(np.linalg.norm(J[20] - J[18]))
    spine = float(J[12][2])
    sh = float(abs(J[16][0]))
    for side, hip, knee in (("l", 1, 4), ("r", 2, 5)):
        Rt = parts[f"thigh_{side}"].R
        parts[f"thigh_{side}"].offset = J[hip].copy()
        parts[f"shin_{side}"].offset = J[hip] + Rt @ (J[knee] - J[hip])          # knee after the thigh swings forward
    parts["head"].offset = J[12].copy()
    for side, s_j in (("l", 16), ("r", 17)):
        parts[f"uarm_{side}"].offset = J[s_j].copy()                                # shoulder in the trunk frame
        parts[f"farm_{side}"].offset = np.array([0.0, 0.0, -ua])
        parts[f"hand_{side}"].offset = np.array([0.0, 0.0, -fa])
    # standing stature from the posed skeleton: crown above the pelvis + thigh + shin + foot
    height = float(V[:, 2].max() + np.linalg.norm(J[4] - J[1]) + np.linalg.norm(J[7] - J[4]) + 0.08)
    return Body(parts, J, height, ua, fa, spine, sh, parts["shin_l"].offset, parts["shin_r"].offset)


@functools.lru_cache(maxsize=8)
def make_cached(gender: str, betas: tuple, hair_cover: float = 0.8) -> Body:
    """make() memoised on the shape, so the sampler and the scene builder share one build."""
    return make(gender, list(betas), hair_cover)


def mesh_assets(body: Body, prefix: str = "sx_") -> str:
    """<mesh> asset entries with inline vertex / face data."""
    out = []
    for name, p in body.parts.items():
        out.append(f'<mesh name="{prefix}{name}" vertex="{" ".join(f"{x:.4f}" for x in p.verts.ravel())}" '
                   f'face="{" ".join(str(i) for i in p.faces.ravel())}"/>')
    return "\n".join(out)


def sample_shape(rng) -> dict:
    """Random body shape parameters for one session."""
    gender = str(rng.choice(["neutral", "male", "female"], p=[0.2, 0.4, 0.4]))
    betas = np.clip(rng.normal(0.0, 1.0, 10), -2.5, 2.5)
    betas[0] = np.clip(rng.normal(0.0, 1.3), -3.0, 3.0)        # first component is mostly stature
    return {"gender": gender, "betas": [float(b) for b in betas]}
