"""Randomized desk sessions: who sits where, what they do, frame by frame.

A session fixes the room, the person, the sensor mounting and the sensor
model, then plays a timeline of posture segments at the sensor frame rate.
Every frame carries the ground-truth label, so nothing has to be annotated.

Labels follow the on-board estimator (firmware TofPosture.h) minus `unknown`,
which only exists because the estimator has to learn a baseline first:

    0 upright · 1 lean_forward · 2 recline · 3 face_down · 4 chin_rest · 5 drowsy · 6 away

Each segment also includes the look-alikes that a real desk throws at the
classifier, labelled as what they are: glancing down at the keyboard,
drinking from a cup, turning the head, typing jitter, breathing, swaying.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from .body import Person, Pose, Room, build_scene
from .sensor import PRESETS, Sensor, SensorModel, SensorPose, SensorSpec

LABELS = ["upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away"]
L = {name: i for i, name in enumerate(LABELS)}

FLAG_TRANSITION = 1   # frame is inside a transition into this segment
FLAG_NOD = 2          # a nod is in progress (falling or recovering)
FLAG_LOOKALIKE = 4    # a look-alike action (glance down, cup, head turn) is in progress

# Relative frequency and duration range (s) of each segment type
SEGMENTS = {
    "upright": (0.34, (15, 90)),
    "lean_forward": (0.12, (5, 30)),
    "recline": (0.12, (5, 40)),
    "face_down": (0.10, (5, 40)),
    "chin_rest": (0.12, (5, 40)),
    "drowsy": (0.12, (20, 70)),
    "away": (0.08, (3, 30)),
}


@dataclass
class SessionConfig:
    room: Room
    person: Person
    sensor_pose: SensorPose
    spec: SensorSpec
    model: SensorModel
    fps: float = 15.0
    seed: int = 0
    meta: dict = field(default_factory=dict)


def _u(rng, a, b):
    return float(rng.uniform(a, b))


def sample_session(rng: np.random.Generator, sensor: str = "vl53l8cx") -> SessionConfig:
    spec = PRESETS[sensor]
    room = Room(
        desk_height=_u(rng, 0.70, 0.76),
        desk_depth=_u(rng, 0.55, 0.80),
        desk_refl=_u(rng, 0.15, 0.7),
        chair_back=rng.random() < 0.9,
        chair_refl=_u(rng, 0.03, 0.5),
        wall_refl=_u(rng, 0.3, 0.85),
    )
    person = Person(
        scale=float(np.clip(rng.normal(1.0, 0.06), 0.85, 1.15)),
        shoulder_half=_u(rng, 0.16, 0.22),
        hip_z=_u(rng, 0.15, 0.45),
        seat_height=_u(rng, 0.42, 0.55),
        x_offset=float(rng.normal(0.0, 0.06)),
        torso_refl=_u(rng, 0.08, 0.8),
        skin_refl=_u(rng, 0.25, 0.45),
        hair_refl=_u(rng, 0.04, 0.35),
        hair_cover=_u(rng, 0.3, 1.0),
    )
    room.wall_z = room.desk_depth + person.hip_z + _u(rng, 0.5, 3.2)
    room.chair_z = room.desk_depth + person.hip_z
    for _ in range(int(rng.integers(0, 4))):
        room.clutter.append({
            "center": [_u(rng, -1.2, 1.2), _u(rng, 0.5, 2.0), room.wall_z - _u(rng, 0.1, 0.8)],
            "half": [_u(rng, 0.1, 0.5), _u(rng, 0.1, 0.8), _u(rng, 0.05, 0.3)],
            "refl": _u(rng, 0.1, 0.8),
        })

    # Sensor on the monitor top, aimed between head and chest with some slop
    pos = np.array([float(rng.normal(0.0, 0.05)), room.desk_height + _u(rng, 0.28, 0.52), _u(rng, 0.05, 0.25)])
    head_y = person.seat_height + 0.10 + 0.62 * person.scale
    target = np.array([person.x_offset, head_y - _u(rng, 0.05, 0.30), room.desk_depth + person.hip_z])
    v = target - pos
    pitch = float(np.degrees(np.arctan2(-v[1], v[2])))
    sensor_pose = SensorPose(
        position=pos,
        pitch_down_deg=pitch + float(rng.normal(0.0, 4.0)),
        yaw_deg=float(rng.normal(0.0, 4.0)),
        roll_deg=float(rng.normal(0.0, 2.5)),
    )
    model = SensorModel(
        ambient=float(np.exp(rng.uniform(0.0, np.log(3.5)))),
        dropout=_u(rng, 0.002, 0.02),
        separation_m=_u(rng, 0.45, 0.75),
        sigma_rel=_u(rng, 0.006, 0.015),
    )
    return SessionConfig(room, person, sensor_pose, spec, model)


# ---------------------------------------------------------------------------
# Timeline


@dataclass
class Segment:
    kind: str
    duration: float
    pose: Pose
    nods: list = field(default_factory=list)       # (start_s, fall_s, pause_s, recover_s, amp_rad, hold_s)
    lookalikes: list = field(default_factory=list) # (start_s, kind, length_s, amount)


def _target_pose(kind: str, rng) -> Pose:
    deg = np.radians
    if kind == "upright":
        return Pose(trunk_pitch=deg(rng.normal(0, 4)), neck_pitch=deg(rng.normal(6, 5)),
                    arms="typing" if rng.random() < 0.7 else "lap")
    if kind == "lean_forward":
        return Pose(trunk_pitch=deg(_u(rng, 15, 32)), neck_pitch=deg(_u(rng, 0, 15)), arms="typing")
    if kind == "recline":
        return Pose(trunk_pitch=-deg(_u(rng, 15, 38)), neck_pitch=deg(_u(rng, -12, 10)),
                    arms="lap" if rng.random() < 0.6 else "typing", hip_shift=_u(rng, 0.0, 0.12))
    if kind == "face_down":
        return Pose(trunk_pitch=deg(_u(rng, 35, 58)), neck_pitch=deg(_u(rng, 30, 60)), arms="pillow",
                    hip_shift=-_u(rng, 0.0, 0.08))
    if kind == "chin_rest":
        side = "chin_right" if rng.random() < 0.5 else "chin_left"
        roll = deg(_u(rng, 3, 12)) * (1 if side == "chin_right" else -1)
        return Pose(trunk_pitch=deg(_u(rng, 4, 16)), neck_pitch=deg(_u(rng, 5, 20)), arms=side, head_roll=roll)
    if kind == "drowsy":
        return Pose(trunk_pitch=deg(_u(rng, -6, 10)), neck_pitch=deg(_u(rng, 4, 15)),
                    arms="lap" if rng.random() < 0.5 else "typing")
    return Pose()


def _plan_nods(duration: float, rng) -> list:
    nods, t = [], _u(rng, 1.0, 8.0)
    while t < duration - 3.0:
        fall = _u(rng, 0.5, 1.5)
        pause = _u(rng, 0.0, 0.4)
        recover = _u(rng, 0.3, 1.0)
        amp = np.radians(_u(rng, 15, 42))
        hold = _u(rng, 5, 15) if rng.random() < 0.25 else 0.0     # micro-sleep with the head down
        nods.append((t, fall, pause, recover, amp, hold))
        t += fall + pause + hold + recover + _u(rng, 4.0, 25.0)
    return nods


def _plan_lookalikes(kind: str, duration: float, rng) -> list:
    if kind in ("away", "face_down"):
        return []
    out, t = [], _u(rng, 2.0, 10.0)
    while t < duration - 3.0:
        what = rng.choice(["glance", "cup", "turn"], p=[0.5, 0.2, 0.3])
        length = {"glance": _u(rng, 0.6, 2.5), "cup": _u(rng, 2.0, 5.0), "turn": _u(rng, 1.0, 4.0)}[what]
        amount = {"glance": np.radians(_u(rng, 12, 30)), "cup": 1.0, "turn": np.radians(_u(rng, -40, 40))}[what]
        out.append((t, str(what), length, float(amount)))
        t += length + _u(rng, 6.0, 30.0)
    return out


def plan_timeline(total_s: float, rng) -> list[Segment]:
    kinds = list(SEGMENTS)
    probs = np.array([SEGMENTS[k][0] for k in kinds])
    probs /= probs.sum()
    segs = [Segment("away", _u(rng, 3.0, 8.0), Pose())]   # start empty so a baseline/background can form
    segs.append(Segment("upright", _u(rng, 15.0, 30.0), _target_pose("upright", rng)))
    t = sum(s.duration for s in segs)
    while t < total_s:
        kind = str(rng.choice(kinds, p=probs))
        if kind == segs[-1].kind:
            continue
        lo, hi = SEGMENTS[kind][1]
        dur = _u(rng, lo, hi)
        seg = Segment(kind, dur, _target_pose(kind, rng))
        if kind == "drowsy":
            seg.nods = _plan_nods(dur, rng)
        seg.lookalikes = _plan_lookalikes(kind, dur, rng)
        segs.append(seg)
        # Most non-upright segments return to upright, like a real desk day
        if kind not in ("upright", "away") and rng.random() < 0.6:
            segs.append(Segment("upright", _u(rng, 10, 60), _target_pose("upright", rng)))
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


def generate_frames(cfg: SessionConfig, total_s: float, rng: np.random.Generator):
    """Yields (t_ms, distance_mm[H,W], status[H,W], label, flags) at cfg.fps."""
    sensor = Sensor(cfg.spec, cfg.model, rng)
    segs = plan_timeline(total_s, rng)
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
            label = L[seg.kind]
            # Label what the sensor can observe: before the first nod a drowsy
            # person looks upright, and training on an invisible label is noise.
            if seg.kind == "drowsy" and (not seg.nods or ts < seg.nods[0][0]):
                label = L["upright"]
            if seg.kind == "away":
                surfaces = build_scene(cfg.room, None, None, seat_person=cfg.person)
            else:
                w = 1.0 if trans <= 0 else min(1.0, ts / trans)
                if w < 1.0:
                    flags |= FLAG_TRANSITION
                pose = _lerp_pose(start_pose if start_pose is not None else seg.pose, seg.pose, w)
                if i > 0 and segs[i - 1].kind == "away":
                    pose.hip_shift = seg.pose.hip_shift + 0.6 * (1 - w)   # walking in from behind
                # micro motion
                pose.breath = 0.004 * np.sin(2 * np.pi * breath_f * (t_global + ts))
                pose.trunk_roll += np.radians(1.5) * np.sin(2 * np.pi * sway_f * (t_global + ts))
                pose.head_yaw += np.radians(rng.normal(0, 0.6))
                if pose.arms == "typing":
                    pose.arm_jitter = 0.01 * np.sin(2 * np.pi * 3.0 * (t_global + ts) + rng.normal(0, 0.3))
                for nod in seg.nods:
                    off, active = _nod_offset(nod, ts)
                    if active:
                        pose.neck_pitch += off
                        flags |= FLAG_NOD
                for start, what, length, amount in seg.lookalikes:
                    if start <= ts < start + length:
                        flags |= FLAG_LOOKALIKE
                        k = np.sin(np.pi * (ts - start) / length)
                        if what == "glance":
                            pose.neck_pitch += amount * min(1.0, 2 * k)
                        elif what == "turn":
                            pose.head_yaw += amount * k
                        elif what == "cup" and pose.arms in ("typing", "lap"):
                            pose.arms = "cup_right"
                surfaces = build_scene(cfg.room, cfg.person, pose)
                prev_pose = pose
            dist, status = sensor.measure(surfaces, cfg.sensor_pose)
            yield int(round((t_global + ts) * 1000)), dist, status, label, flags
        if seg.kind == "away":
            prev_pose = None
        t_global += n * dt
