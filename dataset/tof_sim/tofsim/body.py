"""Parametric seated person, desk and room built from ray-castable primitives.

World frame (metres): X = right as seen from the sensor, Y = up, Z = away
from the monitor toward the user. The desk front edge is at Z = desk_depth,
the floor at Y = 0.

At 8×8 zones over 45° a zone is ~6 cm at 65 cm, so capsules and ellipsoids
are enough; a mesh body would not change what the sensor sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np

from .raycast import Box, Capsule, Ellipsoid, Surface


class Part(IntEnum):
    WALL = 0
    FURNITURE = 1
    DESK = 2
    CHAIR = 3
    TORSO = 10
    NECK = 11
    HEAD = 12
    HAIR = 13
    UPPER_ARM = 14
    FOREARM = 15
    HAND = 16
    LEG = 17


PERSON_PARTS = {Part.TORSO, Part.NECK, Part.HEAD, Part.HAIR, Part.UPPER_ARM, Part.FOREARM, Part.HAND, Part.LEG}


def rot_x(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


@dataclass
class Room:
    desk_height: float = 0.73
    desk_depth: float = 0.70        # monitor/sensor side at Z≈0, user side at Z=desk_depth
    desk_width: float = 1.40
    wall_z: float = 2.8             # wall behind the user
    wall_refl: float = 0.6
    desk_refl: float = 0.4
    chair_back: bool = True
    chair_refl: float = 0.08        # black office chairs are dark at 940 nm
    chair_z: float | None = None    # hip position of the chair; stays put while the seat is empty
    clutter: list = field(default_factory=list)   # extra boxes (shelves, doors …)


@dataclass
class Person:
    scale: float = 1.0              # body size factor
    shoulder_half: float = 0.19
    hip_z: float = 0.30             # hip distance behind the desk front edge
    seat_height: float = 0.47
    x_offset: float = 0.0
    torso_refl: float = 0.5         # clothing
    skin_refl: float = 0.35
    hair_refl: float = 0.12         # black hair is weak at 940 nm
    hair_cover: float = 0.8         # how much of the head top hair covers (0..1)


@dataclass
class Pose:
    trunk_pitch: float = 0.0        # rad, + = toward the desk, - = recline
    trunk_roll: float = 0.0         # rad, + = lean to the person's left (sensor's right)
    neck_pitch: float = 0.0         # rad, + = head flexed forward/down
    head_yaw: float = 0.0
    head_roll: float = 0.0
    breath: float = 0.0             # chest expansion, metres
    # typing | lap | crossed | behind_head | phone | hanging | pillow | chin_right | chin_left | cup_right
    # (behind_head/crossed/phone/hanging are MuJoCo-only; the numpy backend draws them as lap)
    arms: str = "typing"
    other_arms: str = "typing"      # the free arm while one arm props the chin or holds a cup
    arm_jitter: float = 0.0         # small hand motion while typing, metres
    hip_shift: float = 0.0          # sliding the chair forward(+)/back(-), metres


def build_scene(room: Room, person: Person | None, pose: Pose | None, seat_person: Person | None = None) -> list[Surface]:
    """`person`/`pose` = who is sitting (None when away). `seat_person` keeps the
    empty chair at that person's seat height and position while they are away."""
    if person is None and seat_person is not None:
        return _empty_seat(room, seat_person)
    return _scene(room, person, pose)


def _empty_seat(room: Room, p: Person) -> list[Surface]:
    s = _scene(room, None, None)
    if room.chair_back:
        s = [x for x in s if x.part != Part.CHAIR]
        z = room.chair_z if room.chair_z is not None else room.desk_depth + p.hip_z
        s.append(Surface(Box(np.array([p.x_offset, p.seat_height + 0.35, z + 0.13]), np.array([0.22, 0.28, 0.03])),
                         Part.CHAIR, room.chair_refl))
    return s


def _scene(room: Room, person: Person | None, pose: Pose | None) -> list[Surface]:
    s: list[Surface] = []
    # Room
    s.append(Surface(Box(np.array([0.0, 1.5, room.wall_z + 0.05]), np.array([4.0, 2.0, 0.05])), Part.WALL, room.wall_refl))
    s.append(Surface(Box(np.array([0.0, room.desk_height - 0.02, room.desk_depth / 2]),
                         np.array([room.desk_width / 2, 0.02, room.desk_depth / 2])), Part.DESK, room.desk_refl))
    for c in room.clutter:
        s.append(Surface(Box(np.asarray(c["center"]), np.asarray(c["half"])), Part.FURNITURE, c["refl"]))

    base_z = room.chair_z if room.chair_z is not None else room.desk_depth + (person.hip_z if person else 0.30)
    hip_z = base_z + (pose.hip_shift if pose else 0.0)
    if room.chair_back:
        # The chair stays where it was when the seat is empty; it tilts back with a recline.
        back_tilt = pose.trunk_pitch if (pose and pose.trunk_pitch < 0) else 0.0
        R = rot_x(-back_tilt)
        seat_h = person.seat_height if person else 0.47
        x = person.x_offset if person else 0.0
        center = np.array([x, seat_h + 0.35, hip_z + 0.13])
        s.append(Surface(Box(center, np.array([0.22, 0.28, 0.03]), R), Part.CHAIR, room.chair_refl))

    if person is None or pose is None:
        return s
    s.extend(_person_surfaces(room, person, pose, hip_z))
    return s


