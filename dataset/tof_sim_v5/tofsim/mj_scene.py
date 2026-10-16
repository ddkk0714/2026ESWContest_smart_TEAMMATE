"""MuJoCo backend: articulated seated person + desk + room, ray-cast with mj_multiRay.

World frame (MuJoCo, z up, right-handed):
    x = right as seen from the sensor, y = from the monitor toward the user, z = up.
The numpy backend (body.py) uses (X right, Y up, Z toward the user); positions
convert as (X, Y, Z) -> (X, Z, Y).

The person is a kinematic tree with real joints:
    pelvis (slide along y: scooting the chair) -> trunk (pitch, roll) -> chest (breathing)
    -> neck (neck pitch, yaw, roll, lower-neck flexion) -> head (upper-neck extension), and per arm
    shoulder (ball) -> elbow (hinge) -> hand. Pose.head_forward bends the neck forward and the head back
    by the same angle, so the head translates toward the desk with the face direction unchanged.
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


def build_mjcf(room: Room, person: Person, body=None) -> tuple[str, dict]:
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
    if body is not None:                                       # SMPL-X skeleton lengths
        spine, sh, ua, fa = body.spine, body.sh, body.ua, body.fa

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
      <joint name="seat_tilt" type="hinge" axis="1 0 0" range="-0.5 0.1"/>
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
    g = person.girth                     # thickness: torso depth, neck/arm/leg radii
    hk = k * person.head_scale           # head and hair size (the neck stays as it is)
    head_pos = (0, -0.01 * hk, 0.09 * k + 0.10 * hk)
    head_size = (0.075 * hk * person.head_width, 0.095 * hk, 0.11 * hk)
    hair_size = (head_size[0] * 1.06, head_size[1] * 1.04, head_size[2] * (0.98 * person.hair_cover + 0.2))
    skin, cloth, hair = person.skin_refl, person.torso_refl, person.hair_refl

    def arm(side, sx):
        return f"""
        <body name="uarm_{side}" pos="{_v(sx, 0, spine - 0.03 * k)}">
          <joint name="shoulder_{side}" type="ball"/>
          {pg(f"uarm_{side}", cloth, type="capsule", fromto=_v(0, 0, 0, 0, 0, -ua), size=_v(0.045 * k * g), rgba="0.3 0.4 0.7 1")}
          <body name="farm_{side}" pos="{_v(0, 0, -ua)}">
            <joint name="elbow_{side}" type="hinge" axis="1 0 0"/>
            {pg(f"farm_{side}", skin, type="capsule", fromto=_v(0, 0, 0, 0, 0, -fa), size=_v(0.038 * k * g), rgba="0.9 0.75 0.65 1")}
            <body name="hand_{side}" pos="{_v(0, 0, -fa)}">
              {pg(f"hand_{side}", skin, type="ellipsoid", size=_v(0.045 * k, 0.02 * k, 0.05 * k), pos=_v(0, 0, -0.05 * k),
                  rgba="0.9 0.75 0.65 1")}
              <site name="wrist_{side}" pos="0 0 0"/>
            </body>
          </body>
        </body>"""

    legs = "".join(
        f"""
        {pg(f"thigh_{s}", cloth, type="capsule", fromto=_v(sx, 0, 0, sx, -0.42 * k, -0.02), size=_v(0.07 * k * g), rgba="0.2 0.2 0.3 1")}
        {pg(f"shin_{s}", cloth, type="capsule", fromto=_v(sx, -0.42 * k, -0.02, sx, -0.37 * k, -0.47 * k), size=_v(0.055 * k * g),
            rgba="0.2 0.2 0.3 1")}"""
        for s, sx in (("l", 0.10), ("r", -0.10)))

    ws, wr = room.walker_scale, room.walker_refl
    walker = f"""
    <body name="walker" mocap="true" pos="0 0 -10">
      {geom("walker_leg_l", wr, type="capsule", fromto=_v(0.10, 0, 0.05, 0.10, 0, 0.78 * ws), size=_v(0.075), rgba="0.25 0.25 0.3 1", group="3")}
      {geom("walker_leg_r", wr, type="capsule", fromto=_v(-0.10, 0, 0.05, -0.10, 0, 0.78 * ws), size=_v(0.075), rgba="0.25 0.25 0.3 1", group="3")}
      {geom("walker_torso", wr, type="capsule", fromto=_v(0, 0, 0.80 * ws, 0, 0, 1.40 * ws), size=_v(0.17), rgba="0.45 0.35 0.3 1", group="3")}
      {geom("walker_head", 0.35, type="sphere", pos=_v(0, 0, 1.58 * ws), size=_v(0.11), rgba="0.9 0.75 0.65 1", group="3")}
    </body>"""

    prop = f"""
    <body name="prop" mocap="true" pos="0 0 -10">
      {geom("prop_book", 0.6, type="box", size=_v(0.10, 0.012, 0.125), rgba="0.9 0.88 0.8 1", group="3")}
    </body>"""

    person_xml = f"""
    <body name="pelvis" pos="{_v(person.x_offset, chair_y, hip_z)}">
      <joint name="hip_shift" type="slide" axis="0 1 0" range="-0.3 0.8"/>
      <joint name="pelvis_tilt" type="hinge" axis="1 0 0" range="-0.5 0.1"/>
      {legs}
      <body name="trunk">
        <joint name="trunk_pitch" type="hinge" axis="1 0 0"/>
        <joint name="trunk_roll" type="hinge" axis="0 1 0"/>
        <body name="chest">
          <joint name="breath" type="slide" axis="0 -1 0"/>
          {pg("torso", cloth, type="ellipsoid", size=_v(sh * 0.95, 0.11 * k * g, spine * 0.55), pos=_v(0, 0, spine * 0.55),
              rgba="0.3 0.4 0.7 1")}
        </body>
        <body name="neck" pos="{_v(0, 0, spine)}">
          <joint name="neck_pitch" type="hinge" axis="1 0 0"/>
          <joint name="head_yaw" type="hinge" axis="0 0 1"/>
          <joint name="head_roll" type="hinge" axis="0 1 0"/>
          <joint name="neck_flex" type="hinge" axis="1 0 0" armature="0.01"/>
          {pg("neck", skin, type="capsule", fromto=_v(0, 0, 0, 0, 0, 0.09 * k), size=_v(0.05 * k * g), rgba="0.9 0.75 0.65 1")}
          <body name="head" pos="{_v(0, 0, 0.09 * k)}">
            <joint name="head_ext" type="hinge" axis="1 0 0" armature="0.01"/>
            {pg("head", skin, type="ellipsoid", size=_v(*head_size), pos=_v(head_pos[0], head_pos[1], head_pos[2] - 0.09 * k), rgba="0.9 0.75 0.65 1")}
            {pg("hair", hair, type="ellipsoid", size=_v(*hair_size),
                pos=_v(head_pos[0], head_pos[1] + 0.012 * hk, head_pos[2] + 0.02 * hk - 0.09 * k), rgba="0.1 0.08 0.05 1")}
            <site name="chin" pos="{_v(0, -0.06 * hk, head_pos[2] - 0.10 * hk - 0.09 * k)}"/>
            <site name="mouth" pos="{_v(0, -0.09 * hk, head_pos[2] - 0.05 * hk - 0.09 * k)}"/>
          </body>
        </body>
        {arm("l", sh)}
        {arm("r", -sh)}
      </body>
    </body>"""

    assets = ""
    if body is not None:
        from . import smplx_body as sb
        assets = sb.mesh_assets(body)
        person_xml = _mesh_person_xml(person, body, pg, chair_y, hip_z)

    xml = f"""
