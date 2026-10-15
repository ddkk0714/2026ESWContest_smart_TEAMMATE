"""시연용 영상: 자세를 바꿔 가는 사람 + 가상 L8 8×8 + 정답·규칙·AI 판정.

  python animate.py --out demo.mp4
  python animate.py --out demo.mp4 --model models/mlp_sim60.npz --pitch 15 --seed 5

규칙은 펌웨어 TofPosture.cpp 재판정(g++/WSL 필요, 없으면 생략), AI 는 train_mlp.py --save 로
저장한 모델(없으면 생략)이다. 영상 속 사람은 학습에 쓴 세션과 다른 시드로 새로 만든다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tof_logger"))

from tofsim.l8 import L8, Mount  # noqa: E402
from tofsim.motion import StepSpec, timeline  # noqa: E402
from tofsim.scene import Room, build_scene  # noqa: E402

KO = {"upright": "바른 자세", "lean_forward": "상체 앞으로", "forward_head": "거북목",
      "head_down": "고개 숙임", "recline": "뒤로 젖힘", "face_down": "엎드림", "chin_rest": "턱괴기",
      "lean_left": "왼쪽 기울임", "lean_right": "오른쪽 기울임", "drowsy": "꾸벅 졸음", "away": "자리비움",
      "unknown": "판단 중", None: "(자세 바꾸는 중)"}
DEMO = [StepSpec("away", 3), StepSpec("upright", 11), StepSpec("lean_forward", 4), StepSpec("upright", 3),
        StepSpec("recline", 4), StepSpec("upright", 3), StepSpec("face_down", 4), StepSpec("upright", 3),
        StepSpec("chin_rest", 4, "left"), StepSpec("upright", 3), StepSpec("lean_left", 4),
        StepSpec("upright", 3), StepSpec("head_down", 4), StepSpec("upright", 3), StepSpec("drowsy", 12)]
CMDS = {0: "g", 1: "b"}


def font(size: int, bold: bool = False):
    for name in (("malgunbd.ttf" if bold else "malgun.ttf"), "NotoSansCJK-Regular.ttc", "AppleGothic.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def heat(d: np.ndarray, size: int) -> np.ndarray:
    v = np.clip((d.astype(float) - 200) / 1600, 0, 1)
    rgb = np.stack([1 - 0.3 * v, 1 - 0.8 * v, 0.25 + 0.5 * v], axis=-1)
    rgb[d < 0] = 0
    cell = size // d.shape[0]
    img = (np.kron(rgb, np.ones((cell, cell, 1))) * 255).astype(np.uint8)
    img[::cell, :] = img[::cell, :] // 2  # 칸 경계
    img[:, ::cell] = img[:, ::cell] // 2
    return img


def ai_predict(model_path: Path, d: np.ndarray, st: np.ndarray, frames) -> np.ndarray | None:
    if not model_path.exists():
        return None
    from tofsim_train import features, smooth  # noqa: E402  (아래 main 에서 등록)

    z = np.load(model_path)
    classes = [str(c) for c in z["classes"]]
    dist = np.where(np.isin(st, (5, 6, 9, 10)) & (d >= 0), d.astype(np.float32), np.nan)
    calib = np.array([f.step == 1 and f.phase == "step" for f in frames])
    calib &= np.cumsum(calib) > 15  # 기준 자세 단계 첫 1 s 제외
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            base = np.nanmedian(dist[calib], axis=0)
    X = (features(dist, base) - z["mu"]) / z["sd"]
    h = np.maximum(X @ z["W1"] + z["b1"], 0)
    pred = smooth((h @ z["W2"] + z["b2"]).argmax(axis=1))
    out = np.array([classes[p] for p in pred], dtype=object)
    out[~calib.cumsum().astype(bool)] = "unknown"  # 기준선을 잡기 전에는 판단 안 함
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("demo.mp4"))
    ap.add_argument("--model", type=Path, default=HERE / "models" / "mlp_sim60.npz")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--dist", type=float, default=0.62)
    ap.add_argument("--mount-z", type=float, default=0.15)
    ap.add_argument("--pitch", type=float, default=10.0)
    ap.add_argument("--fps", type=float, default=7.5, help="영상 프레임률(센서는 15 Hz)")
    a = ap.parse_args()

    import importlib.util

    spec = importlib.util.spec_from_file_location("tofsim_train", HERE / "train_mlp.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tofsim_train"] = mod
    spec.loader.exec_module(mod)

    rng = np.random.default_rng(a.seed)
    room = Room(dist=a.dist, desk_edge=a.dist - 0.22)
    sc = build_scene(r=room)
    l8 = L8(Mount(z=room.desk_h + a.mount_z, pitch=a.pitch))
    frames = timeline(sc, DEMO, 1.5, rng, fps=15)
    n = len(frames)
    d = np.zeros((n, 8, 8), np.int16)
    st = np.zeros((n, 8, 8), np.uint8)
    for i, f in enumerate(frames):
        sc.set_q(f.q)
        sc.set_chair_tilt(f.chair_tilt)
        d[i], st[i] = l8.measure(sc, f.present, rng)
    ms = np.array([int(f.t * 1000) for f in frames])

    rule = None
    try:
        from tofsim.rule import RuleReplay

        first = {}
        for i, f in enumerate(frames):
            if f.phase == "step" and f.step in CMDS and f.step not in first.values():
                first[i] = f.step
        rule = RuleReplay().run(ms, d, st, cmds={i: CMDS[s] for i, s in first.items()})["posture"]
    except Exception as e:  # noqa: BLE001
        print(f"규칙 재판정 생략: {e}")
    ai = ai_predict(a.model, d, st, frames)

    T = 300
    r = mujoco.Renderer(sc.model, T, T)
    f_big, f_mid, f_small = font(22, True), font(18), font(14)
    step = max(1, int(round(15 / a.fps)))
    out_frames = []
    for i in range(0, n, step):
        f = frames[i]
        sc.set_q(f.q)
        sc.set_chair_tilt(f.chair_tilt)
        if not f.present:  # 자리비움: 사람을 화면 밖으로
            mujoco.mj_kinematics(sc.model, sc.data)
        tiles = []
        for az, el in ((90, -5), (15, -10)):
            cam = mujoco.MjvCamera()
            cam.lookat[:] = [room.dist - 0.05, 0, room.desk_h + 0.25]
            cam.distance, cam.azimuth, cam.elevation = 1.5, az, el
            opt = mujoco.MjvOption()
            opt.geomgroup[2] = 1 if f.present else 0
            r.update_scene(sc.data, camera=cam, scene_option=opt)
            tiles.append(r.render())
        tiles.append(heat(d[i], T - T % 8 if T % 8 else T))
        h = max(t.shape[0] for t in tiles)
        tiles = [np.pad(t, ((0, h - t.shape[0]), (0, 0), (0, 0))) for t in tiles]
        top = np.concatenate(tiles, axis=1)
        canvas = Image.new("RGB", (top.shape[1], h + 96), (24, 24, 28))
        canvas.paste(Image.fromarray(top), (0, 0))
        g = ImageDraw.Draw(canvas)
        g.text((8, 6), "옆", font=f_small, fill=(230, 230, 230))
        g.text((T + 8, 6), "정면", font=f_small, fill=(60, 60, 60))
        g.text((2 * T + 8, 6), "가상 L8 8×8 (노랑=가까움)", font=f_small, fill=(30, 30, 30))
        truth = f.prompt
        g.text((10, h + 8), f"정답: {KO.get(truth, truth)}", font=f_big, fill=(255, 255, 255))
        g.text((T * 2 + 120, h + 12), f"{f.t:5.1f} s", font=f_mid, fill=(170, 170, 170))

        def verdict(x, label, y, color_ok=(110, 220, 120), color_bad=(240, 110, 100)):
            if x is None:
                return
            p = str(x[i])
            ok = truth is not None and (p == truth)
            col = (200, 200, 200) if truth is None or p == "unknown" else (color_ok if ok else color_bad)
            g.text((10 + (0 if label == "규칙" else T + 40), y), f"{label}: {KO.get(p, p)}", font=f_mid, fill=col)

        verdict(rule, "규칙", h + 50)
        verdict(ai, "AI", h + 50)
        out_frames.append(np.asarray(canvas))

    import imageio.v2 as imageio

    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.out.suffix.lower() == ".gif":
        imageio.mimsave(a.out, out_frames, duration=1 / a.fps, loop=0)
    else:
        imageio.mimsave(a.out, out_frames, fps=a.fps, codec="libx264", quality=7,
                        macro_block_size=8)
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
