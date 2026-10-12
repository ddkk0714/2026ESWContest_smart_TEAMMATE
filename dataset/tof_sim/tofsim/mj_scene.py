"""MuJoCo backend: articulated seated person + desk + room, ray-cast with mj_multiRay.

World frame (MuJoCo, z up, right-handed):
    x = right as seen from the sensor, y = from the monitor toward the user, z = up.
The numpy backend (body.py) uses (X right, Y up, Z toward the user); positions
convert as (X, Y, Z) -> (X, Z, Y).

The person is a kinematic tree with real joints:
    pelvis (slide along y: scooting the chair) -> trunk (pitch, roll) -> chest (breathing)
    -> head (neck pitch, yaw, roll), and per arm shoulder (ball) -> elbow (hinge) -> hand.
Poses are applied by writing qpos (no dynamics), so a session is reproducible and
cheap. Arms reach their targets (keyboard, chin, mouth, desk) with two-link IK.

Person geoms are in group 1, everything else in group 0, so "away" frames just
exclude group 1 from the rays and the empty chair stays where it was.
"""

from __future__ import annotations

import math

import numpy as np
import mujoco

from .body import Person, Pose, Room

PERSON_GROUP = 1


def _v(*a) -> str:
    return " ".join(f"{x:.4f}" for x in a)


