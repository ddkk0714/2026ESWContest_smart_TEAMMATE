"""MuJoCo 장면: 책상·의자·벽·앉은 사람. 물리는 쓰지 않고 자세(qpos) → 광선 교차만 쓴다.

월드 좌표(m): x = 센서에서 사람 쪽(앞), y = 센서가 볼 때 왼쪽, z = 위. 바닥 z = 0.
사람은 -x 를 보고 앉는다. 사람 기준 왼쪽 = -y = 센서가 볼 때 오른쪽.

반사율(940 nm)은 MuJoCo 가 모르므로 geom 이름 → 반사율 표로 따로 들고 다닌다(Scene.reflectance).
사람 geom 은 group 2 라서 자리비움은 광선에서 group 2 를 빼서 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import mujoco
import numpy as np

PERSON_GROUP = 2


@dataclass
class Person:
    """사람 한 명의 체형·재질. 길이는 scale 로 한꺼번에 늘이고 줄인다."""
    scale: float = 1.0           # 키 비율(1.0 ≈ 170 cm)
    width: float = 1.0           # 어깨·몸통 폭 비율
    hair: float = 0.10           # 머리카락 반사율(검은 머리 0.05~0.15)
    skin: float = 0.45
    clothes: float = 0.30        # 검은 옷이라도 940 nm 에서는 밝을 수 있다
    pants: float = 0.20


@dataclass
class Room:
    desk_h: float = 0.73         # 책상 높이
    seat_h: float = 0.46
    dist: float = 0.62           # 센서 → 사람 가슴 앞면(수평) 거리
    person_y: float = 0.0        # 사람 좌우 위치
    desk_edge: float = 0.38      # 센서 → 책상 앞 모서리(사람 쪽) 거리
    wall: float | None = 1.8     # 센서 → 사람 뒤 벽 거리. None 이면 벽 없음(4 m 너머)
    chair_back: float = 0.15     # 가슴 앞면 → 등받이 앞면
    chair_tilt_deg: float = 8.0  # 등받이 뒤로 기운 각
    desk_refl: float = 0.35
    wall_refl: float = 0.70
    chair_refl: float = 0.10
    floor_refl: float = 0.25


@dataclass
class Scene:
    model: mujoco.MjModel
    data: mujoco.MjData
    person: Person
    room: Room
    reflectance: np.ndarray                     # geom id → 반사율
    person_geoms: np.ndarray                    # geom id 가 사람 것인지
    qadr: dict[str, int] = field(default_factory=dict)  # 관절 이름 → qpos 주소

    def set_q(self, q: dict[str, float]) -> None:
        self.data.qpos[:] = 0.0
        for name, v in q.items():
            self.data.qpos[self.qadr[name]] = v
        mujoco.mj_kinematics(self.model, self.data)

    def set_chair_tilt(self, deg: float) -> None:
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "chair_back")
        a = np.radians(deg) / 2
        self.model.body_quat[bid] = (np.cos(a), 0.0, np.sin(a), 0.0)  # +y 축 회전 = 뒤로
        mujoco.mj_kinematics(self.model, self.data)

    def get_q(self) -> dict[str, float]:
        return {k: float(self.data.qpos[a]) for k, a in self.qadr.items()}


JOINTS = (
    "lumbar_pitch", "lumbar_roll", "thoracic_pitch", "neck_pitch", "head_pitch", "head_roll",
    "head_yaw",
    "sh_L_x", "sh_L_y", "sh_L_z", "elbow_L", "sh_R_x", "sh_R_y", "sh_R_z", "elbow_R",
)
# 부호: *_pitch 는 +y 축 회전이라 양수 = 위쪽이 +x(센서에서 멀어짐) = 뒤로. 앞으로 숙임은 음수.


def _f(v: float) -> str:
    return f"{v:.4f}"


def build_xml(p: Person, r: Room) -> str:
    s, w = p.scale, p.width
    chest_x = r.dist
    hip = (chest_x + 0.12 * s, r.person_y, r.seat_h + 0.09 * s)
    back_x = chest_x + r.chair_back + 0.11 * s
    tilt = np.radians(r.chair_tilt_deg)
    wall = ""
    if r.wall is not None:
        wall = (f'<geom name="wall" type="box" pos="{_f(r.wall + 0.05)} 0 1.5" size="0.05 3 1.5" '
                f'rgba=".85 .85 .8 1"/>')

    def cap(name: str, a, b, rad: float, rgba: str) -> str:
        return (f'<geom name="{name}" type="capsule" fromto="{" ".join(_f(x) for x in (*a, *b))}" '
                f'size="{_f(rad)}" group="{PERSON_GROUP}" rgba="{rgba}"/>')

    cloth, skin, hair, pants = ".25 .35 .6 1", ".9 .7 .6 1", ".15 .1 .08 1", ".2 .2 .25 1"
    arms = []
    for side, sy in (("L", -1.0), ("R", 1.0)):  # 사람 왼쪽 = -y
        arms.append(f'''
          <body name="uarm_{side}" pos="0 {_f(sy * 0.175 * s * w)} {_f(0.185 * s)}">
            <joint name="sh_{side}_x" type="hinge" axis="1 0 0" range="-1.6 1.6"/>
            <joint name="sh_{side}_y" type="hinge" axis="0 1 0" range="-0.8 2.8"/>
            <joint name="sh_{side}_z" type="hinge" axis="0 0 1" range="-1.6 1.6"/>
            {cap(f"uarm_{side}", (0, 0, 0), (0, 0, -0.28 * s), 0.045 * s, cloth)}
            <site name="elbow_{side}" pos="0 0 {_f(-0.28 * s)}"/>
            <body name="farm_{side}" pos="0 0 {_f(-0.28 * s)}">
              <joint name="elbow_{side}" type="hinge" axis="0 1 0" range="0 2.6"/>
              {cap(f"farm_{side}", (0, 0, 0), (0, 0, -0.24 * s), 0.037 * s, skin)}
              <geom name="hand_{side}" type="ellipsoid" pos="0 0 {_f(-0.31 * s)}"
                    size="{_f(0.03 * s)} {_f(0.045 * s)} {_f(0.08 * s)}" group="{PERSON_GROUP}" rgba="{skin}"/>
              <site name="hand_{side}" pos="0 0 {_f(-0.33 * s)}"/>
            </body>
          </body>''')

    legs = "".join(
        cap(f"thigh_{k}", (0, sy * 0.09 * s * w, 0), (-0.42 * s, sy * 0.10 * s * w, 0.02), 0.075 * s, pants)
        + cap(f"shin_{k}", (-0.42 * s, sy * 0.10 * s * w, 0.02), (-0.45 * s, sy * 0.10 * s * w, -hip[2] + 0.05),
              0.05 * s, pants)
        for k, sy in (("L", -1.0), ("R", 1.0)))

    return f'''<mujoco model="deskmate_l8">
  <compiler angle="radian"/>
  <option gravity="0 0 0"/>
  <visual><global offwidth="960" offheight="720"/></visual>
  <worldbody>
    <light pos="0.3 0 2.5" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="4 4 0.1" rgba=".55 .5 .45 1"/>
    {wall}
    <geom name="desk" type="box" pos="{_f((r.desk_edge - 0.45) / 2 + 0.0)} 0 {_f(r.desk_h - 0.015)}"
          size="{_f((r.desk_edge + 0.45) / 2)} 0.7 0.015" rgba=".75 .6 .45 1"/>
    <geom name="chair_seat" type="box" pos="{_f(chest_x + 0.05)} {_f(r.person_y)} {_f(r.seat_h - 0.04)}"
          size="0.24 0.24 0.04" rgba=".1 .1 .1 1"/>
    <body name="chair_back" pos="{_f(back_x)} {_f(r.person_y)} {_f(r.seat_h + 0.05)}" euler="0 {_f(tilt)} 0">
      <geom name="chair_back" type="box" pos="0.03 0 0.30" size="0.03 0.23 0.27" rgba=".1 .1 .1 1"/>
    </body>
    <body name="sensor" pos="0 0 {_f(r.desk_h + 0.1)}">
      <geom name="sensor_box" type="box" size="0.01 0.01 0.01" rgba="1 0 0 1" contype="0" conaffinity="0" group="5"/>
    </body>
    <body name="pelvis" pos="{_f(hip[0])} {_f(hip[1])} {_f(hip[2])}">
      {cap("pelvis", (0, -0.12 * s * w, 0), (0, 0.12 * s * w, 0), 0.10 * s, pants)}
      {legs}
      <body name="torso_lo" pos="0 0 {_f(0.05 * s)}">
        <joint name="lumbar_pitch" type="hinge" axis="0 1 0" range="-1.2 0.7"/>
        <joint name="lumbar_roll" type="hinge" axis="1 0 0" range="-0.6 0.6"/>
        {cap("torso_lo_a", (0, -0.06 * s * w, 0), (0, -0.06 * s * w, 0.20 * s), 0.105 * s, cloth)}
        {cap("torso_lo_b", (0, 0.06 * s * w, 0), (0, 0.06 * s * w, 0.20 * s), 0.105 * s, cloth)}
        <body name="torso_up" pos="0 0 {_f(0.22 * s)}">
          <joint name="thoracic_pitch" type="hinge" axis="0 1 0" range="-0.8 0.5"/>
          {cap("chest_a", (0, -0.07 * s * w, 0), (0, -0.07 * s * w, 0.17 * s), 0.11 * s, cloth)}
          {cap("chest_b", (0, 0.07 * s * w, 0), (0, 0.07 * s * w, 0.17 * s), 0.11 * s, cloth)}
          {cap("shoulders", (0, -0.16 * s * w, 0.19 * s), (0, 0.16 * s * w, 0.19 * s), 0.06 * s, cloth)}
          <site name="chest" pos="{_f(-0.11 * s)} 0 {_f(0.08 * s)}"/>
          <body name="neck" pos="0 0 {_f(0.24 * s)}">
            <joint name="neck_pitch" type="hinge" axis="0 1 0" range="-1.0 0.6"/>
            {cap("neck", (0, 0, 0), (0, 0, 0.09 * s), 0.05 * s, skin)}
            <body name="head" pos="0 0 {_f(0.10 * s)}">
              <joint name="head_pitch" type="hinge" axis="0 1 0" range="-1.0 0.7"/>
              <joint name="head_roll" type="hinge" axis="1 0 0" range="-0.6 0.6"/>
              <joint name="head_yaw" type="hinge" axis="0 0 1" range="-1.0 1.0"/>
              <geom name="cranium" type="ellipsoid" pos="{_f(0.012 * s)} 0 {_f(0.10 * s)}"
                    size="{_f(0.097 * s)} {_f(0.079 * s)} {_f(0.112 * s)}" group="{PERSON_GROUP}" rgba="{hair}"/>
              <geom name="face" type="ellipsoid" pos="{_f(-0.032 * s)} 0 {_f(0.07 * s)}"
                    size="{_f(0.072 * s)} {_f(0.066 * s)} {_f(0.092 * s)}" group="{PERSON_GROUP}" rgba="{skin}"/>
              <site name="chin" pos="{_f(-0.075 * s)} 0 {_f(-0.005 * s)}"/>
              <site name="head_c" pos="0 0 {_f(0.09 * s)}"/>
            </body>
          </body>
          {"".join(arms)}
        </body>
      </body>
    </body>
  </worldbody>
</mujoco>'''


def _refl_for(name: str, p: Person, r: Room) -> float:
    if name == "cranium":
        return p.hair
    if name in ("face", "neck") or name.startswith(("farm_", "hand_")):
        return p.skin
    if name.startswith(("thigh_", "shin_", "pelvis")):
        return p.pants
    if name.startswith(("torso", "chest", "shoulders", "uarm_")):
        return p.clothes
    return {"desk": r.desk_refl, "wall": r.wall_refl, "chair_seat": r.chair_refl,
            "chair_back": r.chair_refl, "floor": r.floor_refl}.get(name, 0.0)


def build_scene(p: Person | None = None, r: Room | None = None) -> Scene:
    p, r = p or Person(), r or Room()
    m = mujoco.MjModel.from_xml_string(build_xml(p, r))
    d = mujoco.MjData(m)
    refl = np.zeros(m.ngeom)
    person = np.zeros(m.ngeom, dtype=bool)
    for g in range(m.ngeom):
        name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
        refl[g] = _refl_for(name, p, r)
        person[g] = m.geom_group[g] == PERSON_GROUP
    qadr = {j: int(m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, j)]) for j in JOINTS}
    sc = Scene(m, d, p, r, refl, person, qadr)
    sc.set_q({})
    return sc