<mujoco model="deskmate_tof">
  <compiler angle="radian" autolimits="true" boundmass="0.01" boundinertia="0.0001"/>
  <option gravity="0 0 0"/>
  <asset>{assets}</asset>
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
    {walker}
    {prop}
    {person_xml}
  </worldbody>
</mujoco>"""
    return xml, refl


def _mesh_person_xml(person: Person, body, pg, chair_y: float, hip_z: float) -> str:
    """The person body tree with SMPL-X mesh pieces instead of capsules (same joints and sites)."""
    skin, cloth, hair = person.skin_refl, person.torso_refl, person.hair_refl
    P = body.parts

    def mesh(name, refl_value, at_origin=True, rgba="0.3 0.4 0.7 1"):
        # the piece's vertices are already in the body frame (smplx_body.make), so no rotation here
        p = P[name]
        pos = (0.0, 0.0, 0.0) if at_origin else tuple(p.offset)
        return pg(name, refl_value, type="mesh", mesh=f"sx_{name}", pos=_v(*pos), rgba=rgba)

    # Inner core: an invisible, slightly smaller capsule body inside the mesh skin. Rigid pieces
    # open a little where a limb turns; a ray slipping through such a seam then stops on the core,
    # one or two centimetres under the skin, instead of crossing to the far inner surface.
    k = person.scale
    HIDE = "0.3 0.3 0.3 0"

    def core(name, refl_value, **attrs):
        return pg(name, refl_value, rgba=HIDE, **attrs)

    def arm(side):
        o = P[f"uarm_{side}"].offset
        return f"""
        <body name="uarm_{side}" pos="{_v(*o)}">
          <joint name="shoulder_{side}" type="ball"/>
          {mesh(f"uarm_{side}", cloth)}
          {core(f"core_uarm_{side}", cloth, type="capsule", fromto=_v(0, 0, 0.02 * k, 0, 0, -body.ua), size=_v(0.040 * k))}
          <body name="farm_{side}" pos="{_v(0, 0, -body.ua)}">
            <joint name="elbow_{side}" type="hinge" axis="1 0 0"/>
            {mesh(f"farm_{side}", skin, rgba="0.9 0.75 0.65 1")}
            {core(f"core_farm_{side}", skin, type="capsule", fromto=_v(0, 0, 0, 0, 0, -body.fa), size=_v(0.034 * k))}
            <body name="hand_{side}" pos="{_v(0, 0, -body.fa)}">
              {mesh(f"hand_{side}", skin, rgba="0.9 0.75 0.65 1")}
              {core(f"core_hand_{side}", skin, type="ellipsoid", size=_v(0.038 * k, 0.014 * k, 0.045 * k), pos=_v(0, 0, -0.05 * k))}
              <site name="wrist_{side}" pos="0 0 0"/>
            </body>
          </body>
        </body>"""

    cap = mesh("hair", hair, rgba="0.1 0.08 0.05 1")                 # scalp piece of the head mesh, dark at 940 nm
    jaw = body.joints[22] - body.joints[15]                         # jaw joint in the head (skull base) frame
    legs = "".join(mesh(n, cloth, at_origin=False, rgba="0.2 0.2 0.3 1") for n in ("thigh_l", "shin_l", "thigh_r", "shin_r"))
    for s in ("l", "r"):
        hip, knee = P[f"thigh_{s}"].offset, P[f"shin_{s}"].offset
        legs += core(f"core_thigh_{s}", cloth, type="capsule", fromto=_v(*hip, *knee), size=_v(0.062 * k))
        legs += core(f"core_shin_{s}", cloth, type="capsule", fromto=_v(*knee, knee[0], knee[1], knee[2] - 0.42 * k), size=_v(0.048 * k))
    legs += core("core_pelvis", cloth, type="ellipsoid", size=_v(0.13 * k, 0.095 * k, 0.09 * k), pos=_v(0, 0, 0.02 * k))
    hv_ = P["head"].verts
    hlo, hhi = hv_.min(0), hv_.max(0)
    head_core = core("core_head", skin, type="ellipsoid", size=_v(*(0.45 * (hhi - hlo))), pos=_v(*(0.5 * (hlo + hhi))))
    trunk_core = core("core_trunk", cloth, type="ellipsoid", size=_v(body.sh * 0.92, 0.10 * k, body.spine * 0.52),
                      pos=_v(0, 0, body.spine * 0.53))
    return f"""
    <body name="pelvis" pos="{_v(person.x_offset, chair_y, hip_z)}">
      <joint name="hip_shift" type="slide" axis="0 1 0" range="-0.3 0.8"/>
      <joint name="pelvis_tilt" type="hinge" axis="1 0 0" range="-0.5 0.1"/>
      {mesh("pelvis", cloth, rgba="0.2 0.2 0.3 1")}
      {legs}
      <body name="trunk">
        <joint name="trunk_pitch" type="hinge" axis="1 0 0"/>
        <joint name="trunk_roll" type="hinge" axis="0 1 0"/>
        <body name="chest">
          <joint name="breath" type="slide" axis="0 -1 0"/>
          {mesh("trunk", cloth)}
          {trunk_core}
        </body>
        <body name="neck" pos="{_v(*P["neck"].offset)}">
          <joint name="neck_pitch" type="hinge" axis="1 0 0"/>
          <joint name="head_yaw" type="hinge" axis="0 0 1"/>
          <joint name="head_roll" type="hinge" axis="0 1 0"/>
          <joint name="neck_flex" type="hinge" axis="1 0 0" armature="0.01"/>
          {mesh("neck", skin, rgba="0.9 0.75 0.65 1")}
          {core("core_neck", skin, type="capsule", fromto=_v(0, 0, -0.02 * k, 0, 0, body.neck + 0.01 * k), size=_v(0.045 * k))}
          <body name="head" pos="{_v(*P["head"].offset)}">
            <joint name="head_ext" type="hinge" axis="1 0 0" armature="0.01"/>
            {mesh("head", skin, rgba="0.9 0.75 0.65 1")}
            {cap}
            {head_core}
            <site name="chin" pos="{_v(jaw[0], jaw[1] - 0.07, jaw[2] - 0.05)}"/>
            <site name="mouth" pos="{_v(jaw[0], jaw[1] - 0.09, jaw[2])}"/>
          </body>
        </body>
        {arm("l")}
        {arm("r")}
      </body>
    </body>"""


def _elbow_on_plane(S, W, L1, L2, z, y_max):
    """Elbow E with |E - S| = L1, |E - W| = L2 and E[2] = z, in front of the desk edge (E[1] <= y_max).

    The two candidates lie on the circle where the two spheres meet; the one further toward the
    desk (smaller y) is returned, or None if there is none.
    """
    v = W - S
    D = float(np.linalg.norm(v))
    if D < 1e-6 or D > L1 + L2 or D < abs(L1 - L2):
        return None
    n = v / D
    a = (L1 * L1 - L2 * L2 + D * D) / (2 * D)
    h = math.sqrt(max(L1 * L1 - a * a, 0.0))
    c = S + a * n
    u = np.cross(n, [0.0, 0.0, 1.0])
    if np.linalg.norm(u) < 1e-6:
        u = np.array([1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    A, B, C = h * u[2], h * w[2], z - c[2]          # A cos t + B sin t = C
    R = math.hypot(A, B)
    if R < 1e-9 or abs(C) > R:
        return None
    base, dlt = math.atan2(B, A), math.acos(C / R)
    cands = [c + h * (math.cos(t) * u + math.sin(t) * w) for t in (base + dlt, base - dlt)]
    cands = [e for e in cands if e[1] <= y_max]
    return min(cands, key=lambda e: e[1]) if cands else None


def _quat_from_mat(R: np.ndarray) -> np.ndarray:
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, R.reshape(-1))
    return q


class MjDesk:
    """One compiled session scene. set_pose() then cast()."""

    def __init__(self, room: Room, person: Person):
        self.room, self.person = room, person
        body = None
        if getattr(person, "smplx", None):
            from . import smplx_body as sb
            body = sb.make_cached(person.smplx["gender"], tuple(person.smplx["betas"]), round(person.hair_cover, 3))
        self.body_mesh = body
        xml, refl = build_mjcf(room, person, body)
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
        self.ua, self.fa = (body.ua, body.fa) if body is not None else (0.30 * k, 0.27 * k)
        self.neck_len = body.neck if body is not None else 0.09 * k      # neck base -> skull base
        self.head_off = np.array(body.parts["head"].offset, dtype=float) if body is not None else np.array([0.0, 0.0, 0.09 * k])
        self._geomgroup_all = np.array([1, 1, 0, 1, 0, 0], dtype=np.uint8)     # group 2 = monitor, never hit; 3 = bystander
        self._geomgroup_empty = np.array([1, 0, 0, 1, 0, 0], dtype=np.uint8)
        self._walker = self.model.body_mocapid[self.body("walker")]
        self._prop = self.model.body_mocapid[self.body("prop")]

    # ------------------------------------------------------------------ pose
    def set_walker(self, pos):
        """Bystander at body-frame (x, z toward the user) on the floor, or parked under the floor."""
        self.data.mocap_pos[self._walker] = (0.0, 0.0, -10.0) if pos is None else (pos[0], pos[1], 0.0)

    def set_pose(self, pose: Pose | None):
        m, d = self.model, self.data
        d.qpos[:] = 0.0
        d.mocap_pos[self._prop] = (0.0, 0.0, -10.0)
        for side in ("l", "r"):
            d.qpos[self.jnt[f"shoulder_{side}"]] = 1.0        # identity quaternion
        if pose is None:
            self.present = False
            self._chin = None
            mujoco.mj_kinematics(m, d)
            return
        self.present = True
        q, j = d.qpos, self.jnt
        q[j["hip_shift"]] = pose.hip_shift
        q[j["trunk_pitch"]] = pose.trunk_pitch      # + = toward the desk (top moves to -y)
        q[j["trunk_roll"]] = pose.trunk_roll        # + = lean to the person's left = sensor's right (+x); hinge y points to -y (body faces the desk)
        q[j["breath"]] = pose.breath
        q[j["neck_pitch"]] = pose.neck_pitch
        q[j["head_yaw"]] = pose.head_yaw
        q[j["head_roll"]] = pose.head_roll          # + = tilt to the person's left (+x), same sense as trunk_roll
        # forward-head posture: bend the neck forward and the head back by the same angle, so the
        # head translates toward the desk (neck_len * sin) and drops a little, face direction unchanged
        h = min(max(float(pose.head_forward), 0.0), self.neck_len * np.sin(np.radians(70.0)))
        flex = float(np.arcsin(h / self.neck_len)) if h > 0.0 else 0.0
        q[j["neck_flex"]] = flex
        q[j["head_ext"]] = -flex
        # office-chair recline: the seat tips back about a third of the lean (knees rise), the
        # backrest follows the trunk; the trunk still ends at the requested world angle
        seat = 0.35 * min(0.0, pose.trunk_pitch)
        q[j["pelvis_tilt"]] = seat
        q[j["trunk_pitch"]] = pose.trunk_pitch - seat
        if "chair_tilt" in j:
            q[j["seat_tilt"]] = seat
            q[j["chair_tilt"]] = min(0.0, pose.trunk_pitch) - seat
        mujoco.mj_kinematics(m, d)
        self._chin = self._settle_chin(pose) if pose.arms in self.CHIN_ARMS else None
        self._place_arms(pose)
        mujoco.mj_kinematics(m, d)

    # propping arm(s) for each chin mode: (side, +1 for the arm at +x)
    CHIN_ARMS = {"chin_right": (("l", 1.0),), "chin_left": (("r", -1.0),), "chin_both": (("l", 1.0), ("r", -1.0))}

    def _chin_arms(self, mode):
        """{side: (wrist, elbow)} for the propping arm(s) with the elbow on the desk top, in the
        current trunk pose, or None if an arm cannot reach."""
        d, room, p = self.data, self.room, self.person
        k = p.scale
        chin = d.site_xpos[self.site("chin")]
        z_el = room.desk_height + 0.038 * k * p.girth            # forearm resting on the desk top
        out = {}
        for side, sign in self.CHIN_ARMS[mode]:
            S = d.xpos[self.body(f"uarm_{side}")].copy()
            dx = 0.035 * sign if mode == "chin_both" else 0.0
            W = chin + np.array([dx, 0.02, -0.08 * k])             # wrist under the chin, hand up to it
            E = _elbow_on_plane(S, W, self.ua, self.fa, z_el, room.desk_depth - 0.03)
            if E is None or abs(E[0]) > room.desk_width / 2 - 0.02:
                return None
            out[side] = (W, E)
        return out

    def _settle_chin(self, pose: Pose):
        """Bend the trunk forward (and slide the chair) until the propping elbow(s) can rest on the
        desk. Returns _chin_arms() of the pose found, or None (the arm then floats as before)."""
        m, d = self.model, self.data
        q, j = d.qpos, self.jnt
        base_p, base_h = float(q[j["trunk_pitch"]]), float(q[j["hip_shift"]])
        for dh in (0.0, -0.06, 0.06, -0.12, 0.12):
            for dp in np.radians([0, 2.5, -2.5, 5, -5, 7.5, 10, 12.5, 15, 17.5, 20, 22.5, 25, 30, 35]):
                q[j["trunk_pitch"]], q[j["hip_shift"]] = base_p + dp, base_h + dh
                mujoco.mj_kinematics(m, d)
                arms = self._chin_arms(pose.arms)
                if arms is not None:
                    return arms
        q[j["trunk_pitch"]], q[j["hip_shift"]] = base_p, base_h
        mujoco.mj_kinematics(m, d)
        return None

    def _place_arms(self, pose: Pose):
        d, room, p = self.data, self.room, self.person
        k = p.scale
        desk_top = room.desk_height + 0.03
        chin = d.site_xpos[self.site("chin")].copy()
        mouth = d.site_xpos[self.site("mouth")].copy()
        trunk_R = d.xmat[self.body("trunk")].reshape(3, 3)
        kb_y = room.desk_depth - 0.20 + pose.arm_jitter
        chin_sides = {s for s, _ in self.CHIN_ARMS.get(pose.arms, ())}
        for side, sign in (("l", 1.0), ("r", -1.0)):     # "l" = person's left = sensor's right (+x)
            S = d.xpos[self.body(f"uarm_{side}")].copy()
            mode = pose.arms
            active_cup = mode == "cup_right" and side == "l"
            active_one = self.ONE_HANDED.get(mode) == side
            if side in chin_sides and self._chin is not None:
                W, E = self._chin[side]
                pole = E                                   # |E-S| = upper arm, |E-W| = forearm: the elbow lands on E
            elif side in chin_sides:                       # no reachable desk point: hand to the chin, elbow free
                W = chin + np.array([0.035 * sign if mode == "chin_both" else 0.0, 0.02, -0.03])
                pole = np.array([S[0] * 0.6 + chin[0] * 0.4, room.desk_depth - 0.12, desk_top])
            elif active_cup:
                W = mouth + np.array([0.0, -0.05, -0.02])
                pole = S + np.array([0.08 * sign, 0.05, -0.4])
            elif active_one:
                W, pole = self._one_hand_target(mode, sign, S, trunk_R, kb_y, desk_top, pose, mouth)
            else:
                if mode in self.CHIN_ARMS or mode == "cup_right" or mode in self.ONE_HANDED:
                    mode = pose.other_arms     # the arm that is not propping the chin / holding the cup / busy
                W, pole = self._arm_target(mode, side, sign, S, trunk_R, kb_y, desk_top)
            self._ik_arm(side, S, W, pole, trunk_R)
        if pose.arms == "book":
            self._place_book(trunk_R)

    # one-handed activities: which arm is busy ("r" = the person's right, at -x); the other follows other_arms
    ONE_HANDED = {"phone_call": "r", "eat": "r", "bag": "r"}

    def _one_hand_target(self, mode, sign, S, trunk_R, kb_y, desk_top, pose, mouth):
        """Wrist target and elbow pole for the busy arm of a one-handed activity."""
        d, room, p = self.data, self.room, self.person
        k = p.scale
        if mode == "phone_call":
            # hand at the ear on the same side; elbow out and forward
            head_b = self.body("head")
            ear = d.xpos[head_b] + d.xmat[head_b].reshape(3, 3) @ (np.array([0.085 * sign * k, 0.0, 0.17 * k]) - self.head_off)
            return ear + np.array([0.02 * sign, -0.01, -0.02]), S + np.array([0.22 * sign, -0.28, -0.10])
        if mode == "eat":
            # hand goes from the desk (snack in front of the keyboard) to the mouth and back; arm_phase 0..1
            s = 0.5 - 0.5 * np.cos(2 * np.pi * pose.arm_phase)
            on_desk = np.array([p.x_offset + 0.06 * sign, kb_y - 0.04, desk_top + 0.04])
            at_mouth = mouth + np.array([0.0, -0.09, -0.09])     # wrist below the mouth, fingers reach it
            return on_desk * (1 - s) + at_mouth * s, S + np.array([0.10 * sign, 0.05, -0.4])
        if mode == "bag":
            # reaching down to a bag on the floor beside the chair (the trunk leans that way via trunk_roll)
            chair_y = d.xpos[self.body("pelvis")][1]
            return np.array([S[0] + 0.32 * sign, chair_y + 0.05, 0.15]), S + np.array([0.30 * sign, 0.10, -0.25])
        raise ValueError(mode)

    def _place_book(self, trunk_R):
        """A book held in both hands in front of the chest, tilted back toward the face."""
        d, p = self.data, self.person
        k = p.scale
        trunk_o = d.xpos[self.body("trunk")]
        centre = trunk_o + trunk_R @ np.array([0.0, -0.30 * k, 0.52 * k * 0.74])
        tilt = np.radians(35.0)
        R = trunk_R @ np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
        q = np.zeros(4)
        mujoco.mju_mat2Quat(q, np.ascontiguousarray(R).reshape(-1))
        d.mocap_pos[self._prop] = centre
        d.mocap_quat[self._prop] = q

    def _arm_target(self, mode, side, sign, S, trunk_R, kb_y, desk_top):
        """Wrist target and elbow pole for a free arm. sign = +1 for the arm at +x."""
        d, room, p = self.data, self.room, self.person
        k = p.scale
        spine = 0.52 * k
        trunk_o = d.xpos[self.body("trunk")]
        head_b = self.body("head")
        head_c = d.xpos[head_b] + d.xmat[head_b].reshape(3, 3) @ (np.array([0.0, -0.01 * k, 0.19 * k]) - self.head_off)
        # trunk frame: +y is the person's back, -y their front, +z up the spine
        chest_front = trunk_o + trunk_R @ np.array([0.0, -0.13 * k, spine * 0.45])
        if mode == "mouse":
            if sign > 0:                                   # left hand stays on the keyboard
                return self._arm_target("typing", side, sign, S, trunk_R, kb_y, desk_top)
            jit = kb_y - (room.desk_depth - 0.20)          # the typing jitter, reused as mouse motion
            W = np.array([p.x_offset - 0.30 + 1.5 * jit, room.desk_depth - 0.16 - 1.0 * jit, desk_top + 0.03])
            pole = S + np.array([0.06 * sign, 0.10, -0.35])
        elif mode == "book":
            # both hands hold a book in front of the chest (prop placed by _place_book)
            W = trunk_o + trunk_R @ np.array([0.11 * sign, -0.28 * k, spine * 0.70 - 0.04 * k])
            pole = S + trunk_R @ np.array([0.10 * sign, -0.05, -0.30])
        elif mode == "pillow":
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
        elif mode == "writing":
            # pen hand (the person's right, at -x) on the paper in front of them; the other hand
            # rests flat on the sheet a little further out and forward
            if sign < 0:
                W = np.array([p.x_offset - 0.10, kb_y - 0.02, desk_top + 0.015])
            else:
                W = np.array([p.x_offset + 0.17, kb_y + 0.05, desk_top + 0.0])
        else:  # typing
            W = np.array([S[0] * 0.55, kb_y, desk_top + 0.03])
        if mode in ("typing", "writing", "mouse"):
            # Keep the elbow bent: a keyboard beyond the relaxed reach is not chased with a
            # straight arm; the hands rest nearer on the desk (between keyboard and desk edge).
            R = p.reach * (self.ua + self.fa)
            dx, dz = float(W[0] - S[0]), float(W[2] - S[2])
            hy = math.sqrt(max(R * R - dx * dx - dz * dz, 0.0))
            if S[1] - W[1] > hy:                          # the person faces -y
                W[1] = min(S[1] - hy, room.desk_depth - 0.03)
            if np.linalg.norm(W - S) > 0.97 * (self.ua + self.fa):
                # even the desk edge is out of easy reach (chair far back): hands rest on the lap
                return self._arm_target("lap", side, sign, S, trunk_R, kb_y, desk_top)
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
        # Keep the arm's roll fixed: the hinge axis (local x) always points to the person's left,
        # which is how the mesh pieces were cut. Otherwise the arm can end up twisted half a turn
        # (palms out, thumbs out) while the capsules looked the same.
        if nx < 0 or (abs(nx) < 1e-6 and ny > 0):
            nx, ny, nz = -nx, -ny, -nz
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
    # yaw and roll are negated so both backends agree with sensor._rot (+yaw turns the beam toward +x)
    p, yaw, r = np.radians([pose.pitch_down_deg, -pose.yaw_deg, -pose.roll_deg + (180.0 if pose.upside_down else 0.0)])
    B = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=float)   # columns: right=x, up=z, forward=y
    cp, sp = np.cos(-p), np.sin(-p)
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])          # about x: -pitch tips forward down
    cy, sy = np.cos(yaw), np.sin(yaw)
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    cr, sr = np.cos(r), np.sin(r)
    Ry = np.array([[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]])          # roll about forward (y)
    return origin, Rz @ Rx @ Ry @ B