def _person_surfaces(room: Room, p: Person, q: Pose, hip_z: float) -> list[Surface]:
    k = p.scale
    out: list[Surface] = []
    hip = np.array([p.x_offset, p.seat_height + 0.10 * k, hip_z])

    # Trunk: rotate about the hip. Pitch forward tilts the top toward -Z (the desk).
    R_trunk = rot_z(q.trunk_roll) @ rot_x(-q.trunk_pitch)
    spine_len = 0.52 * k
    up = R_trunk @ np.array([0.0, 1.0, 0.0])
    neck_base = hip + up * spine_len
    chest = hip + up * (spine_len * 0.55)
    torso_r = np.array([p.shoulder_half * k * 0.95, spine_len * 0.55, 0.11 * k + q.breath])
    out.append(Surface(Ellipsoid(chest, torso_r, R_trunk), Part.TORSO, p.torso_refl))

    # Neck and head: neck flexion on top of the trunk angle.
    R_head = R_trunk @ rot_x(-q.neck_pitch) @ rot_y(q.head_yaw) @ rot_z(q.head_roll)
    neck_up = R_head @ np.array([0.0, 1.0, 0.0])
    neck_top = neck_base + neck_up * 0.09 * k
    out.append(Surface(Capsule(neck_base, neck_top, 0.05 * k), Part.NECK, p.skin_refl))
    head_c = neck_top + R_head @ np.array([0.0, 0.10 * k, -0.01 * k])
    head_r = np.array([0.075, 0.11, 0.095]) * k
    out.append(Surface(Ellipsoid(head_c, head_r, R_head), Part.HEAD, p.skin_refl))
    if p.hair_cover > 0:
        # Hair cap: a slightly larger ellipsoid shifted up and back, so it wins
        # the closest-surface test on the crown and back of the head.
        hair_c = head_c + R_head @ np.array([0.0, 0.02 * k, 0.012 * k])
        hair_r = head_r * np.array([1.06, 0.98 * p.hair_cover + 0.2, 1.04])
        out.append(Surface(Ellipsoid(hair_c, hair_r, R_head), Part.HAIR, p.hair_refl))

    # Arms
    side_x = R_trunk @ np.array([1.0, 0.0, 0.0])
    sh_l = neck_base + side_x * (p.shoulder_half * k) - up * 0.03 * k     # sensor's right
    sh_r = neck_base - side_x * (p.shoulder_half * k) - up * 0.03 * k     # sensor's left
    desk_top = room.desk_height + 0.03
    ua, fa = 0.30 * k, 0.27 * k

    def arm(shoulder, elbow, wrist):
        out.append(Surface(Capsule(shoulder, elbow, 0.045 * k), Part.UPPER_ARM, p.torso_refl))
        out.append(Surface(Capsule(elbow, wrist, 0.038 * k), Part.FOREARM, p.skin_refl))
        out.append(Surface(Ellipsoid(wrist + (wrist - elbow) / max(np.linalg.norm(wrist - elbow), 1e-6) * 0.05 * k,
                                     np.array([0.045, 0.02, 0.05]) * k), Part.HAND, p.skin_refl))

    def elbow_down(shoulder, fwd=0.10):
        e = shoulder + np.array([0.0, -ua * 0.95, -fwd * k])
        return e

    jit = q.arm_jitter
    chin = head_c + R_head @ np.array([0.0, -0.10 * k, -0.06 * k])
    for shoulder, sign, name in ((sh_l, 1.0, "left"), (sh_r, -1.0, "right")):
        # "left"/"right" here is the sensor's view: sh_l is at +X.
        mode = q.arms
        if mode in ("chin_right", "chin_left", "cup_right"):
            active = (mode in ("chin_right", "cup_right") and name == "left") or (mode == "chin_left" and name == "right")
            # chin_right = the arm on the sensor's right (+X) props the chin.
            if active and mode != "cup_right":
                elbow = np.array([shoulder[0] * 0.6 + chin[0] * 0.4, desk_top + 0.02, room.desk_depth - 0.12])
                out_w = chin + np.array([0.0, -0.03, 0.02])
                arm(shoulder, elbow, out_w)
                continue
            if active and mode == "cup_right":
                elbow = elbow_down(shoulder, 0.12)
                wrist = head_c + np.array([0.03 * sign, -0.12 * k, -0.12 * k])
                arm(shoulder, elbow, wrist)
                continue
            mode = "typing"
        if mode not in ("typing", "lap", "pillow"):
            mode = "lap"
        if mode == "typing":
            elbow = elbow_down(shoulder, 0.05) + np.array([0.04 * sign, 0.0, 0.0])
            elbow[1] = max(elbow[1], desk_top + 0.04)
            wrist = np.array([elbow[0] - 0.10 * sign, desk_top + 0.03, room.desk_depth - 0.20 + jit])
            arm(shoulder, elbow, wrist)
        elif mode == "lap":
            elbow = elbow_down(shoulder, -0.02)
            wrist = elbow + np.array([-0.08 * sign, -0.05, -0.20 * k])
            arm(shoulder, elbow, wrist)
        elif mode == "pillow":
            # Folded on the desk under the head (face down)
            elbow = np.array([shoulder[0] + 0.12 * sign, desk_top + 0.04, room.desk_depth - 0.10])
            wrist = np.array([-0.05 * sign + p.x_offset, desk_top + 0.05, room.desk_depth - 0.30])
            arm(shoulder, elbow, wrist)
    # Legs: thighs under the desk (mostly hidden, but the sensor may see knees)
    for sign in (1.0, -1.0):
        knee = hip + np.array([0.10 * sign, -0.02, -0.42 * k])
        out.append(Surface(Capsule(hip + np.array([0.09 * sign, 0, 0]), knee, 0.07 * k), Part.LEG, p.torso_refl))
        out.append(Surface(Capsule(knee, knee + np.array([0, -0.45 * k, 0.05]), 0.055 * k), Part.LEG, p.torso_refl))
    return out
