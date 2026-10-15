"""지시 자세 → 관절 각도(qpos).

몸통·목·머리는 각도 분포에서 뽑는다(사람·시도마다 다르게). 손·팔꿈치·엎드린 머리처럼
'어디에 닿아 있다'로 정의되는 것은 MuJoCo 자코비안으로 IK 를 풀어 맞춘다.

각도는 '앞으로 숙임 = 양수(도)' 로 적고, MuJoCo hinge 부호(+y 축, 양수 = 뒤로)로 바꿔 넣는다.
분포 숫자는 depth_teacher 라벨 기준(상체 앞으로 8 cm·고개 숙임 9°·거북목 12° 등)을 넘도록
잡은 그럴듯한 값이고, 사람 측정치는 아니다.
"""
from __future__ import annotations

import mujoco
import numpy as np

from .scene import Scene

# label → {관절: (평균°, 표준편차°)}, 앞으로 = 양수. 롤은 사람 왼쪽으로 기울면 양수.
_TORSO = {
    "upright":      {"lumbar": (2, 4), "thoracic": (0, 3), "neck": (8, 4), "head": (-5, 4)},
    "lean_forward": {"lumbar": (26, 6), "thoracic": (8, 4), "neck": (0, 5), "head": (-16, 5)},
    "forward_head": {"lumbar": (3, 3), "thoracic": (6, 3), "neck": (32, 6), "head": (-24, 5)},
    "head_down":    {"lumbar": (3, 3), "thoracic": (4, 3), "neck": (24, 6), "head": (26, 8)},
    "recline":      {"lumbar": (-22, 6), "thoracic": (-6, 3), "neck": (14, 5), "head": (6, 5)},
    "chin_rest":    {"lumbar": (12, 5), "thoracic": (6, 3), "neck": (8, 4), "head": (6, 5)},
    "lean_left":    {"lumbar": (3, 3), "thoracic": (0, 3), "neck": (8, 4), "head": (-5, 4)},
    "lean_right":   {"lumbar": (3, 3), "thoracic": (0, 3), "neck": (8, 4), "head": (-5, 4)},
    "drowsy":       {"lumbar": (6, 4), "thoracic": (8, 4), "neck": (12, 4), "head": (4, 4)},
    "face_down":    {"lumbar": (30, 0), "thoracic": (15, 0), "neck": (25, 0), "head": (20, 0)},
}
SIDE = {"left": -1.0, "right": 1.0}  # 사람 기준 왼쪽 = -y


def _rad_fwd(deg: float) -> float:
    return -np.radians(deg)


def _site(sc: Scene, name: str) -> np.ndarray:
    return sc.data.site_xpos[mujoco.mj_name2id(sc.model, mujoco.mjtObj.mjOBJ_SITE, name)].copy()


def solve_ik(sc: Scene, targets: list[tuple[str, np.ndarray, float]], joints: list[str],
             iters: int = 80, damping: float = 0.02, tol: float = 0.004) -> float:
    """사이트들을 목표 위치로. 고르지 않은 관절은 그대로 둔다. 남은 최대 오차(m)를 돌려준다."""
    m, d = sc.model, sc.data
    jids = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, j) for j in joints]
    dofs = [int(m.jnt_dofadr[j]) for j in jids]
    qs = [int(m.jnt_qposadr[j]) for j in jids]
    lo = np.array([m.jnt_range[j][0] for j in jids])
    hi = np.array([m.jnt_range[j][1] for j in jids])
    sids = [(mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, s), np.asarray(t, float), w)
            for s, t, w in targets]
    jacp = np.zeros((3, m.nv))
    err_max = np.inf
    for _ in range(iters):
        mujoco.mj_kinematics(m, d)
        mujoco.mj_comPos(m, d)
        rows, errs = [], []
        for sid, tgt, w in sids:
            mujoco.mj_jacSite(m, d, jacp, None, sid)
            rows.append(w * jacp[:, dofs])
            errs.append(w * (tgt - d.site_xpos[sid]))
        J, e = np.vstack(rows), np.concatenate(errs)
        err_max = max(np.linalg.norm(errs[i] / sids[i][2]) for i in range(len(sids)))
        if err_max < tol:
            break
        dq = J.T @ np.linalg.solve(J @ J.T + damping ** 2 * np.eye(len(e)), e)
        q = d.qpos[qs] + np.clip(dq, -0.2, 0.2)
        d.qpos[qs] = np.clip(q, lo, hi)
    mujoco.mj_kinematics(m, d)
    return float(err_max)


def _arm_joints(side: str) -> list[str]:
    s = "L" if side == "left" else "R"
    return [f"sh_{s}_x", f"sh_{s}_y", f"sh_{s}_z", f"elbow_{s}"]


def _arm_to(sc: Scene, side: str, hand: np.ndarray, elbow: np.ndarray | None = None) -> float:
    s = "L" if side == "left" else "R"
    targets = [(f"hand_{s}", hand, 1.0)]
    if elbow is not None:
        targets.append((f"elbow_{s}", elbow, 0.7))
    return solve_ik(sc, targets, _arm_joints(side))


