"""Open the seated person in the MuJoCo viewer in a pose you choose (no playback).

    python pose_viewer.py --arms typing
    python pose_viewer.py --trunk-pitch 20 --neck-pitch 30 --arms chin_right --other-arms lap
    python pose_viewer.py --trunk-pitch -25 --arms behind_head          # reclined
    python pose_viewer.py --seed 12 --gender female --arms writing
    python pose_viewer.py --dump person.xml                            # also save the scene as MJCF

Angles are degrees. trunk-pitch: + leans toward the desk, - reclines. neck-pitch: + head down.
trunk-roll / head-roll: + to the person's left. head-yaw: + turns to the left.
arms: typing | writing | lap | crossed | behind_head | phone | hanging | pillow | chin_right | chin_left | chin_both | cup_right
other-arms: the free arm while one arm props the chin / holds the cup.
While the window is open, type a new pose in the terminal, e.g.  trunk_pitch=15 arms=lap  and press Enter.
"""
import argparse
import threading
import time

import mujoco
import mujoco.viewer
import numpy as np

from tofsim import sample_session
from tofsim.body import Pose
from tofsim.mj_scene import MjDesk

FIELDS = ("trunk_pitch", "trunk_roll", "neck_pitch", "head_yaw", "head_roll", "hip_shift")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--gender", choices=["neutral", "male", "female"], default=None)
    ap.add_argument("--trunk-pitch", type=float, default=0.0)
    ap.add_argument("--trunk-roll", type=float, default=0.0)
    ap.add_argument("--neck-pitch", type=float, default=0.0)
    ap.add_argument("--head-yaw", type=float, default=0.0)
    ap.add_argument("--head-roll", type=float, default=0.0)
    ap.add_argument("--hip-shift", type=float, default=0.0, help="metres, + moves the chair back")
    ap.add_argument("--arms", default="typing")
    ap.add_argument("--other-arms", default="typing")
    ap.add_argument("--dump", default=None, help="write the scene MJCF to this file")
    ap.add_argument("--tour", type=float, default=0.0, help="cycle through a set of poses, this many seconds each")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    cfg = sample_session(rng)
    if args.gender and cfg.person.smplx:
        cfg.person.smplx["gender"] = args.gender
    desk = MjDesk(cfg.room, cfg.person)
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fp:
            fp.write(desk.xml)
        print("wrote", args.dump)

    state = {"pose": Pose(trunk_pitch=np.radians(args.trunk_pitch), trunk_roll=np.radians(args.trunk_roll),
                          neck_pitch=np.radians(args.neck_pitch), head_yaw=np.radians(args.head_yaw),
                          head_roll=np.radians(args.head_roll), hip_shift=args.hip_shift,
                          arms=args.arms, other_arms=args.other_arms), "dirty": True}

    def reader():
        print("pose> ", end="", flush=True)
        for line in iter(input, ""):
            p = state["pose"]
            for tok in line.split():
                if "=" not in tok:
                    continue
                k, v = tok.split("=", 1)
                if k in FIELDS:
                    setattr(p, k, float(v) if k == "hip_shift" else np.radians(float(v)))
                elif k in ("arms", "other_arms"):
                    setattr(p, k, v)
                else:
                    print("unknown:", k)
            state["dirty"] = True
            print("pose> ", end="", flush=True)

    TOUR = [
        ("바른자세 · 타자", Pose(arms="typing")),
        ("바른자세 · 필기", Pose(arms="writing")),
        ("바른자세 · 무릎 위", Pose(arms="lap")),
        ("바른자세 · 팔짱", Pose(arms="crossed")),
        ("상체 앞으로", Pose(trunk_pitch=np.radians(22), neck_pitch=np.radians(8), arms="typing")),
        ("뒤로 젖힘 · 머리 뒤 깍지", Pose(trunk_pitch=np.radians(-25), arms="behind_head")),
        ("턱괴기 (오른손)", Pose(trunk_pitch=np.radians(12), trunk_roll=np.radians(-10), neck_pitch=np.radians(12),
                             head_roll=np.radians(-8), arms="chin_left", other_arms="typing")),
        ("턱괴기 (두 손)", Pose(trunk_pitch=np.radians(15), neck_pitch=np.radians(10), arms="chin_both")),
        ("꾸벅 졸음 (고개 숙임)", Pose(trunk_pitch=np.radians(5), neck_pitch=np.radians(35), arms="lap")),
        ("엎드림", Pose(trunk_pitch=np.radians(50), neck_pitch=np.radians(50), arms="pillow", hip_shift=-0.05)),
    ]
    if args.tour <= 0:
        threading.Thread(target=reader, daemon=True).start()
    with mujoco.viewer.launch_passive(desk.model, desk.data) as v:
        p = cfg.person
        v.cam.lookat[:] = [p.x_offset, cfg.room.desk_depth + p.hip_z - 0.2, p.seat_height + 0.45]
        v.cam.azimuth, v.cam.elevation, v.cam.distance = 150.0, -15.0, 2.2
        t0, idx = time.time(), -1
        while v.is_running():
            if args.tour > 0:
                i = int((time.time() - t0) // args.tour) % len(TOUR)
                if i != idx:
                    idx = i
                    state["pose"], state["dirty"] = TOUR[i][1], True
                    print(f"[{i + 1}/{len(TOUR)}] {TOUR[i][0]}", flush=True)
            if state["dirty"]:
                with v.lock():
                    desk.set_pose(state["pose"])
                state["dirty"] = False
            v.sync()
            time.sleep(0.03)


if __name__ == "__main__":
    main()