def build_mjcf(room: Room, person: Person) -> tuple[str, dict]:
    """Returns (xml, reflectivity by geom name)."""
    k = person.scale
    refl = {}
    geoms = []

    def geom(name, refl_value, **attrs):
        refl[name] = refl_value
        a = " ".join(f'{key}="{val}"' for key, val in attrs.items())
        return f'<geom name="{name}" {a}/>'

    desk_h, desk_d = room.desk_height, room.desk_depth
    chair_y = room.chair_z if room.chair_z is not None else desk_d + person.hip_z
    seat_h = person.seat_height
    spine = 0.52 * k
    sh = person.shoulder_half * k
    ua, fa = 0.30 * k, 0.27 * k

    # --- static scene (group 0)
    geoms.append(geom("wall", room.wall_refl, type="box", size=_v(4.0, 0.05, 2.0), pos=_v(0, room.wall_z + 0.05, 1.5),
                      rgba="0.8 0.8 0.75 1", group="0"))
    geoms.append(geom("floor", 0.3, type="plane", size="5 5 0.1", rgba="0.5 0.5 0.5 1", group="0"))
    geoms.append(geom("desk", room.desk_refl, type="box", size=_v(room.desk_width / 2, desk_d / 2, 0.02),
                      pos=_v(0, desk_d / 2, desk_h - 0.02), rgba="0.6 0.45 0.3 1", group="0"))
    geoms.append(geom("keyboard", 0.15, type="box", size=_v(0.22, 0.07, 0.012),
                      pos=_v(person.x_offset, desk_d - 0.22, desk_h + 0.012), rgba="0.15 0.15 0.15 1", group="0"))
    for i, c in enumerate(room.clutter):
        cx, cy, cz = c["center"]          # body.py coordinates (X, Y, Z)
        hx, hy, hz = c["half"]
        geoms.append(geom(f"clutter{i}", c["refl"], type="box", size=_v(hx, hz, hy), pos=_v(cx, cz, cy),
                          rgba="0.5 0.6 0.7 1", group="0"))
    chair = ""
    if room.chair_back:
        refl["chair_seat"] = room.chair_refl
        refl["chair_back"] = room.chair_refl
        chair = f"""
    <body name="chair" pos="{_v(person.x_offset, chair_y, seat_h)}">
      <geom name="chair_seat" type="box" size="0.23 0.23 0.03" pos="0 0.02 -0.03" rgba="0.1 0.1 0.1 1" group="0"/>
      <body name="chair_backrest" pos="0 0.16 0.05">
        <joint name="chair_tilt" type="hinge" axis="1 0 0" range="-0.8 0.1"/>
        <geom name="chair_back" type="box" size="0.22 0.03 0.28" pos="0 0 0.33" rgba="0.1 0.1 0.1 1" group="0"/>
      </body>
    </body>"""

    # --- person (group 1). Person faces -y (toward the monitor).
    def pg(name, refl_value, **attrs):
        attrs.setdefault("group", str(PERSON_GROUP))
        return geom(name, refl_value, **attrs)

    hip_z = seat_h + 0.10 * k
    head_pos = (0, -0.01 * k, 0.09 * k + 0.10 * k)
    head_size = (0.075 * k, 0.095 * k, 0.11 * k)
    hair_size = (head_size[0] * 1.06, head_size[1] * 1.04, head_size[2] * (0.98 * person.hair_cover + 0.2))
    skin, cloth, hair = person.skin_refl, person.torso_refl, person.hair_refl

    def arm(side, sx):
        return f"""
        <body name="uarm_{side}" pos="{_v(sx, 0, spine - 0.03 * k)}">
          <joint name="shoulder_{side}" type="ball"/>
          {pg(f"uarm_{side}", cloth, type="capsule", fromto=_v(0, 0, 0, 0, 0, -ua), size=_v(0.045 * k), rgba="0.3 0.4 0.7 1")}
          <body name="farm_{side}" pos="{_v(0, 0, -ua)}">
            <joint name="elbow_{side}" type="hinge" axis="1 0 0"/>
            {pg(f"farm_{side}", skin, type="capsule", fromto=_v(0, 0, 0, 0, 0, -fa), size=_v(0.038 * k), rgba="0.9 0.75 0.65 1")}
            <body name="hand_{side}" pos="{_v(0, 0, -fa)}">
              {pg(f"hand_{side}", skin, type="ellipsoid", size=_v(0.045 * k, 0.02 * k, 0.05 * k), pos=_v(0, 0, -0.05 * k),
                  rgba="0.9 0.75 0.65 1")}
              <site name="wrist_{side}" pos="0 0 0"/>
            </body>
          </body>
        </body>"""

    legs = "".join(
        f"""
        {pg(f"thigh_{s}", cloth, type="capsule", fromto=_v(sx, 0, 0, sx, -0.42 * k, -0.02), size=_v(0.07 * k), rgba="0.2 0.2 0.3 1")}
        {pg(f"shin_{s}", cloth, type="capsule", fromto=_v(sx, -0.42 * k, -0.02, sx, -0.37 * k, -0.47 * k), size=_v(0.055 * k),
            rgba="0.2 0.2 0.3 1")}"""
        for s, sx in (("l", 0.10), ("r", -0.10)))

    person_xml = f"""
    <body name="pelvis" pos="{_v(person.x_offset, chair_y, hip_z)}">
      <joint name="hip_shift" type="slide" axis="0 1 0" range="-0.3 0.8"/>
      {legs}
      <body name="trunk">
        <joint name="trunk_pitch" type="hinge" axis="1 0 0"/>
        <joint name="trunk_roll" type="hinge" axis="0 1 0"/>
        <body name="chest">
          <joint name="breath" type="slide" axis="0 -1 0"/>
          {pg("torso", cloth, type="ellipsoid", size=_v(sh * 0.95, 0.11 * k, spine * 0.55), pos=_v(0, 0, spine * 0.55),
              rgba="0.3 0.4 0.7 1")}
        </body>
        <body name="head" pos="{_v(0, 0, spine)}">
          <joint name="neck_pitch" type="hinge" axis="1 0 0"/>
          <joint name="head_yaw" type="hinge" axis="0 0 1"/>
          <joint name="head_roll" type="hinge" axis="0 1 0"/>
          {pg("neck", skin, type="capsule", fromto=_v(0, 0, 0, 0, 0, 0.09 * k), size=_v(0.05 * k), rgba="0.9 0.75 0.65 1")}
          {pg("head", skin, type="ellipsoid", size=_v(*head_size), pos=_v(*head_pos), rgba="0.9 0.75 0.65 1")}
          {pg("hair", hair, type="ellipsoid", size=_v(*hair_size),
              pos=_v(head_pos[0], head_pos[1] + 0.012 * k, head_pos[2] + 0.02 * k), rgba="0.1 0.08 0.05 1")}
          <site name="chin" pos="{_v(0, -0.06 * k, head_pos[2] - 0.10 * k)}"/>
          <site name="mouth" pos="{_v(0, -0.09 * k, head_pos[2] - 0.05 * k)}"/>
        </body>
        {arm("l", sh)}
        {arm("r", -sh)}
      </body>
    </body>"""

    xml = f"""
<mujoco model="deskmate_tof">
  <compiler angle="radian" autolimits="true" boundmass="0.01" boundinertia="0.0001"/>
  <option gravity="0 0 0"/>
  <visual>
    <global offwidth="960" offheight="720"/>
    <headlight ambient="0.35 0.35 0.35" diffuse="0.55 0.55 0.55"/>
  </visual>
  <worldbody>
    <light directional="true" pos="0 -1 3" dir="0.3 0.4 -1" diffuse="0.6 0.6 0.6"/>
    {"".join(geoms)}
    <body name="monitor" pos="0 0.05 {desk_h + 0.25:.4f}">
      <geom name="monitor" type="box" size="0.30 0.02 0.20" rgba="0.05 0.05 0.05 1" group="2"/>
    </body>
    {chair}
    {person_xml}
  </worldbody>
</mujoco>"""
    return xml, refl


