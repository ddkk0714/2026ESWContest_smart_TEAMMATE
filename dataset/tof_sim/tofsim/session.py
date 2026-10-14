"""Randomized desk sessions: who sits where, what they do, frame by frame.

A session fixes the room, the person, the sensor mounting and the sensor
model, then plays a timeline of posture segments at the sensor frame rate.
Every frame carries the ground-truth label, so nothing has to be annotated.

Labels follow the on-board estimator (firmware TofPosture.h) minus `unknown`,
which only exists because the estimator has to learn a baseline first:

    0 upright · 1 lean_forward · 2 recline · 3 face_down · 4 chin_rest · 5 drowsy · 6 away · 7 misplaced

`misplaced` is not a posture: the device stands where it cannot see the user properly (upside
down, too near, too far, pointing past them, tilted well off), so the product should ask for a
re-adjustment. It only arises in placement mode "random" (PLACEMENT_MODES, _placement_ok).

Each segment also includes the look-alikes that a real desk throws at the
classifier, labelled as what they are: glancing down at the keyboard,
drinking from a cup, turning the head, typing jitter, breathing, swaying.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from .body import Person, Pose, Room, build_scene
from . import smplx_body as sb
from .sensor import PRESETS, Sensor, SensorModel, SensorPose, SensorSpec, _rot

LABELS = ["upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away", "misplaced"]

# Sensor placement: a small desk stand (Stream Deck-like) anywhere on an arc in front of the
# user. At the demo the arc is split into three 30 deg sectors (left / front / right); the
# azimuth is kept in SessionConfig.meta so data can be split the same way.
SENSOR_AZIMUTHS_DEG = tuple(range(-45, 46, 15))  # 7 device positions on the arc; + = the user's left (+x)
# The sensor device, measured from the team CAD (TEAMMATE.step, 2026-10-14): one housing with two
# VL53L8CX behind cover glasses. Body frame here: x = the user's left, y up, z toward the user.
DEVICE_HALF_M = (0.0988, 0.0584)    # footprint half width (x) and half depth (z), centre = device origin
DEVICE_SENSORS = (                  # (offset from the footprint centre on the desk, up deg, outward yaw deg)
    ((0.0524, 0.1299, -0.0003), 20.0, 8.0),     # 0: the user's-left sensor (CAD -x), turned 8 deg to its side
    ((-0.0524, 0.1299, -0.0003), 20.0, -8.0),   # 1: the user's-right sensor
)
SENSOR_DIST_M = (0.60, 1.10)       # arc radius: horizontal distance from each device to the chin
DESK_CLEARANCE_M = 0.005           # rays must stay above the desk top + this
CLUTTER_MAX = 0                    # boxes against the back wall per session (0 = plain wall by default);
                                   # sample_session(clutter_max=N) / train.py --clutter N override it per run
WALK_EDGE_M = 1.8                  # |x| where someone walking out of the room leaves the scene (as _bystander_pos)
STAND_FRONT_M = 0.05               # someone who stands up is this much in front of where they sat (and 0.2 m off the desk)
HEAD_COLUMNS = (2.0, 2.67)         # how many zone columns the head spans, seen from the arc
HEAD_REF_DIST_M = 0.85             # random placement: the head is sized for HEAD_COLUMNS at this distance

# Placement modes (sample_session(placement=...)):
#   "arc"    the demo: a device at one of SENSOR_AZIMUTHS_DEG, SENSOR_DIST_M from the chin, aimed at it
#   "random" wherever a user might put it: free distance and azimuth, rough aim, maybe on a riser, and a
#            share of deliberately wrong spots; the session is `misplaced` when _placement_ok says no
#   "mixed"  each session is "arc" or "random" with equal chance
PLACEMENT_MODES = ("arc", "random", "mixed")
RANDOM_DIST_M = (0.35, 1.45)        # random: horizontal device-to-chin distance drawn here (the desk permitting)
RANDOM_AZIMUTH_DEG = (-60.0, 60.0)  # random: device azimuth around the chin (+ = toward +x, the left of the user)
RANDOM_AIM_SD_DEG = 6.0             # random: sd of the aim error (how far off the chin the device points)
RANDOM_PITCH_SD_DEG = 3.0           # random: sd of extra tilt (uneven desk, rubber feet, something underneath)
RANDOM_RISER_P = 0.25               # random: chance the device stands on something (a book, a stand)
RANDOM_RISER_M = (0.03, 0.15)       # random: height of that something
RANDOM_WRONG_P = 0.15               # random: chance of a deliberately wrong placement (see _place_random)
# What still counts as usable (_placement_ok): the upright head centre this far from the sensor and this
# far inside the field of view (horizontal / vertical), and the device the right way up. Else `misplaced`.
# Chosen so that every "arc" placement passes: there the head centre sits 10-17 deg off the axis sideways
# (each sensor is turned 8 deg outward) and up to 25 deg above it (tall person, high seat, device near).
OK_DIST_M = (0.45, 1.30)
OK_MARGIN_H_DEG = 4.0
OK_MARGIN_V_DEG = 0.0

# Scene richness (sample_session(scene=...), train.py --scene):
#   "plain" the desk, keyboard, chair and a bare wall (plus --clutter boxes): the same draws as before
#   "rich"  things on the desk (monitors, a laptop, a cup, books, a lamp, a bag) and around the room (shelves,
#           a partition, a neighbour at the next desk, a window that returns nothing, a plant), each with its own
#           chance, placed clear of the device, the keyboard and the hands (see _furnish)
SCENE_MODES = ("plain", "rich")
VIEW_INTRUDE_M = 0.06              # rich: a desk item may poke at most this far into the bottom of the field of view
L = {name: i for i, name in enumerate(LABELS)}

FLAG_TRANSITION = 1   # frame is inside a transition into this segment
FLAG_NOD = 2          # a nod is in progress (falling or recovering)
FLAG_LOOKALIKE = 4    # a look-alike action (glance down, cup, head turn) is in progress
FLAG_BYSTANDER = 8    # a second person is in the room (walking behind or standing beside)

# Relative frequency and duration range (s) of each segment type
SEGMENTS = {
    "upright": (0.30, (15, 90)),
    "lean_forward": (0.12, (5, 30)),
    "recline": (0.12, (5, 40)),
    "face_down": (0.10, (5, 40)),
    "chin_rest": (0.12, (5, 40)),
    "drowsy": (0.12, (20, 70)),
    "away": (0.08, (3, 30)),
    # Variants of the postures above, labelled as them (SEGMENT_LABEL): more ways of sitting the same way
    "armrest": (0.06, (10, 60)),       # upright, leaning on one armrest
    "look_aside": (0.06, (5, 30)),     # upright, head turned to a second monitor or a colleague for a while
    "elbows": (0.05, (5, 30)),         # lean_forward, propped on both forearms, eyes on the screen
    "stretch": (0.04, (3, 7)),         # recline, a short stretch with the hands behind the head
    "writing": (0.07, (10, 60)),       # lean_forward, studying on paper: head down over the desk, pencil hand moving
}
SEGMENT_LABEL = {"armrest": "upright", "look_aside": "upright", "elbows": "lean_forward", "stretch": "recline",
                 "writing": "lean_forward"}


@dataclass
class SessionConfig:
    room: Room
    person: Person
    sensor_pose: SensorPose
    spec: SensorSpec
    model: SensorModel
    fps: float = 10.0                   # VL53L8CX 8x8 ranging rate used on the device
    seed: int = 0
    meta: dict = field(default_factory=dict)


def _u(rng, a, b):
    return float(rng.uniform(a, b))


def sample_session(rng: np.random.Generator, sensor: str = "vl53l8cx",
                   azimuth_deg: float | None = None, sensor_index: int | None = None,
                   placement: str = "arc", clutter_max: int | None = None, scene: str = "rich") -> SessionConfig:
    """One random desk session. The sensor is one device sensor (`sensor_index`, see
    DEVICE_SENSORS) at one arc position (`azimuth_deg`, in SENSOR_AZIMUTHS_DEG); random when
    not given. cfg.meta["arc"] holds (azimuth_deg, sensor_index, SensorPose) for every sensor of
    every device on the arc, so all views of the same session can be rendered by swapping
    cfg.sensor_pose; cfg.meta["devices"] holds (azimuth_deg, centre, yaw_deg) of each device.

    `placement` (PLACEMENT_MODES) picks how the device is put down. "arc" draws exactly as it
    always did. "random" places one device freely (`azimuth_deg` is ignored), fills meta["arc"]
    and meta["devices"] with that one device, and sets meta["misplaced"] when the spot is one
    the classifier cannot be expected to read: every frame of such a session is then labelled
    `misplaced`, the empty chair included. meta["placement_mode"] records the mode drawn.
    `clutter_max` overrides CLUTTER_MAX (boxes and shelves against the back wall, 0..N per session).
    `scene` (SCENE_MODES): "rich" furnishes the desk and the room (_furnish, meta["scene"]); "plain" does not."""
    if placement not in PLACEMENT_MODES:
        raise ValueError(f"placement must be one of {PLACEMENT_MODES}, not {placement!r}")
    if scene not in SCENE_MODES:
        raise ValueError(f"scene must be one of {SCENE_MODES}, not {scene!r}")
    spec = PRESETS[sensor]
    room = Room(
        desk_height=_u(rng, 0.70, 0.76),
        desk_depth=_u(rng, 0.60, 0.90),
        desk_width=_u(rng, 1.4, 2.0),
        desk_refl=_u(rng, 0.15, 0.7),
        chair_back=rng.random() < 0.9,
        chair_refl=_u(rng, 0.03, 0.5),
        wall_refl=_u(rng, 0.3, 0.85),
        walker_scale=_u(rng, 0.9, 1.1),
        walker_refl=_u(rng, 0.1, 0.7),
    )
    person = Person(
        scale=float(np.clip(rng.normal(1.0, 0.06), 0.85, 1.15)),
        shoulder_half=_u(rng, 0.16, 0.22),
        girth=_u(rng, 1.35, 1.65),
        head_scale=1.5 ** (1 / 3),       # 1.5x the head volume
        reach=_u(rng, 0.68, 0.82),        # typing elbow about 85-110 deg
        hip_z=_u(rng, 0.15, 0.45),
        seat_height=_u(rng, 0.42, 0.55),
        x_offset=float(rng.normal(0.0, 0.06)),
        torso_refl=_u(rng, 0.08, 0.8),
        skin_refl=_u(rng, 0.25, 0.45),
        hair_refl=_u(rng, 0.04, 0.35),
        hair_cover=_u(rng, 0.6, 1.0),
    )
    if sb.available():
        # SMPL-X body: real proportions replace the capsule-body thickness and head tweaks
        person.smplx = sb.sample_shape(rng)
        body = sb.make_cached(person.smplx["gender"], tuple(person.smplx["betas"]), round(person.hair_cover, 3))
        person.scale = body.height / 1.72
        person.shoulder_half = body.sh / person.scale
        person.girth = person.head_scale = person.head_width = 1.0
        person.chin_up = 0.10 * person.scale + float(body.joints[22][2]) - 0.05     # jaw joint, chin a little lower
    room.wall_z = room.desk_depth + person.hip_z + _u(rng, 0.5, 3.2)
    room.chair_z = room.desk_depth + person.hip_z
    n_clutter = CLUTTER_MAX if clutter_max is None else int(clutter_max)
    for _ in range(int(rng.integers(0, n_clutter + 1))):
        # boxes, shelves and the like standing against the back wall; nothing may reach the chair
        half = [_u(rng, 0.1, 0.5), _u(rng, 0.1, 0.8), _u(rng, 0.05, 0.3)]
        z = room.wall_z - half[2] - _u(rng, 0.0, 0.4)
        if z - half[2] < room.chair_z + 0.35:
            continue
        room.clutter.append({
            "center": [_u(rng, -1.2, 1.2), _u(rng, 0.5, 2.0), z],
            "half": half,
            "refl": _u(rng, 0.1, 0.8),
        })

    mode = placement if placement != "mixed" else ("arc" if rng.random() < 0.5 else "random")
    arc, meta = (_place_arc if mode == "arc" else _place_random)(room, person, spec, rng)
    if arc is None:                      # this desk/person admits no placement: draw the whole session again
        return sample_session(rng, sensor, azimuth_deg, sensor_index, placement, clutter_max, scene)
    if mode == "arc":
        a_i = int(rng.integers(len(SENSOR_AZIMUTHS_DEG))) if azimuth_deg is None else SENSOR_AZIMUTHS_DEG.index(int(azimuth_deg))
    else:
        a_i = 0                          # random: the one device
    s_i = int(rng.integers(len(DEVICE_SENSORS))) if sensor_index is None else int(sensor_index)
    meta["azimuth_deg"], meta["sensor_index"], sensor_pose = arc[a_i * len(DEVICE_SENSORS) + s_i]
    meta["placement_mode"] = mode
    meta["clutter"] = len(room.clutter)
    meta["scene_mode"] = scene
    if scene == "rich":
        meta["scene"] = _furnish(room, person, spec, sensor_pose, meta["devices"][a_i][1], rng)
    fd_pitch, fd_shift = _face_down_limit(room, person, meta["devices"][a_i])
    meta["limits"] = {"face_down_pitch": fd_pitch, "face_down_shift": fd_shift}
    # Head as wide as HEAD_COLUMNS zone columns from the arc (every arc sensor is equally far
    # from the head). Hair is the outermost layer, 1.06x the head width (mj_scene.py).
    k, h = person.scale, person.head_scale
    head_c = np.array([person.x_offset, person.seat_height + (0.71 + 0.10 * h) * k,
                       room.desk_depth + person.hip_z - 0.01 * h * k])
    dist = float(np.linalg.norm(head_c - sensor_pose.position))
    cols = _u(rng, *HEAD_COLUMNS)
    # Random placement keeps a real-sized head (HEAD_COLUMNS at HEAD_REF_DIST_M): nearer looks bigger,
    # a cue the classifier should have when the distance is not known in advance.
    half = (dist if mode == "arc" else HEAD_REF_DIST_M) * np.tan(np.radians(cols * spec.fov_h_deg / spec.width / 2))
    if person.smplx is None:                                   # SMPL-X heads keep their real width
        person.head_width = half / (0.075 * h * k * 1.06)
    meta["head_columns"] = cols
    if mode == "random":
        ok, why = _placement_ok(sensor_pose, spec, head_c)
        meta["misplaced"] = not ok
        meta["placement"].update(ok=ok, why=why, head_dist_m=dist)
    model = SensorModel(
        ambient=float(np.exp(rng.uniform(0.0, np.log(3.5)))),
        dropout=_u(rng, 0.002, 0.02),
        separation_m=_u(rng, 0.45, 0.75),
        sigma_rel=_u(rng, 0.006, 0.015),
    )
    return SessionConfig(room, person, sensor_pose, spec, model, meta=meta)


def _chin(room: Room, person: Person) -> np.ndarray:
    """Chin of the upright person (body frame): the point a device is aimed at."""
    k, h = person.scale, person.head_scale
    # body.py upright: head centre (0.71 + 0.10 h) k above the seat, vertical radius 0.11 h k (h = head_scale)
    chin_up = person.chin_up if person.chin_up is not None else (0.71 - 0.01 * h) * k
    return np.array([person.x_offset, person.seat_height + chin_up, room.desk_depth + person.hip_z - 0.01 * h * k])


def _keyboard_box(room: Room, person: Person) -> tuple[np.ndarray, np.ndarray]:
    """Keyboard (centre, half extents) as in mj_scene.py (half 0.22 x 0.012 x 0.07 m), padded a little."""
    return (np.array([person.x_offset, room.desk_height + 0.012, room.desk_depth - 0.22]), np.array([0.23, 0.017, 0.08]))


def _device_axes(psi: float) -> tuple[np.ndarray, np.ndarray]:
    """(forward, side) unit vectors on the desk of a device turned to yaw `psi` deg."""
    pr = np.radians(psi)
    return np.array([np.sin(pr), 0.0, np.cos(pr)]), np.array([np.cos(pr), 0.0, -np.sin(pr)])


def _device_on_desk(c: np.ndarray, psi: float, room: Room, person: Person) -> bool:
    """Does a device centred at `c`, turned to `psi`, stand fully on the desk, clear of the keyboard?"""
    hw, hd = DEVICE_HALF_M
    fwd, side = _device_axes(psi)
    corners = [c + sx * hw * side + sz * hd * fwd for sx in (-1, 1) for sz in (-1, 1)]
    if any(q[2] < 0.02 or q[2] > room.desk_depth - 0.02 or abs(q[0]) > room.desk_width / 2 - 0.02 for q in corners):
        return False
    kb = np.array([person.x_offset, room.desk_depth - 0.22])          # keyboard centre (x, z), mj_scene.py
    ex = hw * abs(side[0]) + hd * abs(fwd[0])                  # footprint half extents on x and z
    ez = hw * abs(side[2]) + hd * abs(fwd[2])
    return not (abs(c[0] - kb[0]) < ex + 0.22 and abs(c[2] - kb[1]) < ez + 0.07)


def _device_poses(c: np.ndarray, psi: float, pitch_err: float, roll: float,
                  riser: float = 0.0, upside_down: bool = False) -> list[SensorPose]:
    """The sensor poses of a device centred at `c`, turned to `psi`, standing `riser` m above the desk."""
    fwd, side = _device_axes(psi)
    return [SensorPose(position=c + ox * side + np.array([0.0, oy + riser, 0.0]) + oz * fwd,
                       pitch_down_deg=-up + pitch_err, yaw_deg=psi + yaw, roll_deg=roll, upside_down=upside_down)
            for (ox, oy, oz), up, yaw in DEVICE_SENSORS]


def _place_arc(room: Room, person: Person, spec: SensorSpec, rng) -> tuple[list | None, dict]:
    """One sensor device at every SENSOR_AZIMUTHS_DEG on an arc around the user, each turned to
    face the user; its sensors sit as in DEVICE_SENSORS. One radius per session; the small
    mounting error (pitch, roll, aim) is drawn once and shared by every device. An arc is drawn
    again unless every device stands fully on the desk, clear of the keyboard, and no sensor
    sees the desk top or the keyboard. Returns (None, {}) when no arc fits this desk and person.
    """
    chin = _chin(room, person)
    kb_box = _keyboard_box(room, person)
    for tries in range(1, 301):
        radius = _u(rng, *SENSOR_DIST_M)
        pitch_err, yaw_err, roll = float(rng.normal(0.0, 1.0)), float(rng.normal(0.0, 2.0)), float(rng.normal(0.0, 1.0))
        arc, devices = [], []
        for az in SENSOR_AZIMUTHS_DEG:
            a = np.radians(az)
            c = np.array([chin[0] + radius * np.sin(a), room.desk_height, chin[2] - radius * np.cos(a)])
            v = chin - c
            psi = float(np.degrees(np.arctan2(v[0], v[2]))) + yaw_err
            if not _device_on_desk(c, psi, room, person):
                break
            poses = _device_poses(c, psi, pitch_err, roll)
            if any(_sees_desk(pose, spec, room, kb_box) for pose in poses):
                break
            arc.extend((float(az), i, pose) for i, pose in enumerate(poses))
            devices.append((float(az), c, psi))
        if len(devices) == len(SENSOR_AZIMUTHS_DEG):
            return arc, {"arc": arc, "devices": devices, "radius_m": radius, "tries": tries}
    return None, {}


def _place_random(room: Room, person: Person, spec: SensorSpec, rng) -> tuple[list | None, dict]:
    """One device wherever a user might actually put it (placement "random"). Distance and
    azimuth are free (RANDOM_DIST_M, RANDOM_AZIMUTH_DEG), the aim at the chin is rough
    (RANDOM_AIM_SD_DEG), the tilt varies (RANDOM_PITCH_SD_DEG), the device may stand on a riser,
    and RANDOM_WRONG_P of the draws are deliberately wrong: upside down, too near, too far,
    pointing past the user, or tilted well off. Only physics is enforced here (the footprint on
    the desk, clear of the keyboard; seeing the desk or the keyboard is allowed). Whether the
    spot is usable is decided afterwards by _placement_ok. Returns (arc, meta) shaped like
    _place_arc, with this one device; meta["placement"] records what was drawn.
    """
    chin = _chin(room, person)
    for tries in range(1, 301):
        radius = _u(rng, *RANDOM_DIST_M)
        az = _u(rng, *RANDOM_AZIMUTH_DEG)
        aim = float(rng.normal(0.0, RANDOM_AIM_SD_DEG))
        pitch_err = float(rng.normal(0.0, RANDOM_PITCH_SD_DEG))
        roll = float(rng.normal(0.0, 2.0))
        riser = _u(rng, *RANDOM_RISER_M) if rng.random() < RANDOM_RISER_P else 0.0
        upside_down, wrong = False, ""
        if rng.random() < RANDOM_WRONG_P:
            wrong = str(rng.choice(["upside_down", "near", "far", "aside", "tilted"]))
            sgn = 1.0 if rng.random() < 0.5 else -1.0
            if wrong == "upside_down":
                upside_down = True
            elif wrong == "near":
                radius = _u(rng, 0.20, 0.45)
            elif wrong == "far":
                radius = _u(rng, 1.20, 1.60)        # only a deep desk and a far-back chair admit this
            elif wrong == "aside":
                aim = sgn * _u(rng, 25.0, 50.0)
            elif wrong == "tilted":
                pitch_err = sgn * _u(rng, 12.0, 30.0)
        a = np.radians(az)
        c = np.array([chin[0] + radius * np.sin(a), room.desk_height, chin[2] - radius * np.cos(a)])
        v = chin - c
        psi = float(np.degrees(np.arctan2(v[0], v[2]))) + aim
        if not _device_on_desk(c, psi, room, person):
            continue
        poses = _device_poses(c, psi, pitch_err, roll, riser, upside_down)
        arc = [(az, i, pose) for i, pose in enumerate(poses)]
        return arc, {"arc": arc, "devices": [(az, c, psi)], "radius_m": radius, "tries": tries,
                     "placement": {"aim_deg": aim, "pitch_err_deg": pitch_err, "roll_deg": roll, "riser_m": riser,
                                   "upside_down": upside_down, "wrong": wrong}}
    return None, {}


def _placement_ok(pose: SensorPose, spec: SensorSpec, head_c: np.ndarray) -> tuple[bool, str]:
    """Can the classifier be expected to read this mounting? The device must be the right way
    up, and the upright head centre must be OK_DIST_M away and OK_MARGIN_H/V_DEG inside the
    field of view. Returns (ok, why), why in "", "upside_down", "near", "far", "aside", "tilted".
    """
    if pose.upside_down or abs((pose.roll_deg + 180.0) % 360.0 - 180.0) > 90.0:
        return False, "upside_down"
    d = head_c - pose.position
    dist = float(np.linalg.norm(d))
    if dist < OK_DIST_M[0]:
        return False, "near"
    if dist > OK_DIST_M[1]:
        return False, "far"
    x, y, z = _rot(pose).T @ d                     # sensor frame: x right, y up, z forward
    if z <= 0.0 or abs(np.degrees(np.arctan2(x, z))) > spec.fov_h_deg / 2 - OK_MARGIN_H_DEG:
        return False, "aside"
    if abs(np.degrees(np.arctan2(y, z))) > spec.fov_v_deg / 2 - OK_MARGIN_V_DEG:
        return False, "tilted"
    return True, ""


def _sees_desk(pose: SensorPose, spec: SensorSpec, room: Room, keyboard=None, n: int = 17) -> bool:
    """True if any beam in the field of view meets the desk top (plus clearance) on the desk,
    or the keyboard box (centre, half extents) when given. Samples an n x n grid over the view."""
    tx = np.tan(np.radians(spec.fov_h_deg / 2))
    ty = np.tan(np.radians(spec.fov_v_deg / 2))
    u = np.linspace(-1.0, 1.0, n)
    gx, gy = np.meshgrid(u * tx, u * ty)
    D = np.stack([gx.ravel(), gy.ravel(), np.ones(n * n)], 1) @ _rot(pose).T
    o = pose.position
    down = D[:, 1] < 0
    t = (room.desk_height + DESK_CLEARANCE_M - o[1]) / np.where(down, D[:, 1], -1.0)
    hit = o + t[:, None] * D
    if np.any(down & (hit[:, 2] >= 0.0) & (hit[:, 2] <= room.desk_depth) & (np.abs(hit[:, 0]) <= room.desk_width / 2)):
        return True
    if keyboard is not None:
        c, h = keyboard
        with np.errstate(divide="ignore", invalid="ignore"):
            t1, t2 = (c - h - o) / D, (c + h - o) / D
        t_in = np.nanmax(np.minimum(t1, t2), 1)
        t_out = np.nanmin(np.maximum(t1, t2), 1)
        if np.any(t_out >= np.maximum(t_in, 0.0)):
            return True
    return False


# ---------------------------------------------------------------------------
# Timeline


@dataclass
class Segment:
    kind: str
    duration: float
    pose: Pose
    nods: list = field(default_factory=list)       # (start_s, fall_s, pause_s, recover_s, amp_rad, hold_s)
    lookalikes: list = field(default_factory=list) # (start_s, kind, length_s, amount)
    nod_side: float = 0.0          # drowsy: 0 = nods forward, +-1 = the head drops to that side (+ = +x)
    leave: tuple | None = None     # away: (side, walk m/s, stand-up s) — gets up and walks out to that side
    ret: tuple | None = None       # away: (side, walk m/s, extra z m) — walks back in from that side, then sits


# Where the hands are in each posture: (mode, weight). People do not keep their
# hands on the keyboard when they lean back or doze, and the classifier must not
# learn "hands on the desk" as part of a posture.
ARMS = {
    "upright": [("typing", 0.40), ("writing", 0.15), ("lap", 0.15), ("crossed", 0.10), ("phone", 0.12), ("hanging", 0.08)],
    "lean_forward": [("typing", 0.50), ("writing", 0.15), ("phone", 0.15), ("crossed", 0.10), ("lap", 0.10)],
    "recline": [("lap", 0.25), ("behind_head", 0.25), ("crossed", 0.20), ("phone", 0.15), ("hanging", 0.10),
                ("typing", 0.05)],
    "face_down": [("pillow", 0.8), ("hanging", 0.2)],
    "chin_rest_other": [("typing", 0.40), ("writing", 0.15), ("lap", 0.25), ("crossed", 0.20)],
    "drowsy": [("lap", 0.30), ("typing", 0.20), ("writing", 0.10), ("crossed", 0.20), ("hanging", 0.15), ("behind_head", 0.05)],
    "armrest": [("lap", 0.30), ("hanging", 0.25), ("crossed", 0.20), ("typing", 0.15), ("phone", 0.10)],
    "look_aside": [("typing", 0.30), ("lap", 0.25), ("crossed", 0.20), ("hanging", 0.15), ("phone", 0.10)],
    "elbows": [("writing", 0.70), ("typing", 0.30)],
    "stretch": [("behind_head", 1.0)],
    "writing": [("writing", 1.0)],
}


def _arms(kind: str, rng) -> str:
    modes, w = zip(*ARMS[kind])
    w = np.array(w) / sum(w)
    return str(rng.choice(modes, p=w))


def _target_pose(kind: str, rng, limits: dict | None = None) -> Pose:
    deg = np.radians
    if kind == "upright":
        return Pose(trunk_pitch=deg(rng.normal(0, 5)), trunk_roll=deg(rng.normal(0, 2)), neck_pitch=deg(rng.normal(6, 6)),
                    head_yaw=deg(rng.normal(0, 6)), arms=_arms("upright", rng))
    if kind == "lean_forward":
        return Pose(trunk_pitch=deg(_u(rng, 14, 36)), neck_pitch=deg(_u(rng, -2, 16)), head_yaw=deg(rng.normal(0, 6)),
                    arms=_arms("lean_forward", rng))
    if kind == "recline":
        return Pose(trunk_pitch=-deg(_u(rng, 14, 42)), neck_pitch=deg(_u(rng, -14, 10)), head_yaw=deg(rng.normal(0, 6)),
                    arms=_arms("recline", rng), hip_shift=_u(rng, 0.0, 0.14))
    if kind == "armrest":
        side = 1.0 if rng.random() < 0.5 else -1.0           # which armrest (+ = +x)
        return Pose(trunk_pitch=deg(rng.normal(0, 4)), trunk_roll=deg(_u(rng, 8, 15)) * side,
                    neck_pitch=deg(rng.normal(6, 5)), head_roll=deg(_u(rng, 0, 6)) * side, arms=_arms("armrest", rng))
    if kind == "look_aside":
        side = 1.0 if rng.random() < 0.5 else -1.0
        return Pose(trunk_pitch=deg(rng.normal(0, 4)), trunk_roll=deg(_u(rng, 0, 5)) * side,
                    neck_pitch=deg(rng.normal(4, 5)), head_yaw=deg(_u(rng, 30, 60)) * side, arms=_arms("look_aside", rng))
    if kind == "elbows":
        return Pose(trunk_pitch=deg(_u(rng, 22, 35)), neck_pitch=deg(_u(rng, -10, 5)), arms=_arms("elbows", rng))
    if kind == "writing":
        return Pose(trunk_pitch=deg(_u(rng, 12, 28)), neck_pitch=deg(_u(rng, 20, 40)), head_yaw=deg(rng.normal(0, 5)),
                    arms="writing")
    if kind == "stretch":
        return Pose(trunk_pitch=-deg(_u(rng, 20, 35)), neck_pitch=-deg(_u(rng, 5, 15)), arms=_arms("stretch", rng),
                    hip_shift=_u(rng, 0.0, 0.08))
    if kind == "face_down":
        hi = max(25.0, min(58.0, (limits or {}).get("face_down_pitch", 58.0)))   # keep the head off the sensor device
        back = (limits or {}).get("face_down_shift", 0.0)              # chair scooted back to clear the device
        return Pose(trunk_pitch=deg(_u(rng, min(35.0, hi - 5.0), hi)), neck_pitch=deg(_u(rng, 30, 60)),
                    arms=_arms("face_down", rng), hip_shift=back - _u(rng, 0.0, 0.08))
    if kind == "chin_rest":
        # chin_right = the arm on the +x side (the person's left) props the chin; chin_both = both hands.
        # The trunk leans toward the propping arm; MuJoCo then bends it further forward as needed
        # so the elbow rests on the desk (mj_scene.MjDesk._settle_chin).
        r = rng.random()
        side = "chin_right" if r < 0.4 else "chin_left" if r < 0.8 else "chin_both"
        sgn = {"chin_right": 1.0, "chin_left": -1.0, "chin_both": 0.0}[side]
        trunk_roll = deg(_u(rng, 6, 16)) * sgn if sgn else deg(float(rng.normal(0.0, 2.0)))
        head_roll = deg(_u(rng, 3, 12)) * sgn if sgn else deg(float(rng.normal(0.0, 3.0)))
        return Pose(trunk_pitch=deg(_u(rng, 6, 22)), trunk_roll=trunk_roll, neck_pitch=deg(_u(rng, 5, 20)),
                    arms=side, head_roll=head_roll, other_arms=_arms("chin_rest_other", rng))
    if kind == "drowsy":
        # drowsy baseline: a drooping head and a slightly slumped trunk, even between nods
        return Pose(trunk_pitch=deg(_u(rng, 0, 14)), neck_pitch=deg(_u(rng, 8, 22)), arms=_arms("drowsy", rng))
    return Pose()


def _plan_nods(duration: float, rng) -> list:
    nods, t = [], _u(rng, 1.0, 8.0)
    while t < duration - 3.0:
        fall = _u(rng, 0.5, 1.5)
        pause = _u(rng, 0.0, 0.4)
        recover = _u(rng, 0.3, 1.0)
        amp = np.radians(_u(rng, 25, 55))
        hold = _u(rng, 4, 20) if rng.random() < 0.45 else 0.0     # micro-sleep with the head down
        nods.append((t, fall, pause, recover, amp, hold))
        t += fall + pause + hold + recover + _u(rng, 3.0, 15.0)
    return nods


def _plan_lookalikes(kind: str, duration: float, rng) -> list:
    if kind in ("away", "face_down", "stretch", "look_aside"):   # too short, or the head is already turned
        return []
    out, t = [], _u(rng, 2.0, 10.0)
    while t < duration - 3.0:
        what = rng.choice(["glance", "cup", "turn"], p=[0.5, 0.2, 0.3])
        length = {"glance": _u(rng, 0.6, 2.5), "cup": _u(rng, 2.0, 5.0), "turn": _u(rng, 1.0, 4.0)}[what]
        amount = {"glance": np.radians(_u(rng, 12, 30)), "cup": 1.0, "turn": np.radians(_u(rng, -40, 40))}[what]
        out.append((t, str(what), length, float(amount)))
        t += length + _u(rng, 6.0, 30.0)
    return out


def _face_down_limit(room: Room, person: Person, device) -> tuple[float, float]:
    """(max trunk pitch deg, extra chair push-back m) that keep the face-down head clear of the
    sensor device. Someone who finds the device in the way scoots the chair back a little before
    lying down; only when even the desk edge leaves no room does the pose get shallower.

    device = (az, centre (x, y, z), yaw_deg) from meta["devices"]. Head position follows body.py
    with the deepest neck flexion; the head is treated as a sphere.
    """
    _, c, psi = device
    k, h = person.scale, person.head_scale
    hw, hd = DEVICE_HALF_M
    pr = np.radians(psi)
    ex, ez = hw * abs(np.cos(pr)) + hd * abs(np.sin(pr)), hw * abs(np.sin(pr)) + hd * abs(np.cos(pr))
    r = 0.12 * h * k * max(1.0, person.head_width)
    if abs(person.x_offset - c[0]) >= ex + r:
        return 58.0, 0.0                                                 # the device is beside the head's path

    def head_z(pitch, shift):
        th, ph = np.radians(pitch), np.radians(60.0)
        return (room.desk_depth + person.hip_z - 0.08 + shift            # chair pulled in by up to 8 cm
                - 0.52 * k * np.sin(th) - (0.09 * k + 0.10 * h * k) * np.sin(th + ph) - 0.01 * h * k * np.cos(th + ph))

    clear = c[2] + ez + 0.02 + r
    need = clear - head_z(58.0, 0.0)
    if need <= 0:
        return 58.0, 0.0
    max_shift = max(0.0, (room.desk_depth - 0.05) - head_z(58.0, 0.0))  # the head must still land on the desk
    if need <= max_shift:
        return 58.0, float(need)
    for pitch in np.arange(57.0, 24.0, -1.0):
        if head_z(pitch, max_shift) > clear:
            return float(pitch), float(max_shift)
    return 25.0, float(max_shift)


def _furnish(room: Room, person: Person, spec: SensorSpec, pose: SensorPose, device_c: np.ndarray, rng) -> dict:
    """Things on the desk and around the room (scene "rich"), as boxes in room.clutter. Desk items
    keep clear of the device footprint, the keyboard and the hands, and may poke no more than
    VIEW_INTRUDE_M into the bottom of the field of view (nobody leaves a laptop lid right in front
    of the device). Room furniture stands beside the desk or against the back wall; the window
    reflects nothing, like glass. Returns {"desk": [kinds], "room": [kinds]} for meta."""
    desk_h, desk_d, half_w = room.desk_height, room.desk_depth, room.desk_width / 2
    x0, chair_z = person.x_offset, room.chair_z
    placed = {"desk": [], "room": []}
    fwd, _side = _device_axes(pose.yaw_deg)
    px, py, pz = pose.position

    def box(kind, center, half, refl, where):
        room.clutter.append({"center": [float(v) for v in center], "half": [float(v) for v in half],
                             "refl": float(refl), "kind": kind})
        placed[where].append(kind)

    def clear(x, z, hx, hz):     # on the desk, off the device, the keyboard and the hands
        if abs(x) + hx > half_w - 0.02 or z - hz < 0.02 or z + hz > desk_d - 0.02:
            return False
        if abs(x - device_c[0]) < hx + DEVICE_HALF_M[0] + 0.03 and abs(z - device_c[2]) < hz + DEVICE_HALF_M[1] + 0.03:
            return False
        if abs(x - x0) < hx + 0.26 and z + hz > desk_d - 0.34 and z - hz < desk_d - 0.10:
            return False
        return not (abs(x - x0) < hx + 0.20 and z + hz > desk_d - 0.10)

    def fits_view(x, z, top):    # `top` = height above the desk; beside the view, or low enough
        vx, vz = x - px, z - pz
        ang = np.degrees(np.arctan2(vx * fwd[2] - vz * fwd[0], vx * fwd[0] + vz * fwd[2]))
        if abs(ang) > spec.fov_h_deg / 2 + 3.0:
            return True
        bottom = py + np.hypot(vx, vz) * np.tan(np.radians(-pose.pitch_down_deg - spec.fov_v_deg / 2))
        return desk_h + top <= bottom + VIEW_INTRUDE_M

    def put(kind, tries, draw):  # draw() -> (x, z, (hx, hy, hz), refl, centre height above the desk)
        for _ in range(tries):
            x, z, (hx, hy, hz), refl, yc = draw()
            if clear(x, z, hx, hz) and fits_view(x, z, yc + hy):
                box(kind, (x, desk_h + yc, z), (hx, hy, hz), refl, "desk")
                return x, z
        return None

    def pick_side():
        return 1.0 if rng.random() < 0.5 else -1.0

    # --- on the desk
    if rng.random() < 0.75:                                   # a monitor on a stand at the far edge
        hy = _u(rng, 0.15, 0.22)
        mon = put("monitor", 6, lambda: (x0 + rng.normal(0.0, 0.08), _u(rng, 0.06, 0.16),
                                          (_u(rng, 0.25, 0.35), hy, 0.015), _u(rng, 0.05, 0.3), 0.10 + hy))
        if mon is not None and rng.random() < 0.3:            # a second one beside it
            s2 = pick_side()
            put("monitor2", 4, lambda: (mon[0] + s2 * _u(rng, 0.55, 0.7), _u(rng, 0.06, 0.2),
                                        (_u(rng, 0.25, 0.32), hy, 0.015), _u(rng, 0.05, 0.3), 0.10 + hy))
    if rng.random() < 0.3:                                    # an open laptop to one side
        s2 = pick_side()
        for _ in range(6):
            x, z = x0 + s2 * _u(rng, 0.45, 0.7), _u(rng, desk_d - 0.45, desk_d - 0.2)
            if clear(x, z, 0.16, 0.11) and fits_view(x, z, 0.02) and fits_view(x, z - 0.105, 0.24):
                box("laptop", (x, desk_h + 0.01, z), (0.16, 0.01, 0.11), _u(rng, 0.1, 0.5), "desk")
                box("laptop_lid", (x, desk_h + 0.13, z - 0.105), (0.16, 0.11, 0.005), _u(rng, 0.05, 0.3), "desk")
                break
    if rng.random() < 0.6:                                    # a cup or a bottle within reach
        s2, tall = pick_side(), rng.random() < 0.4
        hy = _u(rng, 0.11, 0.14) if tall else _u(rng, 0.04, 0.06)
        put("bottle" if tall else "cup", 8, lambda: (x0 + s2 * _u(rng, 0.30, 0.55), _u(rng, desk_d - 0.38, desk_d - 0.10),
                                                     (0.035, hy, 0.035), _u(rng, 0.1, 0.7), hy))
    if rng.random() < 0.5:                                    # a stack of books
        s2, hy = pick_side(), _u(rng, 0.02, 0.08)
        put("books", 8, lambda: (x0 + s2 * _u(rng, 0.35, 0.75), _u(rng, 0.08, desk_d - 0.12),
                                 (_u(rng, 0.08, 0.12), hy, _u(rng, 0.10, 0.14)), _u(rng, 0.2, 0.8), hy))
    if rng.random() < 0.4:                                    # a desk lamp: post and head
        s2 = pick_side()
        for _ in range(6):
            x, z = x0 + s2 * _u(rng, 0.45, 0.8), _u(rng, 0.06, 0.3)
            if clear(x, z, 0.07, 0.11) and fits_view(x, z + 0.06, 0.42):
                box("lamp", (x, desk_h + 0.20, z), (0.02, 0.20, 0.02), 0.3, "desk")
                box("lamp_head", (x, desk_h + 0.40, z + 0.06), (0.07, 0.02, 0.05), 0.3, "desk")
                break
    if rng.random() < 0.2:                                    # a bag dropped on the desk
        s2, hy = pick_side(), _u(rng, 0.10, 0.16)
        put("bag", 6, lambda: (x0 + s2 * _u(rng, 0.5, 0.9), _u(rng, 0.15, desk_d - 0.15),
                               (_u(rng, 0.12, 0.18), hy, _u(rng, 0.06, 0.10)), _u(rng, 0.05, 0.4), hy))

    # --- around the room
    for s2 in (-1.0, 1.0):
        if rng.random() < 0.35:                               # a bookshelf standing beside the desk
            hy = _u(rng, 0.85, 1.05)
            box("shelf", (s2 * (half_w + _u(rng, 0.45, 1.0)), hy, _u(rng, 0.3, max(0.4, min(chair_z + 0.5, room.wall_z - 0.5)))),
                (0.15, hy, _u(rng, 0.35, 0.55)), _u(rng, 0.15, 0.7), "room")
        elif rng.random() < 0.3:                              # a partition panel along the side of the desk
            hy = _u(rng, 0.6, 0.8)
            box("partition", (s2 * (half_w + 0.03), hy, desk_d / 2 + 0.15), (0.02, hy, _u(rng, 0.55, 0.75)),
                _u(rng, 0.2, 0.6), "room")
    if rng.random() < 0.3:                                    # a neighbour at the next desk (desk, torso, head)
        s2 = pick_side()
        nx = s2 * (half_w + _u(rng, 1.0, 1.6))
        box("neighbor_desk", (nx, desk_h - 0.02, desk_d / 2), (0.6, 0.02, desk_d / 2), room.desk_refl, "room")
        seat, nz = _u(rng, 0.42, 0.55), chair_z + _u(rng, -0.15, 0.2)
        box("neighbor_torso", (nx, seat + 0.33, nz), (0.19, 0.30, 0.12), _u(rng, 0.08, 0.7), "room")
        box("neighbor_head", (nx, seat + 0.78, nz - 0.02), (0.08, 0.11, 0.09), 0.3, "room")
    if rng.random() < 0.4:                                    # a bookshelf against the back wall
        hy = _u(rng, 0.9, 1.0)
        box("back_shelf", (_u(rng, -1.5, 1.5), hy, room.wall_z - 0.16), (_u(rng, 0.4, 0.6), hy, 0.15), _u(rng, 0.15, 0.7), "room")
    if rng.random() < 0.35:                                   # a window: glass returns nothing
        box("window", (_u(rng, -1.4, 1.4), _u(rng, 1.3, 1.5), room.wall_z - 0.02),
            (_u(rng, 0.5, 0.8), _u(rng, 0.45, 0.6), 0.01), 0.01, "room")
    if rng.random() < 0.3:                                    # a plant in a corner
        box("plant", (pick_side() * _u(rng, 1.0, 1.6), 0.45, _u(rng, chair_z + 0.4, max(chair_z + 0.5, room.wall_z - 0.3))),
            (0.15, 0.45, 0.15), _u(rng, 0.2, 0.5), "room")
    return placed


def _plan_bystander(cfg: SessionConfig, total_s: float, rng) -> list:
    """Second-person events: someone walks past behind the chair, comes in from the side and stands
    beside the desk (a chat, short or long), or comes up behind the shoulder and looks on for a
    while. Positions are in the body frame (x, z toward the user)."""
    room, p = cfg.room, cfg.person
    chair_z = room.desk_depth + p.hip_z
    out, t = [], _u(rng, 10.0, 120.0)
    while t < total_s - 5.0:
        kinds = ["enter_side", "behind_shoulder"] + (["walk_behind"] if room.wall_z - chair_z >= 0.75 else [])
        kind = str(rng.choice(kinds))
        side, v = (1.0 if rng.random() < 0.5 else -1.0), _u(rng, 0.7, 1.4)
        if kind == "walk_behind":
            ev = {"t0": t, "kind": kind, "side": side, "v": v, "z": _u(rng, chair_z + 0.35, room.wall_z - 0.35), "dur": 3.6 / v}
        else:
            if kind == "enter_side":
                x_stop, hold, z = _u(rng, 0.55, 1.0), _u(rng, 2.0, 60.0), room.desk_depth + _u(rng, 0.1, 0.4)
            else:
                x_stop, hold, z = _u(rng, 0.3, 0.5), _u(rng, 3.0, 20.0), chair_z + _u(rng, 0.35, 0.6)
            ev = {"t0": t, "kind": kind, "side": side, "v": v, "z": z,
                  "x_stop": x_stop, "hold": hold, "dur": 2 * (1.8 - x_stop) / v + hold}
        out.append(ev)
        t += ev["dur"] + _u(rng, 30.0, 240.0)
    return out


def _bystander_pos(events: list, t: float):
    """(x, z) of the bystander at time t, or None when nobody is there."""
    for ev in events:
        x = t - ev["t0"]
        if not 0.0 <= x < ev["dur"]:
            continue
        s, v = ev["side"], ev["v"]
        if ev["kind"] == "walk_behind":
            return (s * (1.8 - v * x), ev["z"])
        t_in = (1.8 - ev["x_stop"]) / v
        if x < t_in:
            px = 1.8 - v * x
        elif x < t_in + ev["hold"]:
            px = ev["x_stop"]
        else:
            px = ev["x_stop"] + v * (x - t_in - ev["hold"])
        return (s * px, ev["z"])
    return None


def _plan_leave(rng) -> tuple:
    return (1.0 if rng.random() < 0.5 else -1.0, _u(rng, 0.7, 1.3), _u(rng, 0.6, 1.2))


def _plan_return(rng) -> tuple:
    return (1.0 if rng.random() < 0.5 else -1.0, _u(rng, 0.7, 1.3), _u(rng, 0.0, 0.25))


def _away_phases(seg: Segment, dur: float, x0: float) -> tuple[float, float, float]:
    """(stand-up s, walk-out s, walk-in s) of an away segment, squeezed so both legs fit in `dur`
    with at least a second of empty chair between them."""
    standup = seg.leave[2] if seg.leave is not None else 0.0
    lw = (WALK_EDGE_M - seg.leave[0] * x0) / seg.leave[1] if seg.leave is not None else 0.0
    rw = (WALK_EDGE_M - seg.ret[0] * x0) / seg.ret[1] if seg.ret is not None else 0.0
    total = standup + lw + rw
    if total > 0.0 and total + 1.0 > dur:
        k = max(0.0, dur - 1.0) / total
        standup, lw, rw = standup * k, lw * k, rw * k
    return standup, lw, rw


def _away_frame(seg: Segment, ts: float, dur: float, start_pose: Pose | None, cfg: SessionConfig):
    """What the sensor sees `ts` s into an away segment of `dur` s: (seated pose or None, the
    person as the walker body (x, z) or None, moving). Leaving: they first rise in the chair
    (chair pushed back, upper body forward), then walk out sideways from where they sat, to
    seg.leave's side. Returning: they
    walk in from seg.ret's side and reach the chair as the segment ends; the next segment sits
    them down. The walker is the same mocap body mj_scene uses for bystanders."""
    p, room = cfg.person, cfg.room
    x0, z0 = p.x_offset, room.desk_depth + max(0.2, p.hip_z - STAND_FRONT_M)
    standup, lw, rw = _away_phases(seg, dur, x0)
    if seg.leave is not None and ts < standup + lw:
        side = seg.leave[0]
        if ts < standup:
            k = ts / standup
            pose = replace(start_pose if start_pose is not None else Pose())
            pose.hip_shift += 0.2 * k                      # pushes the chair back
            pose.trunk_pitch += np.radians(15.0) * k       # leans forward to get up
            return pose, None, True
        k = (ts - standup) / max(lw, 1e-6)
        return None, (x0 + (side * WALK_EDGE_M - x0) * k, z0), True
    if seg.ret is not None and dur - ts < rw:
        side, _, z_extra = seg.ret
        k = (dur - ts) / max(rw, 1e-6)                     # 1 at the room edge, 0 at the chair
        return None, (x0 + (side * WALK_EDGE_M - x0) * k, z0 + z_extra), True
    return None, None, False


def plan_timeline(total_s: float, rng, limits: dict | None = None) -> list[Segment]:
    kinds = list(SEGMENTS)
    probs = np.array([SEGMENTS[k][0] for k in kinds])
    probs /= probs.sum()
    segs = [Segment("away", _u(rng, 3.0, 8.0), Pose(), ret=_plan_return(rng))]   # start empty: the person walks in
    segs.append(Segment("upright", _u(rng, 15.0, 30.0), _target_pose("upright", rng, limits)))
    t = sum(s.duration for s in segs)
    while t < total_s:
        kind = str(rng.choice(kinds, p=probs))
        if kind == segs[-1].kind:
            continue
        lo, hi = SEGMENTS[kind][1]
        dur = _u(rng, lo, hi)
        seg = Segment(kind, dur, _target_pose(kind, rng, limits))
        if kind == "away":
            seg.leave, seg.ret = _plan_leave(rng), _plan_return(rng)
        if kind == "drowsy":
            seg.nods = _plan_nods(dur, rng)
            # most people nod forward; some slump sideways (head and a shoulder drop to one side)
            seg.nod_side = 0.0 if rng.random() < 0.65 else (1.0 if rng.random() < 0.5 else -1.0)
        seg.lookalikes = _plan_lookalikes(kind, dur, rng)
        segs.append(seg)
        # Most non-upright segments return to upright, like a real desk day
        if kind not in ("upright", "away") and rng.random() < 0.6:
            segs.append(Segment("upright", _u(rng, 10, 60), _target_pose("upright", rng, limits)))
        t = sum(s.duration for s in segs)
    return segs


# ---------------------------------------------------------------------------
# Frame generation

_LERP_FIELDS = ("trunk_pitch", "trunk_roll", "neck_pitch", "head_yaw", "head_roll", "hip_shift")


def _lerp_pose(a: Pose, b: Pose, w: float) -> Pose:
    out = replace(b)
    for f in _LERP_FIELDS:
        setattr(out, f, getattr(a, f) * (1 - w) + getattr(b, f) * w)
    out.arms = b.arms if w >= 0.5 else a.arms
    out.other_arms = b.other_arms if w >= 0.5 else a.other_arms
    return out


def _nod_offset(nod, t: float) -> tuple[float, bool]:
    start, fall, pause, recover, amp, hold = nod
    x = t - start
    if x < 0:
        return 0.0, False
    if x < fall:
        return amp * (x / fall), True
    x -= fall
    if x < pause + hold:
        return amp, True
    x -= pause + hold
    if x < recover:
        return amp * (1 - x / recover), True
    return 0.0, False


def generate_frames(cfg: SessionConfig, total_s: float, rng: np.random.Generator,
                    backend: str = "mujoco", desk=None):
    """Yields (t_ms, distance_mm[H,W], status[H,W], label, flags) at cfg.fps.

    backend "mujoco": articulated body + mj_multiRay (mj_scene.py). Pass `desk`
    (an MjDesk built from cfg) to watch the same scene in a viewer.
    backend "numpy": the original primitive ray caster (body.py).
    """
    sensor = Sensor(cfg.spec, cfg.model, rng)
    if backend == "mujoco":
        from .mj_scene import MjDesk, sensor_frame
        if desk is None:
            desk = MjDesk(cfg.room, cfg.person)
        origin, R = sensor_frame(cfg.sensor_pose)

        def measure(pose, walker=None):
            desk.set_walker(walker)
            desk.set_pose(pose)
            return sensor.measure(desk.cast, origin, R)
    elif backend == "numpy":
        def measure(pose, walker=None):
            if pose is None:
                surfaces = build_scene(cfg.room, None, None, seat_person=cfg.person)
            else:
                surfaces = build_scene(cfg.room, cfg.person, pose)
            return sensor.measure_surfaces(surfaces, cfg.sensor_pose)
    else:
        raise ValueError(f"unknown backend {backend!r}")
    segs = plan_timeline(total_s, rng, cfg.meta.get("limits"))
    events = _plan_bystander(cfg, total_s, rng)
    misplaced = bool(cfg.meta.get("misplaced", False))
    dt = 1.0 / cfg.fps
    breath_f = _u(rng, 0.2, 0.33)
    sway_f = _u(rng, 0.03, 0.08)
    prev_pose = Pose()
    t_global = 0.0
    for i, seg in enumerate(segs):
        trans = _u(rng, 0.5, 2.0) if i > 0 else 0.0
        start_pose = prev_pose
        n = int(seg.duration * cfg.fps)
        for j in range(n):
            ts = j * dt
            flags = 0
            label = L[SEGMENT_LABEL.get(seg.kind, seg.kind)]
            # Label what the sensor can observe: before the first nod a drowsy
            # person looks upright, and training on an invisible label is noise.
            if seg.kind == "drowsy" and (not seg.nods or ts < seg.nods[0][0]):
                label = L["upright"]
            # A misplaced device cannot read any posture: ask for a re-adjustment. The whole
            # session, empty chair included, so one session never carries both labels for an
            # empty view.
            if misplaced:
                label = L["misplaced"]
            self_walker = None
            if seg.kind == "away":
                frame_pose, self_walker, moving = _away_frame(seg, ts, n * dt, start_pose, cfg)
                if moving:
                    flags |= FLAG_TRANSITION
            else:
                w = 1.0 if trans <= 0 else min(1.0, ts / trans)
                if w < 1.0:
                    flags |= FLAG_TRANSITION
                pose = _lerp_pose(start_pose if start_pose is not None else seg.pose, seg.pose, w)
                if i > 0 and segs[i - 1].kind == "away":
                    # sitting down: perched forward on the chair edge, then settling into the pose
                    pose.hip_shift = seg.pose.hip_shift + 0.08 * (1 - w)
                    pose.trunk_pitch += np.radians(12.0) * (1 - w)
                # micro motion
                pose.breath = 0.004 * np.sin(2 * np.pi * breath_f * (t_global + ts))
                pose.trunk_roll += np.radians(1.5) * np.sin(2 * np.pi * sway_f * (t_global + ts))
                pose.head_yaw += np.radians(rng.normal(0, 0.6))
                if pose.arms == "typing":
                    pose.arm_jitter = 0.01 * np.sin(2 * np.pi * 3.0 * (t_global + ts) + rng.normal(0, 0.3))
                elif pose.arms == "writing":
                    pose.arm_jitter = 0.015 * np.sin(2 * np.pi * 1.5 * (t_global + ts) + rng.normal(0, 0.3))
                for nod in seg.nods:
                    off, active = _nod_offset(nod, ts)
                    if active:
                        if seg.nod_side:
                            # slumping sideways: the head rolls to that side, a little forward, the shoulder dips
                            pose.head_roll += off * 0.9 * seg.nod_side
                            pose.neck_pitch += off * 0.25
                            pose.trunk_roll += off * 0.3 * seg.nod_side
                        else:
                            pose.neck_pitch += off
                            pose.trunk_pitch += off * 0.25          # the whole upper body sags with the nod
                        flags |= FLAG_NOD
                for start, what, length, amount in seg.lookalikes:
                    if start <= ts < start + length:
                        flags |= FLAG_LOOKALIKE
                        k = np.sin(np.pi * (ts - start) / length)
                        if what == "glance":
                            pose.neck_pitch += amount * min(1.0, 2 * k)
                        elif what == "turn":
                            pose.head_yaw += amount * k
                        elif what == "cup" and pose.arms in ("typing", "lap", "crossed", "phone", "hanging"):
                            pose.other_arms = pose.arms
                            pose.arms = "cup_right"
                frame_pose = pose
                prev_pose = pose
            walker = _bystander_pos(events, t_global + ts)
            if walker is not None:
                flags |= FLAG_BYSTANDER
            if self_walker is not None:          # the user walking out or in: the one walker body is theirs
                walker = self_walker
            dist, status = measure(frame_pose, walker)
            yield int(round((t_global + ts) * 1000)), dist, status, label, flags
        if seg.kind == "away":
            prev_pose = None
        t_global += n * dt
