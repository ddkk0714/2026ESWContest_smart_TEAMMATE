"""가상 L8 세션 생성: 사람·방·센서 설치를 무작위로 바꿔 지시 자세 프로토콜을 '녹화'한다.

  python generate.py --sessions 20 --seed 1 --jobs 4
  python generate.py --sessions 1 --seed 7 --dist 0.6 --mount-z 0.15 --pitch 10   # 조건 고정

출력은 dataset/tof_logger 와 같은 세션 형식(data/sessions/sim<seed>_..._sim/)이라 inspect_session.py·
학습 코드를 그대로 쓴다. rule 칸에는 펌웨어 TofPosture.cpp 를 그대로 돌린 판정이 들어간다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tof_logger"))
from tofl.protocol import Schedule, load_config  # noqa: E402
from tofl.session import SessionWriter  # noqa: E402

from tofsim.l8 import L8, L8Params, Mount  # noqa: E402
from tofsim.motion import StepSpec, timeline  # noqa: E402
from tofsim.rule import RuleReplay  # noqa: E402
from tofsim.scene import Person, Room, build_scene  # noqa: E402

FPS = 15.0


def sample_world(rng: np.random.Generator, fixed: dict) -> tuple[Person, Room, Mount]:
    person = Person(scale=rng.uniform(0.92, 1.08), width=rng.uniform(0.9, 1.1),
                    hair=rng.uniform(0.04, 0.30), skin=rng.uniform(0.30, 0.60),
                    clothes=rng.uniform(0.05, 0.70), pants=rng.uniform(0.05, 0.50))
    dist = fixed.get("dist") or rng.uniform(0.50, 0.80)
    desk_h = rng.uniform(0.70, 0.76)
    room = Room(desk_h=desk_h, seat_h=rng.uniform(0.42, 0.50), dist=dist,
                person_y=rng.normal(0, 0.04), desk_edge=dist - rng.uniform(0.15, 0.30),
                wall=(rng.uniform(dist + 0.6, 3.0) if rng.random() < 0.7 else None),
                chair_back=rng.uniform(0.08, 0.20), chair_tilt_deg=rng.uniform(3, 12),
                desk_refl=rng.uniform(0.15, 0.60), wall_refl=rng.uniform(0.40, 0.90),
                chair_refl=rng.uniform(0.04, 0.25), floor_refl=rng.uniform(0.15, 0.40))
    mz = fixed.get("mount_z")
    mount = Mount(x=0.0, y=rng.normal(0, 0.02),
                  z=desk_h + (mz if mz is not None else rng.uniform(0.03, 0.30)),
                  pitch=fixed.get("pitch") if fixed.get("pitch") is not None else rng.uniform(0, 25),
                  yaw=rng.normal(0, 3), roll=rng.normal(0, 2))
    return person, room, mount


def steps_from_config(cfg: dict) -> tuple[list[StepSpec], float, dict[int, str]]:
    raw = cfg["protocol"]["steps"]
    steps = [StepSpec(s["label"], float(s["seconds"]), s.get("sim")) for s in raw]
    cmds = {i: s["cmd"] for i, s in enumerate(raw) if s.get("cmd")}
    return steps, float(cfg["protocol"]["transition_s"]), cmds


def generate_one(seed: int, out: Path | None, fixed: dict, sub: int, with_rule: bool,
                 config: str | None = None) -> Path:
    rng = np.random.default_rng(seed)
    cfg = load_config(config)
    Schedule.from_config(cfg)  # 라벨·명령 검사
    steps, trans, cmds = steps_from_config(cfg)
    person, room, mount = sample_world(rng, fixed)
    sc = build_scene(person, room)
    l8 = L8(mount, L8Params(sub=sub))
    frames = timeline(sc, steps, trans, rng, fps=FPS)

    n = len(frames)
    d = np.zeros((n, 8, 8), dtype=np.int16)
    st = np.zeros((n, 8, 8), dtype=np.uint8)
    for i, f in enumerate(frames):
        sc.set_q(f.q)
        sc.set_chair_tilt(f.chair_tilt)
        d[i], st[i] = l8.measure(sc, f.present, rng)
    ms = np.array([int(round(f.t * 1000)) for f in frames])

    # 수집기는 단계(step 구간)가 시작되는 첫 프레임에서 명령을 보낸다
    cmd_at: dict[int, str] = {}
    for i, f in enumerate(frames):
        if f.phase == "step" and f.step in cmds and f.step not in {frames[j].step for j in cmd_at}:
            cmd_at[i] = cmds[f.step]
    rule = None
    if with_rule:
        rule = RuleReplay().run(ms, d, st, cmds=cmd_at)

    meta = {"source": "sim", "seed": seed, "note": "MuJoCo 가상 L8",
            "sim": {"person": asdict(person), "room": asdict(room), "mount": asdict(mount),
                    "l8": asdict(L8Params(sub=sub))},
            "protocol": Schedule.from_config(cfg).snapshot(), "rule_source":
                "firmware TofPosture.cpp (replay)" if with_rule else None}
    w = SessionWriter(f"sim{seed:04d}", "sim", meta, base=out,
                      stamp=time.strftime("%Y%m%d_%H%M%S"))
    t0 = 1_700_000_000.0
    w.event("tof_hello", t0, ms=0, sensor="SIM-L8", w=8, h=8, hz=int(FPS), flip_rows=False,
            flip_cols=False, target_order="closest", fw=0)
    for i, f in enumerate(frames):
        if i in cmd_at:
            w.event("cmd", t0 + f.t, cmd=cmd_at[i], step=f.step)
        if rule is not None:
            r = {"present": bool(rule["present"][i]), "posture": str(rule["posture"][i]),
                 "conf": round(float(rule["conf"][i]), 3), "baseline": bool(rule["baseline"][i]),
                 "head_mm": _num(rule["head_mm"][i]), "torso_mm": _num(rule["torso_mm"][i]),
                 "drop_mm": _num(rule["drop_mm"][i]), "chin": round(float(rule["chin"][i]), 3),
                 "motion": round(float(rule["motion"][i]), 3), "nods": int(rule["nods"][i])}
        else:
            r = {"present": None, "posture": None}
        obj = {"seq": i, "ms": int(ms[i]), "w": 8, "h": 8, "d": d[i].ravel().tolist(),
               "st": st[i].ravel().tolist(), "rule": r}
        w.frame(obj, t0 + f.t, f.prompt, f.phase, f.step)
    w.close(end_reason="done", sim_truth={"nodding_frames": int(sum(f.nodding for f in frames))})
    (w.folder / "sim_truth.json").write_text(json.dumps(
        {"present": [f.present for f in frames], "nodding": [f.nodding for f in frames]}),
        encoding="utf-8")
    return w.folder


def _num(v: float):
    return None if not np.isfinite(v) else round(float(v), 1)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--sessions", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1, help="첫 세션 시드. 세션 i 는 seed+i")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out", type=Path, default=HERE / "data" / "sessions")
    ap.add_argument("--config", help="프로토콜(기본 dataset/tof_logger/config.yaml)")
    ap.add_argument("--sub", type=int, default=6, help="칸당 sub×sub 광선")
    ap.add_argument("--no-rule", action="store_true", help="규칙 재판정 생략(컴파일러 없을 때)")
    ap.add_argument("--dist", type=float, help="사람 거리 고정(m)")
    ap.add_argument("--mount-z", type=float, help="센서 높이(책상 위, m) 고정")
    ap.add_argument("--pitch", type=float, help="센서 위쪽 기울기(도) 고정")
    a = ap.parse_args()
    fixed = {"dist": a.dist, "mount_z": a.mount_z, "pitch": a.pitch}
    a.out.mkdir(parents=True, exist_ok=True)
    seeds = [a.seed + i for i in range(a.sessions)]
    if not a.no_rule:
        RuleReplay()  # 병렬 실행 전에 한 번 빌드
    t = time.time()
    args = [(s, a.out, fixed, a.sub, not a.no_rule, a.config) for s in seeds]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            folders = list(ex.map(_star, args))
    else:
        folders = [generate_one(*x) for x in args]
    for f in folders:
        print(f)
    print(f"{len(folders)} 세션, {time.time() - t:.0f} s")
    return 0


def _star(x):
    return generate_one(*x)


if __name__ == "__main__":
    sys.exit(main())