def _quat_from_mat(R: np.ndarray) -> np.ndarray:
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, R.reshape(-1))
    return q


class MjDesk:
    """One compiled session scene. set_pose() then cast()."""

    def __init__(self, room: Room, person: Person):
        self.room, self.person = room, person
        xml, refl = build_mjcf(room, person)
        self.xml = xml
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.geom_refl = np.zeros(m.ngeom)
        for g in range(m.ngeom):
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
            self.geom_refl[g] = refl.get(name, 0.0)
        self.jnt = {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j): m.jnt_qposadr[j] for j in range(m.njnt)}
        self.body = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, n)
        self.site = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, n)
        self.monitor = self.body("monitor")
        self.present = True
        k = person.scale
        self.ua, self.fa = 0.30 * k, 0.27 * k
        self._geomgroup_all = np.array([1, 1, 0, 0, 0, 0], dtype=np.uint8)     # group 2 = monitor, never hit
        self._geomgroup_empty = np.array([1, 0, 0, 0, 0, 0], dtype=np.uint8)

    # ------------------------------------------------------------------ pose
    def set_pose(self, pose: Pose | None):
        m, d = self.model, self.data
        d.qpos[:] = 0.0
        for side in ("l", "r"):
            d.qpos[self.jnt[f"shoulder_{side}"]] = 1.0        # identity quaternion
        if pose is None:
            self.present = False
            mujoco.mj_kinematics(m, d)
            return
        self.present = True
        q, j = d.qpos, self.jnt
        q[j["hip_shift"]] = pose.hip_shift
        q[j["trunk_pitch"]] = pose.trunk_pitch      # + = toward the desk (top moves to -y)
        q[j["trunk_roll"]] = -pose.trunk_roll       # + = person's left = sensor's right (+x)
        q[j["breath"]] = pose.breath
        q[j["neck_pitch"]] = pose.neck_pitch
        q[j["head_yaw"]] = pose.head_yaw
        q[j["head_roll"]] = -pose.head_roll
        if "chair_tilt" in j:
            q[j["chair_tilt"]] = min(0.0, pose.trunk_pitch)
        mujoco.mj_kinematics(m, d)
        self._place_arms(pose)
        mujoco.mj_kinematics(m, d)

    def _place_arms(self, pose: Pose):
        d, room, p = self.data, self.room, self.person
        k = p.scale
        desk_top = room.desk_height + 0.03
        chin = d.site_xpos[self.site("chin")].copy()
        mouth = d.site_xpos[self.site("mouth")].copy()
        trunk_R = d.xmat[self.body("trunk")].reshape(3, 3)
        kb_y = room.desk_depth - 0.20 + pose.arm_jitter
        for side, sign in (("l", 1.0), ("r", -1.0)):     # "l" = person's left = sensor's right (+x)
            S = d.xpos[self.body(f"uarm_{side}")].copy()
            mode = pose.arms
            active_chin = (mode == "chin_right" and side == "l") or (mode == "chin_left" and side == "r")
            active_cup = mode == "cup_right" and side == "l"
            if active_chin:
                W = chin + np.array([0.0, 0.02, -0.03])
                pole = np.array([S[0] * 0.6 + chin[0] * 0.4, room.desk_depth - 0.12, desk_top])
            elif active_cup:
                W = mouth + np.array([0.0, -0.05, -0.02])
                pole = S + np.array([0.08 * sign, 0.05, -0.4])
            else:
                if mode in ("chin_right", "chin_left", "cup_right"):
                    mode = pose.other_arms     # the arm that is not propping the chin / holding the cup
                W, pole = self._arm_target(mode, side, sign, S, trunk_R, kb_y, desk_top)
            self._ik_arm(side, S, W, pole, trunk_R)

    def _arm_target(self, mode, side, sign, S, trunk_R, kb_y, desk_top):
        """Wrist target and elbow pole for a free arm. sign = +1 for the arm at +x."""
        d, room, p = self.data, self.room, self.person
        k = p.scale
        spine = 0.52 * k
        trunk_o = d.xpos[self.body("trunk")]
        head_b = self.body("head")
        head_c = d.xpos[head_b] + d.xmat[head_b].reshape(3, 3) @ np.array([0.0, -0.01 * k, 0.19 * k])
        # trunk frame: +y is the person's back, -y their front, +z up the spine
        chest_front = trunk_o + trunk_R @ np.array([0.0, -0.13 * k, spine * 0.45])
        if mode == "pillow":
            W = np.array([-0.05 * sign + p.x_offset, room.desk_depth - 0.30, desk_top + 0.05])
            pole = S + np.array([0.25 * sign, -0.1, -0.2])
        elif mode == "lap":
            W = np.array([S[0] * 0.7, d.xpos[self.body("pelvis")][1] - 0.25, p.seat_height + 0.12 * k])
            pole = S + np.array([0.05 * sign, 0.15, -0.3])
        elif mode == "behind_head":
            # fingers laced behind the head, elbows out to the sides
            W = head_c + np.array([0.04 * sign, 0.10 * k, 0.03 * k])
            pole = S + np.array([0.40 * sign, 0.05, 0.15])
        elif mode == "crossed":
            # forearms folded across the chest/belly, each hand toward the other side
            W = chest_front + trunk_R @ np.array([-0.09 * sign, -0.02, -0.08 * k])
            pole = S + trunk_R @ np.array([0.12 * sign, -0.05, -0.30])
        elif mode == "phone":
            # both hands in front of the chest holding a phone
            W = chest_front + trunk_R @ np.array([0.035 * sign, -0.22 * k, 0.04 * k])
            pole = S + np.array([0.10 * sign, 0.05, -0.35])
        elif mode == "hanging":
            # arms hanging down at the sides of the chair
            W = S + np.array([0.06 * sign, 0.04, -(self.ua + self.fa) * 0.97])
            pole = S + np.array([0.05 * sign, 0.15, -0.30])
        else:  # typing
            W = np.array([S[0] * 0.55, kb_y, desk_top + 0.03])
            pole = S + np.array([0.06 * sign, 0.10, -0.35])
        return W, pole

    def _ik_arm(self, side, S, W, pole, parent_R):
        """Two-link IK (shoulder ball + elbow hinge). Plain float math: numpy
        overhead on 3-vectors was a fifth of the whole frame time."""
        L1, L2 = self.ua, self.fa
        sx, sy, sz = float(S[0]), float(S[1]), float(S[2])
        dx, dy, dz = float(W[0]) - sx, float(W[1]) - sy, float(W[2]) - sz
        D = math.sqrt(dx * dx + dy * dy + dz * dz)
        inv = 1.0 / max(D, 1e-9)
        hx, hy, hz = dx * inv, dy * inv, dz * inv                     # unit shoulder -> wrist
        D = min(max(D, abs(L1 - L2) + 1e-3), L1 + L2 - 1e-4)
        a = (L1 * L1 - L2 * L2 + D * D) / (2 * D)
        h = math.sqrt(max(L1 * L1 - a * a, 0.0))
        px, py, pz = float(pole[0]) - sx, float(pole[1]) - sy, float(pole[2]) - sz
        dot = px * hx + py * hy + pz * hz
        qx, qy, qz = px - dot * hx, py - dot * hy, pz - dot * hz      # pole, perpendicular part
        qn = math.sqrt(qx * qx + qy * qy + qz * qz)
        if qn < 1e-6:
            qx, qy, qz = hz * hx, hz * hy, hz * hz - 1.0             # (0,0,-1) minus its projection
            qn = math.sqrt(qx * qx + qy * qy + qz * qz) or 1.0
        qx, qy, qz = qx / qn, qy / qn, qz / qn
        ex, ey, ez = a * hx + h * qx, a * hy + h * qy, a * hz + h * qz   # elbow, relative to S
        ux, uy, uz = ex / L1, ey / L1, ez / L1                           # upper arm direction
        fx, fy, fz = (D * hx - ex) / L2, (D * hy - ey) / L2, (D * hz - ez) / L2   # forearm direction
        nx, ny, nz = uy * fz - uz * fy, uz * fx - ux * fz, ux * fy - uy * fx      # hinge axis = u × f
        nn = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nn < 1e-6:
            nx, ny, nz = -uz, 0.0, ux                                    # u × (0,1,0)
            nn = math.sqrt(nx * nx + nz * nz) or 1.0
        nx, ny, nz = nx / nn, ny / nn, nz / nn
        zx, zy, zz = -ux, -uy, -uz                                       # arm points along local -z
        yx, yy, yz = zy * nz - zz * ny, zz * nx - zx * nz, zx * ny - zy * nx   # y = z × x
        R_ua = np.array([[nx, yx, zx], [ny, yy, zy], [nz, yz, zz]])
        q_rel = _quat_from_mat(parent_R.T @ R_ua)
        adr = self.jnt[f"shoulder_{side}"]
        self.data.qpos[adr:adr + 4] = q_rel
        # forearm in the upper-arm frame: components along local y and z
        vy = yx * fx + yy * fy + yz * fz
        vz = zx * fx + zy * fy + zz * fz
        self.data.qpos[self.jnt[f"elbow_{side}"]] = math.atan2(vy, -vz)

    # ------------------------------------------------------------------ rays
    def cast(self, origin: np.ndarray, dirs: np.ndarray):
        n = dirs.shape[0]
        geomid = np.full(n, -1, dtype=np.int32)
        dist = np.full(n, -1.0)
        normal = np.zeros(3 * n)
        group = self._geomgroup_all if self.present else self._geomgroup_empty
        mujoco.mj_multiRay(self.model, self.data, origin.astype(np.float64), np.ascontiguousarray(dirs, dtype=np.float64).ravel(),
                           group, 1, self.monitor, geomid, dist, normal, n, 10.0)
        hit = (geomid >= 0) & (dist > 0)
        t = np.where(hit, dist, np.inf)
        refl = np.where(hit, self.geom_refl[np.maximum(geomid, 0)], 0.0)
        cosang = np.abs(np.einsum("ij,ij->i", normal.reshape(n, 3), dirs))
        return t, refl, np.where(hit, cosang, 0.0)


def sensor_frame(pose) -> tuple[np.ndarray, np.ndarray]:
    """SensorPose (body.py coordinates) -> (origin, R local(right, up, forward) -> MuJoCo world)."""
    X, Y, Z = pose.position
    origin = np.array([X, Z, Y], dtype=np.float64)
    p, yaw, r = np.radians([pose.pitch_down_deg, pose.yaw_deg, pose.roll_deg + (180.0 if pose.upside_down else 0.0)])
    B = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=float)   # columns: right=x, up=z, forward=y
    cp, sp = np.cos(-p), np.sin(-p)
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])          # about x: -pitch tips forward down
    cy, sy = np.cos(yaw), np.sin(yaw)
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    cr, sr = np.cos(r), np.sin(r)
    Ry = np.array([[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]])          # roll about forward (y)
    return origin, Rz @ Rx @ Ry @ B