def sample_pose(sc: Scene, label: str, rng: np.random.Generator, chin_side: str = "left",
                arms: str | None = None) -> dict[str, float]:
    """자세 하나의 qpos(dict). 자리비움도 마지막 바른 자세 qpos 를 돌려준다(사람만 숨긴다)."""
    r, s = sc.room, sc.person.scale
    base = _TORSO.get(label, _TORSO["upright"])
    g = {k: rng.normal(mu, sd) for k, (mu, sd) in base.items()}
    q = {"lumbar_pitch": _rad_fwd(g["lumbar"]), "thoracic_pitch": _rad_fwd(g["thoracic"]),
         "neck_pitch": _rad_fwd(g["neck"]), "head_pitch": _rad_fwd(g["head"]),
         "head_yaw": np.radians(rng.normal(0, 4)), "head_roll": np.radians(rng.normal(0, 3))}
    if label in ("lean_left", "lean_right"):
        sign = 1.0 if label == "lean_left" else -1.0
        q["lumbar_roll"] = sign * np.radians(rng.uniform(10, 20))
        q["head_roll"] = -0.4 * q["lumbar_roll"]
    sc.set_q(q)

    py = r.person_y
    kb_x = r.desk_edge - rng.uniform(0.10, 0.20)   # 키보드 위 손 위치(센서 쪽으로 들어감)
    desk_z = r.desk_h + 0.03

    if label == "face_down":
        # 머리를 책상 위 팔뚝 위로: 척추·목·머리를 같이 풀어 머리 중심을 내린다
        head_t = np.array([r.desk_edge - rng.uniform(0.10, 0.20), py + rng.normal(0, 0.03),
                           r.desk_h + 0.06 * s + 0.09 * s])
        solve_ik(sc, [("head_c", head_t, 1.0)],
                 ["lumbar_pitch", "thoracic_pitch", "neck_pitch", "head_pitch"])
        for side in ("left", "right"):  # 팔짱 끼듯 책상 위
            k = SIDE[side]
            _arm_to(sc, side, np.array([head_t[0] - 0.05, py - k * 0.10, desk_z + 0.02]),
                    np.array([head_t[0] + 0.02, py + k * 0.24 * s, desk_z]))
        return sc.get_q()

    if label == "chin_rest":
        # 실제 턱괴기처럼 몸을 숙여 턱을 팔꿈치 위로 가져온 뒤(1단계), 팔꿈치는 책상·손은 턱(2단계)
        k = SIDE[chin_side]
        elbow = np.array([r.desk_edge - rng.uniform(0.03, 0.10), py + k * rng.uniform(0.08, 0.15), desk_z])
        forearm = 0.33 * s
        chin_t = elbow + np.array([rng.uniform(0.03, 0.08), -k * 0.06, forearm * rng.uniform(0.88, 0.97)])
        solve_ik(sc, [("chin", chin_t, 1.0)],
                 ["lumbar_pitch", "lumbar_roll", "thoracic_pitch", "neck_pitch", "head_pitch"])
        q2 = sc.get_q()
        q2["head_roll"] = -k * np.radians(rng.uniform(4, 12))  # 손 쪽으로 기울임
        sc.set_q(q2)
        # 팔이 늘어진 자세에서 풀면 손이 책상 앞으로 가는 해에 빠진다 → 괸 팔 모양에서 시작
        a = "L" if chin_side == "left" else "R"
        q3 = sc.get_q()
        q3.update({f"sh_{a}_y": 0.6, f"elbow_{a}": 2.3})
        sc.set_q(q3)
        _arm_to(sc, chin_side, _site(sc, "chin") + np.array([0.0, 0.0, -0.02]), elbow)
        other = "right" if chin_side == "left" else "left"
        _arm_to(sc, other, np.array([kb_x, py + SIDE[other] * 0.16, desk_z]))
        return sc.get_q()

    arms = arms or ("lap" if label == "recline" else
                    rng.choice(["type", "type", "type", "desk"]))
    for side in ("left", "right"):
        k = SIDE[side]
        if arms == "type":
            hand = np.array([kb_x, py + k * rng.uniform(0.12, 0.20), desk_z])
        elif arms == "desk":
            hand = np.array([r.desk_edge - rng.uniform(0.02, 0.08), py + k * rng.uniform(0.15, 0.25), desk_z])
        else:  # 무릎 위
            hip = sc.data.xpos[mujoco.mj_name2id(sc.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")]
            hand = np.array([hip[0] - 0.25 * s, py + k * 0.12, hip[2] + 0.09])
        _arm_to(sc, side, hand)
    return sc.get_q()


def chair_tilt_for(q: dict[str, float], base_deg: float) -> float:
    """등받이가 뒤로 젖힌 몸을 따라가게. pitch 양수 = 뒤로."""
    recline = np.degrees(q.get("lumbar_pitch", 0.0) + 0.5 * q.get("thoracic_pitch", 0.0))
    return max(base_deg, recline + 2.0)
