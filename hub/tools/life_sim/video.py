"""시뮬 영상 — 저장된 replay.jsonl(프레임별 자세·두 센서 격자·판정)로 MuJoCo 3D 장면을 다시 렌더해 mp4 로 묶는다.

    python -m tools.life_sim video runs/lifesim/student_a1_wake_first --out a1.mp4 [--speed 10] [--fps 20]

UBAI 계산 노드에는 EGL 이 없어 3D 렌더가 안 되므로, 실행(UBAI)과 렌더(PC)를 나눈다: 실행 때 `--replay` 로 프레임마다 Pose 와 격자를
남기고, PC 에서 같은 tofsim 코드(TOFSIM_ROOT)·같은 시드로 장면을 다시 만들어 Pose 를 넣고 찍는다. SMPL-X 몸은 TOFSIM_SMPLX_DIR
에 모델 파일이 있을 때만 같다(없으면 캡슐 몸 — 장면 요약에 적힌다).

화면 구성: 왼쪽 MuJoCo 3D(사람·책상·의자), 오른쪽 위 두 센서 8×8 깊이(센서 0 = 사용자 왼쪽), 오른쪽 아래 정답/판정·FSM 상태·
C_fatigue·개입/반응 배너. 글자는 영어(글꼴 문제 없음). 전부 시뮬레이션이라고 화면에 적는다.
"""
from __future__ import annotations

import json
import os
from typing import Any

import numpy as np

