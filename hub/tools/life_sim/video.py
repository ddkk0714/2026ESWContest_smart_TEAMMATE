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


def make_video(run_dir: str, out: str | None, *, speed: float = 10.0, fps: int = 20, width: int = 1280, height: int = 560,
               tofsim_root: str | None = None, max_seconds: float | None = None, start_s: float = 0.0,
               camera: dict[str, float] | None = None, progress: bool = True, live: bool = False, loop: bool = False) -> dict[str, Any]:
    """live=True 면 mp4 대신 창(TkAgg)에 같은 화면을 실시간(speed 배)으로 재생한다. 창을 닫으면 끝."""
    import time

    import matplotlib
    matplotlib.use("TkAgg" if live else "Agg")
    import matplotlib.pyplot as plt
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
    # 자리비움(pose 없음)이면 사람 몸 geom 을 숨긴다 — set_pose(None) 은 센서 광선에서만 몸을 빼고 렌더 장면에는 남겨 둔다
    body_geoms = []
    if head >= 0:
        root = int(m.body_rootid[head])
        body_geoms = [g for g in range(m.ngeom) if int(m.body_rootid[m.geom_bodyid[g]]) == root and root != 0]
    body_alpha0 = {g: float(m.geom_rgba[g][3]) for g in body_geoms}
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
        if (e.get("kind") == "intervention" or "hub_kind" in e) and not e.get("blocked_by_policy"):   # 개입 레코드는 kind 가 개입 종류로 덮여 있다
            arm = f" arm={e['arm']}" if e.get("arm") else ""
            banners.append((e["t"], f"INTERVENTION {e['kind']} [{','.join(e.get('channels') or [])}]{arm}", "#d33"))
        elif e.get("kind") == "reaction":
            banners.append((e["t"], f"PERSON {e.get('what')} (after {e.get('source')})", "#2a7"))
        elif e.get("kind") == "feedback":
            banners.append((e["t"], f"CARD {e.get('verdict')} ({e.get('event')})", "#36c"))
        elif e.get("kind") == "command":
            banners.append((e["t"], f"DEVICE {e.get('target_id')}.{e.get('operation')}={e.get('value')} ({e.get('gate')})", "#a60"))
    tick_by_t = {round(tk["t"]): tk for tk in ticks}
    secs = _load_jsonl(os.path.join(run_dir, "frames.jsonl"))
    dev_by_sec = {int(r["sec"]): r.get("devices") or {} for r in secs}
    row_by_sec = {int(r["sec"]): r for r in secs}
    tts_flash: dict[int, bool] = {}
    prev_tts = 0
    for r in secs:
        cur = int((r.get("devices") or {}).get("tts", 0))
        if cur > prev_tts:
            for k in range(int(r["sec"]), int(r["sec"]) + 4):
                tts_flash[k] = True
        prev_tts = cur
    # 기기 geom 자리(장면 좌표, z-up; 사람은 +y 쪽에 앉고 벽은 y≈2.5): 무드등 = 책상 왼쪽 먼 모서리, 선풍기 = 오른쪽 모서리,
    # 공기청정기 = 바닥 오른쪽, 창문 = 사람 뒤 벽. 전부 표시용 — 센서 광선과는 무관하다(렌더 장면에만 덧그린다).
    desk_z = float(scene.cfg.room.desk_height)
    desk_w = float(scene.cfg.room.desk_width) / 2
    wall_y = float(scene.cfg.room.wall_z)
    dev_pos = {"lamp": np.array([desk_w - 0.12, 0.12, desk_z + 0.22]), "fan": np.array([-(desk_w + 0.30), 0.75, 0.95]),
               "purifier": np.array([-(desk_w + 0.45), 1.6, 0.30]), "window": np.array([1.0, wall_y - 0.06, 1.65]),
               "speaker": np.array([0.0, 0.05, desk_z + 0.52])}
    monitor_gid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "monitor")
    monitor_rgba0 = np.array(m.geom_rgba[monitor_gid]) if monitor_gid >= 0 else None

    def _geom(mjscene, gtype, size, pos, rgba, mat=None):
        if mjscene.ngeom >= mjscene.maxgeom:
            return None
        g = mjscene.geoms[mjscene.ngeom]
        mujoco.mjv_initGeom(g, gtype, np.asarray(size, dtype=float), np.asarray(pos, dtype=float),
                            (np.eye(3) if mat is None else np.asarray(mat)).reshape(-1), np.asarray(rgba, dtype=np.float32))
        mjscene.ngeom += 1
        return g

    def add_device_geoms(mjscene, dev: dict[str, Any], flash: bool) -> None:
        lamp_mode, pct = str(dev.get("lamp", "off")), float(dev.get("lamp_pct", 0) or 0)
        on = lamp_mode != "off" and pct > 0
        colour = {"cool": (0.85, 0.92, 1.0), "warm": (1.0, 0.72, 0.35), "hub": (1.0, 0.95, 0.75)}.get(lamp_mode, (0.5, 0.5, 0.5))
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_CYLINDER, [0.025, 0.11, 0], dev_pos["lamp"] - [0, 0, 0.11], (0.25, 0.25, 0.28, 1))  # 스탠드
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_SPHERE, [0.05 + 0.04 * (pct / 100 if on else 0), 0, 0], dev_pos["lamp"],
              (*colour, 1.0 if on else 0.6))
        if on:    # 빛 번짐
            _geom(mjscene, mujoco.mjtGeom.mjGEOM_SPHERE, [0.10 + 0.10 * pct / 100, 0, 0], dev_pos["lamp"], (*colour, 0.18))
        lvl = int(dev.get("fan", 0) or 0)
        rot = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)         # 축을 y(사람 쪽)로
        fan_c = (0.2, 0.55, 1.0) if lvl > 0 else (0.45, 0.45, 0.5)
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_CYLINDER, [0.13, 0.015, 0], dev_pos["fan"], (*fan_c, 0.95), rot)
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_CYLINDER, [0.02, 0.45, 0], dev_pos["fan"] - [0, 0, 0.47], (0.3, 0.3, 0.33, 1))   # 스탠드
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_CYLINDER, [0.14, 0.01, 0], dev_pos["fan"] - [0, 0, 0.94], (0.3, 0.3, 0.33, 1))
        for k in range(lvl):   # 바람: 레벨만큼 반투명 원판이 사람 쪽으로
            _geom(mjscene, mujoco.mjtGeom.mjGEOM_CYLINDER, [0.13 + 0.06 * (k + 1), 0.004, 0], dev_pos["fan"] + [0, 0.12 * (k + 1), 0],
                  (0.4, 0.7, 1.0, 0.16), rot)
        plv = int(dev.get("purifier", 0) or 0)
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_BOX, [0.14, 0.14, 0.30], dev_pos["purifier"], ((0.75, 0.95, 0.8, 1) if plv > 0 else (0.7, 0.7, 0.72, 1)))
        for k in range(plv):
            _geom(mjscene, mujoco.mjtGeom.mjGEOM_SPHERE, [0.05 + 0.04 * k, 0, 0], dev_pos["purifier"] + [0, 0, 0.38 + 0.1 * k], (0.5, 0.9, 0.7, 0.2))
        win_open = bool(dev.get("window", False))
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_BOX, [0.42, 0.01, 0.32], dev_pos["window"], (0.9, 0.9, 0.95, 1))      # 창틀
        _geom(mjscene, mujoco.mjtGeom.mjGEOM_BOX, [0.38, 0.012, 0.28], dev_pos["window"] - [0, 0.005, 0],
              (0.55, 0.8, 1.0, 0.35) if win_open else (0.35, 0.5, 0.7, 0.9))
        if win_open:   # 열린 창: 사람 쪽으로 기운 창짝 + 바깥빛
            _geom(mjscene, mujoco.mjtGeom.mjGEOM_BOX, [0.19, 0.012, 0.28], dev_pos["window"] + [-0.19, -0.18, 0], (0.6, 0.85, 1.0, 0.5),
                  np.array([[0.8, -0.6, 0], [0.6, 0.8, 0], [0, 0, 1]]))
        if flash:
            _geom(mjscene, mujoco.mjtGeom.mjGEOM_SPHERE, [0.06, 0, 0], dev_pos["speaker"], (1.0, 0.3, 0.3, 0.9))
            _geom(mjscene, mujoco.mjtGeom.mjGEOM_SPHERE, [0.14, 0, 0], dev_pos["speaker"], (1.0, 0.4, 0.3, 0.25))

    step = max(1, int(round(speed / fps * float(scn.get("fps", 10)))))    # 재생 속도 = 실제 speed 배
    frames = [r for r in replay if r["t"] >= start_s and (max_seconds is None or r["t"] <= start_s + max_seconds)][::step]
    writer = None
    if not live:
        import imageio
        writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=7, macro_block_size=8)
    else:
        plt.ion()
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    if live:
        try:
            fig.canvas.manager.set_window_title(f"life_sim — {os.path.basename(os.path.normpath(run_dir))} (simulation)")
        except Exception:  # noqa: BLE001
            pass
        plt.show(block=False)
    gs = fig.add_gridspec(2, 3, width_ratios=[1.55, 0.6, 0.6], height_ratios=[1, 0.55], wspace=0.08, hspace=0.25)
    ax3d = fig.add_subplot(gs[:, 0])
    axg = [fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[0, 2])]
    axt = fig.add_subplot(gs[1, 1:])
    last_tick = None
    n_done = 0
    wall0 = time.time()
    t_first = frames[0]["t"] if frames else 0.0
    queue = list(frames)
    while queue:
        rec = queue.pop(0)
        if live:
            if not plt.fignum_exists(fig.number):
                break
            target = wall0 + (rec["t"] - t_first) / speed
            if time.time() > target + 0.25 and queue:       # 렌더가 늦으면 프레임을 건너뛰어 배속을 지킨다
                continue
        pose = frame_pose(rec)
        scene._desk.set_walker(tuple(rec["walker"]) if rec.get("walker") else None)
        scene._desk.set_pose(pose)
        for g in body_geoms:
            m.geom_rgba[g][3] = body_alpha0[g] if pose is not None else 0.0
        mujoco.mj_forward(m, d)
        if lookat_fixed is None:
            lookat_fixed = np.array(d.xpos[head]) if head >= 0 else d.xpos.mean(0)
            lookat_fixed = lookat_fixed + np.array([0.0, 0.0, -0.25])
        cam.lookat[:] = lookat_fixed
        cam.distance, cam.azimuth, cam.elevation = cfg_cam["distance"], cfg_cam["azimuth"], cfg_cam["elevation"]
        sec_i = int(rec["t"])
        dev = dev_by_sec.get(sec_i) or {}
        card_on = int(dev.get("cards", 0) or 0) > int((dev_by_sec.get(sec_i - 30) or {}).get("cards", 0) or 0)
        if monitor_gid >= 0 and monitor_rgba0 is not None:
            m.geom_rgba[monitor_gid] = [0.95, 0.75, 0.2, 1.0] if card_on else monitor_rgba0
        renderer.update_scene(d, cam)
        add_sensor_markers(renderer.scene)
        add_device_geoms(renderer.scene, dev, bool(tts_flash.get(sec_i)))
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
                 f"   0x03 flag {'ON' if rec.get('drowsy_flag') else 'off'}   C_fat {c_fat:.2f}  C_foc {c_foc:.2f}"]
        row = row_by_sec.get(sec_i) or {}
        env = row.get("env") or {}
        if env:
            yn = lambda v: "-" if v is None else ("Y" if v else "n")  # noqa: E731
            lines.append(f"PM2.5 {env.get('pm25', 0):3.0f}  CO2 {env.get('co2_ppm', 0):4.0f}  {env.get('temp_c', 0):4.1f}C "
                         f"{env.get('humidity_pct', 0):3.0f}%  {env.get('lux', 0):4.0f}lx   present mmW {yn(row.get('mm_present'))} "
                         f"ToF {yn(row.get('tof_present'))}")
        chips = [("FAN %d" % int(dev.get("fan", 0) or 0), "#36c" if int(dev.get("fan", 0) or 0) else "#aaa"),
                 ("LAMP %s" % (dev.get("lamp", "off") if dev.get("lamp", "off") == "off" else f"{dev.get('lamp')} {int(dev.get('lamp_pct', 0) or 0)}%"),
                  "#e90" if dev.get("lamp", "off") != "off" else "#aaa"),
                 ("PUR %d" % int(dev.get("purifier", 0) or 0), "#2a8" if int(dev.get("purifier", 0) or 0) else "#aaa"),
                 ("WIN %s" % ("open" if dev.get("window") else "shut"), "#39c" if dev.get("window") else "#aaa"),
                 ("TTS %d" % int(dev.get("tts", 0) or 0), "#d33" if tts_flash.get(sec_i) else "#aaa"),
                 ("CARD" if card_on else "no card", "#c90" if card_on else "#aaa")]
        x = 0.0
        for txt, col in chips:
            axt.text(x, 0.70, txt, va="center", ha="left", fontsize=7.5, color="white", weight="bold", family="monospace",
                     transform=axt.transAxes, bbox=dict(boxstyle="round,pad=0.22", fc=col, ec="none"))
            x += 0.022 * len(txt) + 0.05
        recent = [b for b in banners if 0 <= t_abs - b[0] <= 45.0][-3:]
        axt.text(0.0, 0.99, "\n".join(lines), va="top", ha="left", fontsize=9, family="monospace", transform=axt.transAxes)
        axt.add_patch(plt.Rectangle((0.0, 0.53), 1.0, 0.1, transform=axt.transAxes, color=STATE_COLORS.get(state, "#888"), alpha=0.9))
        axt.text(0.01, 0.58, state, va="center", ha="left", fontsize=11, color="white", weight="bold", transform=axt.transAxes)
        for j, (bt, text, color) in enumerate(recent):
            axt.text(0.0, 0.45 - 0.14 * j, f"+{t_abs - bt:4.0f}s  {text}", va="top", ha="left", fontsize=8.5, color=color,
                     family="monospace", transform=axt.transAxes)
        axt.text(1.0, 0.0, "simulation (not measured)  speed x%d" % int(speed), va="bottom", ha="right", fontsize=7, color="#777",
                 transform=axt.transAxes)
        n_done += 1
        if live:
            fig.canvas.draw_idle()
            plt.pause(max(0.001, target - time.time()))
            if not queue and loop and plt.fignum_exists(fig.number):
                queue, wall0, last_tick = list(frames), time.time(), None
            continue
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
        h8, w8 = (buf.shape[0] // 8) * 8, (buf.shape[1] // 8) * 8
        writer.append_data(np.ascontiguousarray(buf[:h8, :w8]))
        if progress and n_done % 200 == 0:
            print(f"[video] {n_done}/{len(frames)} frames", flush=True)
    if writer is not None:
        writer.close()
    plt.close(fig)
    return {"out": out, "frames": n_done, "speed": speed, "fps": fps, "scene": scene.meta, "smplx": scene.meta.get("smplx")}
