"""MuJoCo 뷰어로 재생 — replay.jsonl 의 자세를 실제 장면에 넣어 창에서 돌려 본다(마우스로 시점 회전·확대).

    python -m tools.life_sim viewer runs/lifesim/student_a1_wake_first [--speed 4] [--start 600] [--loop]

창 제목에 시각·구간·정답·모델 판정·FSM 상태·최근 개입이 흐른다. 주황 구슬·선 = 두 ToF 센서 위치와 광축. PC 전용(GLFW 창).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def run_viewer(run_dir: str, *, speed: float = 4.0, start_s: float = 0.0, loop: bool = True, tofsim_root: str | None = None) -> None:
    import mujoco
    import mujoco.viewer

    from . import tofsim_bridge
    from .scene import build_scene

    if tofsim_root:
        os.environ["TOFSIM_ROOT"] = tofsim_root
    if tofsim_bridge.load() is None:
        raise RuntimeError("tofsim 을 찾지 못했다 (TOFSIM_ROOT)")
    from tofsim.body import Pose

    with open(os.path.join(run_dir, "scenario.json"), encoding="utf-8") as fh:
        scn = json.load(fh)
    replay = _load_jsonl(os.path.join(run_dir, "replay.jsonl"))
    events = _load_jsonl(os.path.join(run_dir, "events.jsonl"))
    ticks = _load_jsonl(os.path.join(run_dir, "ticks.jsonl"))
    t0 = float(scn["t0"])
    tick_by_t = {int(round(tk["t"])): tk for tk in ticks}
    banners = []
    for e in events:
        if e.get("kind") == "intervention" and not e.get("blocked_by_policy"):
            banners.append((e["t"], f"INTERVENTION {e['kind']} [{','.join(e.get('channels') or [])}]" + (f" arm={e['arm']}" if e.get("arm") else "")))
        elif e.get("kind") == "reaction":
            banners.append((e["t"], f"PERSON {e.get('what')}"))
        elif e.get("kind") == "feedback":
            banners.append((e["t"], f"CARD {e.get('verdict')}"))
        elif e.get("kind") == "command":
            banners.append((e["t"], f"DEVICE {e.get('target_id')}={e.get('value')}"))

    scene = build_scene(int(scn["seed"]), backend="mujoco", placement=scn["placement"], clutter=int(scn["clutter"]),
                        scene=scn["scene"], require_ok=bool(scn["require_ok_placement"]), l8_params=scn.get("l8_params"))
    m, d = scene._desk.model, scene._desk.data
    marks = [(np.asarray(o, dtype=float), np.asarray(R, dtype=float)) for (o, R) in scene._frames]
    fps = float(scn.get("fps", 10))
    frames = [r for r in replay if r["t"] >= start_s]
    if not frames:
        raise RuntimeError("재생할 프레임이 없다")

    def pose_of(rec):
        p = rec.get("pose")
        return None if p is None else Pose(**{k: v for k, v in p.items() if k in Pose.__dataclass_fields__})

    head = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "head")
    with mujoco.viewer.launch_passive(m, d, show_left_ui=False, show_right_ui=False) as v:
        scene._desk.set_pose(pose_of(frames[0]))
        mujoco.mj_forward(m, d)
        look = np.array(d.xpos[head]) if head >= 0 else d.xpos.mean(0)
        v.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        v.cam.lookat[:] = look + np.array([0.0, 0.0, -0.25])
        v.cam.distance, v.cam.azimuth, v.cam.elevation = 2.4, 35.0, -16.0
        v.user_scn.ngeom = 0
        for (o, R) in marks:
            g = v.user_scn.geoms[v.user_scn.ngeom]
            mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_SPHERE, np.array([0.018, 0, 0]), o, np.eye(3).reshape(-1),
                                np.array([1.0, 0.25, 0.1, 1.0], dtype=np.float32))
            v.user_scn.ngeom += 1
            g2 = v.user_scn.geoms[v.user_scn.ngeom]
            mujoco.mjv_initGeom(g2, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3), np.eye(3).reshape(-1),
                                np.array([1.0, 0.6, 0.1, 0.35], dtype=np.float32))
            mujoco.mjv_connector(g2, mujoco.mjtGeom.mjGEOM_CAPSULE, 0.004, o, o + 0.9 * R[:, 2])
            v.user_scn.ngeom += 1
        while v.is_running():
            wall0 = time.time()
            for rec in frames:
                if not v.is_running():
                    break
                target = wall0 + (rec["t"] - frames[0]["t"]) / speed
                while time.time() < target and v.is_running():
                    time.sleep(0.002)
                scene._desk.set_walker(tuple(rec["walker"]) if rec.get("walker") else None)
                scene._desk.set_pose(pose_of(rec))
                mujoco.mj_forward(m, d)
                t_abs = t0 + rec["t"]
                tk = tick_by_t.get(int(round(t_abs)) - int(round(t_abs)) % 10)
                state = tk["state"] if tk else "IDLE"
                recent = [b for b in banners if 0 <= t_abs - b[0] <= 45.0][-1:]
                title = (f"t={rec['t'] / 60:5.1f} min | {rec['kind']} / truth {rec['label']} | model {rec.get('pred')} | "
                         f"FSM {state} C_fat {tk['c_fatigue']:.2f}" if tk else
                         f"t={rec['t'] / 60:5.1f} min | {rec['kind']} / truth {rec['label']} | model {rec.get('pred')} | FSM {state}")
                if recent:
                    title += f" | {recent[0][1]}"
                try:
                    import glfw  # noqa: F401
                    v._set_title(title) if hasattr(v, "_set_title") else None
                except Exception:  # noqa: BLE001
                    pass
                print("\r" + title[:150].ljust(150), end="", flush=True)
                v.sync()
            if not loop:
                break
        print()