STATE_COLORS = {"IDLE": "#999", "START": "#8aa", "FOCUS_PC": "#2a7", "FOCUS_MIXED": "#2a7", "FOCUS_NPC": "#2a7", "FOCUS_BREAK": "#7a4",
                "FATIGUE_SUSPECT": "#d93", "FATIGUE": "#d33", "CAUSE_ANALYSIS": "#d33", "ACTION_POSTURE": "#c2c", "ACTION_ENV": "#36c",
                "ACTION_BREAK": "#c63", "MONITOR": "#a86", "RECOVERY": "#3a9", "ESCALATE": "#b11", "REST": "#55a", "END": "#777"}


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def make_video(run_dir: str, out: str, *, speed: float = 10.0, fps: int = 20, width: int = 1280, height: int = 560,
               tofsim_root: str | None = None, max_seconds: float | None = None, start_s: float = 0.0,
               camera: dict[str, float] | None = None, progress: bool = True) -> dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import imageio
    import mujoco

    from . import tofsim_bridge
    from .scene import build_scene

    if tofsim_root:
        os.environ["TOFSIM_ROOT"] = tofsim_root
    if tofsim_bridge.load() is None:
        raise RuntimeError("tofsim 을 찾지 못했다 (TOFSIM_ROOT)")
    from tofsim.body import Pose

    sc_json = os.path.join(run_dir, "scenario.json")
    with open(sc_json, encoding="utf-8") as fh:
        scn = json.load(fh)
    replay = _load_jsonl(os.path.join(run_dir, "replay.jsonl"))
    events = _load_jsonl(os.path.join(run_dir, "events.jsonl"))
    ticks = _load_jsonl(os.path.join(run_dir, "ticks.jsonl"))
    t0 = float(scn["t0"])
    scene = build_scene(int(scn["seed"]), backend="mujoco", placement=scn["placement"], clutter=int(scn["clutter"]),
                        scene=scn["scene"], require_ok=bool(scn["require_ok_placement"]), l8_params=scn.get("l8_params"))
    m, d = scene._desk.model, scene._desk.data
    renderer = mujoco.Renderer(m, 480, 640)
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cfg_cam = {"distance": 2.4, "azimuth": 35.0, "elevation": -16.0}
    cfg_cam.update(camera or {})
    head = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "head")
    lookat_fixed = None
    sensor_marks = [(np.asarray(o, dtype=float), np.asarray(R, dtype=float)) for (o, R) in scene._frames]

    def add_sensor_markers(mjscene) -> None:
        """센서 두 개(작은 구) + 광축(가는 막대)을 장면에 덧그린다. tofsim 은 기기 몸체를 geom 으로 두지 않는다."""
        for i, (o, R) in enumerate(sensor_marks):
            if mjscene.ngeom + 2 > mjscene.maxgeom:
                return
            g = mjscene.geoms[mjscene.ngeom]
            mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_SPHERE, np.array([0.018, 0, 0]), o, np.eye(3).reshape(-1),
                                np.array([1.0, 0.25, 0.1, 1.0], dtype=np.float32))
            mjscene.ngeom += 1
            fwd = R[:, 2]
            g2 = mjscene.geoms[mjscene.ngeom]
            mujoco.mjv_initGeom(g2, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3), np.eye(3).reshape(-1),
                                np.array([1.0, 0.6, 0.1, 0.35], dtype=np.float32))
            mujoco.mjv_connector(g2, mujoco.mjtGeom.mjGEOM_CAPSULE, 0.004, o, o + 0.9 * fwd)
            mjscene.ngeom += 1

    def frame_pose(rec: dict[str, Any]):
        p = rec.get("pose")
        if p is None:
            return None
        return Pose(**{k: v for k, v in p.items() if k in Pose.__dataclass_fields__})

    # 이벤트 → 초 단위 배너
    banners: list[tuple[float, str, str]] = []
    for e in events:
        if e.get("kind") == "intervention" and not e.get("blocked_by_policy"):
            arm = f" arm={e['arm']}" if e.get("arm") else ""
            banners.append((e["t"], f"INTERVENTION {e['kind']} [{','.join(e.get('channels') or [])}]{arm}", "#d33"))
        elif e.get("kind") == "reaction":
            banners.append((e["t"], f"PERSON {e.get('what')} (after {e.get('source')})", "#2a7"))
        elif e.get("kind") == "feedback":
            banners.append((e["t"], f"CARD {e.get('verdict')} ({e.get('event')})", "#36c"))
        elif e.get("kind") == "command":
            banners.append((e["t"], f"DEVICE {e.get('target_id')}.{e.get('operation')}={e.get('value')} ({e.get('gate')})", "#a60"))
    tick_by_t = {round(tk["t"]): tk for tk in ticks}

    step = max(1, int(round(speed / fps * float(scn.get("fps", 10)))))    # 재생 속도 = 실제 speed 배
    frames = [r for r in replay if r["t"] >= start_s and (max_seconds is None or r["t"] <= start_s + max_seconds)][::step]
    writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=7, macro_block_size=8)
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    gs = fig.add_gridspec(2, 3, width_ratios=[1.55, 0.6, 0.6], height_ratios=[1, 0.55], wspace=0.08, hspace=0.25)
    ax3d = fig.add_subplot(gs[:, 0])
    axg = [fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[0, 2])]
    axt = fig.add_subplot(gs[1, 1:])
    last_tick = None
    n_done = 0
    for rec in frames:
        pose = frame_pose(rec)
        scene._desk.set_walker(tuple(rec["walker"]) if rec.get("walker") else None)
        scene._desk.set_pose(pose)
        mujoco.mj_forward(m, d)
        if lookat_fixed is None:
            lookat_fixed = np.array(d.xpos[head]) if head >= 0 else d.xpos.mean(0)
            lookat_fixed = lookat_fixed + np.array([0.0, 0.0, -0.25])
        cam.lookat[:] = lookat_fixed
        cam.distance, cam.azimuth, cam.elevation = cfg_cam["distance"], cfg_cam["azimuth"], cfg_cam["elevation"]
        renderer.update_scene(d, cam)
        add_sensor_markers(renderer.scene)
        img = renderer.render()
        t_abs = t0 + rec["t"]
        tk = tick_by_t.get(int(round(t_abs)) - int(round(t_abs)) % 10) or last_tick
        last_tick = tk
        state = tk["state"] if tk else "IDLE"
        ax3d.clear(); ax3d.imshow(img); ax3d.set_axis_off()
        ax3d.set_title(f"t = {rec['t'] / 60:6.1f} min   segment: {rec['kind']}   truth: {rec['label']}", fontsize=10, loc="left")
        for i, ax in enumerate(axg):
            ax.clear()
            dist = np.array(rec["dist"][i], dtype=float).reshape(8, 8)
            status = np.array(rec["status"][i]).reshape(8, 8)
            valid = np.isin(status, (5, 6, 9, 10)) & (dist >= 0)
            shown = np.where(valid, dist, np.nan)
            ax.imshow(shown, vmin=300, vmax=2000, cmap="viridis_r")
            ax.set_title(f"sensor {i} ({'user-left' if i == 0 else 'user-right'}) mm", fontsize=8)
            ax.set_xticks([]); ax.set_yticks([])
        axt.clear(); axt.set_axis_off()
        pred = rec.get("pred") or "-"
        conf = rec.get("conf")
        c_fat = tk["c_fatigue"] if tk else 0.0
        c_foc = tk["c_focus"] if tk else 0.0
        lines = [f"model: {pred}" + (f" ({conf:.2f})" if isinstance(conf, (int, float)) else "") +
                 f"    0x03 drowsy flag: {'ON' if rec.get('drowsy_flag') else 'off'}",
                 f"FSM: {state}    C_fatigue {c_fat:.2f}   C_focus {c_foc:.2f}" + (f"   gate {tk.get('gate')}" if tk and tk.get("gate") not in (None, "none") else "")]
        recent = [b for b in banners if 0 <= t_abs - b[0] <= 45.0][-3:]
        axt.text(0.0, 0.95, "\n".join(lines), va="top", ha="left", fontsize=10, family="monospace", transform=axt.transAxes)
        axt.add_patch(plt.Rectangle((0.0, 0.52), 1.0, 0.1, transform=axt.transAxes, color=STATE_COLORS.get(state, "#888"), alpha=0.9))
        axt.text(0.01, 0.57, state, va="center", ha="left", fontsize=11, color="white", weight="bold", transform=axt.transAxes)
        for j, (bt, text, color) in enumerate(recent):
            axt.text(0.0, 0.42 - 0.14 * j, f"+{t_abs - bt:4.0f}s  {text}", va="top", ha="left", fontsize=8.5, color=color,
                     family="monospace", transform=axt.transAxes)
        axt.text(1.0, 0.0, "simulation (not measured)  speed x%d" % int(speed), va="bottom", ha="right", fontsize=7, color="#777",
                 transform=axt.transAxes)
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
        h8, w8 = (buf.shape[0] // 8) * 8, (buf.shape[1] // 8) * 8
        writer.append_data(np.ascontiguousarray(buf[:h8, :w8]))
        n_done += 1
        if progress and n_done % 200 == 0:
            print(f"[video] {n_done}/{len(frames)} frames", flush=True)
    writer.close()
    plt.close(fig)
    return {"out": out, "frames": n_done, "speed": speed, "fps": fps, "scene": scene.meta, "smplx": scene.meta.get("smplx")}
