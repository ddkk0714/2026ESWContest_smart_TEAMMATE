"""life_sim CLI.

    python -m tools.life_sim run <scenario.yaml> [...] --out runs/lifesim --model models/opt_l8v5_c256_best.pt [--tofsim DIR]
    python -m tools.life_sim run <scenario.yaml> --posture oracle --synthetic        # tofsim·torch 없이 하네스만
    python -m tools.life_sim table runs/lifesim/*/summary.json                      # 요약 표
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HUB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HUB not in sys.path:
    sys.path.insert(0, HUB)


def cmd_run(args: argparse.Namespace) -> int:
    from tools.life_sim.harness import run_scenario
    from tools.life_sim.scenario import load_scenario
    if args.tofsim:
        os.environ["TOFSIM_ROOT"] = args.tofsim
    rc = 0
    for path in args.scenario:
        sc = load_scenario(path)
        if args.seed is not None:
            sc["seed"] = int(args.seed)
        if args.fsm_config:
            sc["hub"]["fsm_config"] = args.fsm_config
        if args.presence:
            sc["hub"]["presence_priority"] = args.presence
        if args.room_random:
            sc["devices_resolved"].setdefault("room", {})["randomize"] = True
        if args.desk_max_cm is not None:
            sc["hub"]["desk_max_cm"] = args.desk_max_cm
        if args.ingest_set:
            sc["hub"]["ingest_set"] = list(args.ingest_set)
        if args.vent_rule:
            sc["devices_resolved"].setdefault("vent_control", {})["enabled"] = True
        if args.fatigue:
            sc["reaction_resolved"].setdefault("fatigue", {})["enabled"] = True
        if args.policy_engine:
            sc["policy"]["engine"] = args.policy_engine
        if args.policy_profile:
            sc["policy"]["profile"] = args.policy_profile
        src = None
        if args.synthetic:
            from tools.posture_sim.synthetic import SyntheticPerson
            src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=float(sc["fps"]))
        label = args.label or (f"{sc['persona']}_{sc['name']}" + ("_rr" if args.room_random else "")
                               + (f"_pres-{args.presence}" if args.presence else "")
                               + (f"_s{args.seed}" if args.seed is not None else ""))
        try:
            res = run_scenario(sc, out_dir=args.out, model_path=args.model, posture=args.posture, source=src,
                               backend=args.backend, tofsim_root=args.tofsim, label=label, replay=bool(args.replay))
        except Exception as exc:  # noqa: BLE001
            print(f"[life_sim] {path}: 실패 — {exc}", file=sys.stderr)
            if args.raise_errors:
                raise
            rc = 1
            continue
        print(f"[life_sim] {res['run_dir']}: states {' → '.join(res['fsm']['states_seq'])}")
        print(f"           interventions {res['n_interventions']}, wakes {len(res['wakes'])}, posture acc {res['posture']['acc_sec']:.3f}, "
              f"wall {res['wall_s']} s")
    return rc


def cmd_ab(args: argparse.Namespace) -> int:
    from tools.life_sim.ab import run_ab
    from tools.life_sim.scenario import load_scenario
    if args.tofsim:
        os.environ["TOFSIM_ROOT"] = args.tofsim
    rc = 0
    for path in args.scenario:
        sc = load_scenario(path)
        if args.seed is not None:
            sc["seed"] = int(args.seed)
        factory = None
        if args.synthetic:
            from tools.posture_sim.synthetic import SyntheticPerson
            factory = lambda s: SyntheticPerson(s["segments"], seed=s["seed"], fps=float(s["fps"]))  # noqa: E731
        try:
            cmp = run_ab(sc, out_dir=args.out, model_path=args.model, posture=args.posture, backend=args.backend,
                         tofsim_root=args.tofsim, source_factory=factory, seeds=args.seeds)
        except Exception as exc:  # noqa: BLE001
            print(f"[life_sim] ab {path}: 실패 — {exc}", file=sys.stderr)
            if args.raise_errors:
                raise
            rc = 1
            continue
        print(f"[life_sim] {cmp['dir']}")
        for r in cmp["rows"]:
            print(f"  {r['account']} {r['policy']}: 개입 {r['interventions']} 카드 {r['cards']} 수락률 {r['accept_rate']} "
                  f"깨어남 {r['wakes']} 평균 C_fat {r['mean_c_fatigue']:.3f} 취침권유 {r['bedtime']} 환기 {r['vent']}")
    return rc


def cmd_replot(args: argparse.Namespace) -> int:
    """저장된 frames/ticks/events.jsonl 로 timeline.png 를 다시 그린다."""
    from tools.life_sim.plots import plot_timeline
    rc = 0
    for d in [p for pat in args.run_dir for p in sorted(glob.glob(pat))]:
        try:
            load = lambda f: [json.loads(l) for l in open(os.path.join(d, f), encoding="utf-8")]  # noqa: E731
            rows, ticks, events = load("frames.jsonl"), load("ticks.jsonl"), load("events.jsonl")
            plot_timeline(rows, ticks, events, os.path.join(d, "timeline.png"), title=os.path.basename(d.rstrip("/\\")),
                          thresholds={"fatigue_confirm": 0.70, "fatigue_focus_low": 0.40, "recovery_success": 0.30})
            print(f"[life_sim] {d}/timeline.png")
        except Exception as exc:  # noqa: BLE001
            print(f"[life_sim] replot {d}: 실패 — {type(exc).__name__}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def cmd_video(args: argparse.Namespace) -> int:
    from tools.life_sim.video import make_video
    out = args.out or os.path.join(args.run_dir, "video.mp4")
    cam = {k: getattr(args, k) for k in ("azimuth", "elevation", "distance") if getattr(args, k) is not None}
    res = make_video(args.run_dir, out, speed=args.speed, fps=args.fps, tofsim_root=args.tofsim, max_seconds=args.seconds,
                     start_s=args.start, camera=cam)
    print(f"[life_sim] {res['out']}: {res['frames']} frames, x{res['speed']:.0f}, smplx={res.get('smplx')}")
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    from tools.life_sim.video import make_video
    cam = {k: getattr(args, k) for k in ("azimuth", "elevation", "distance") if getattr(args, k) is not None}
    res = make_video(args.run_dir, None, speed=args.speed, fps=args.fps, tofsim_root=args.tofsim, max_seconds=args.seconds,
                     start_s=args.start, camera=cam, live=True, loop=not args.once)
    print(f"[life_sim] play 끝: {res['frames']} frames")
    return 0


def cmd_viewer(args: argparse.Namespace) -> int:
    from tools.life_sim.viewer import run_viewer
    run_viewer(args.run_dir, speed=args.speed, start_s=args.start, loop=not args.once, tofsim_root=args.tofsim)
    return 0


def cmd_daygen(args: argparse.Namespace) -> int:
    from tools.life_sim.daygen import write_days
    seeds: list[int] = []
    for part in args.seeds.split(","):
        lo, _, hi = part.partition("-")
        seeds.extend(range(int(lo), int(hi or lo) + 1))
    index = write_days(seeds, args.out, desk_hours=(args.desk_min_h, args.desk_max_h), vent_rule_p=args.vent_rule_p)
    by = lambda k: {v: sum(1 for d in index if d[k] == v) for v in sorted({d[k] for d in index}, key=str)}  # noqa: E731
    print(f"[life_sim] daygen {len(index)} 각본 → {args.out}  페르소나 {by('persona')} 계절 {by('season')} 환기규칙 {by('vent_rule')}")
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    paths = [p for pat in args.summary for p in sorted(glob.glob(pat))]
    rows = ["| 실행 | 길이 | FSM 경로 | 개입 | 깨어남 | 환기/명령 | 휴식 | 자세 acc | drowsy 재현(모델/0x03) |",
            "|---|---|---|---|---|---|---|---|---|"]
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            s = json.load(fh)
        dr = (s["posture"]["per_label"].get("drowsy") or {}).get("recall")
        cmds = "; ".join(f"{c['target']}={c['value']}" for c in s["commands"][:4])
        rows.append(f"| {s['persona']}/{s['name']} | {s['duration_s']} s | {' → '.join(s['fsm']['states_seq'])} | "
                    f"{', '.join(iv['kind'] for iv in s['interventions'])} | {len(s['wakes'])} | {cmds or '-'} | "
                    f"{len(s['rests'])} | {s['posture']['acc_sec']:.2f} | {dr} / {s['posture']['drowsy_flag_recall_0x03']} |")
    print("\n".join(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="life_sim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario", nargs="+")
    r.add_argument("--out", default="runs/lifesim")
    r.add_argument("--model", help="PostureNet 체크포인트(.pt). 없으면 --posture oracle")
    r.add_argument("--posture", choices=["gru", "oracle"], default="gru")
    r.add_argument("--synthetic", action="store_true", help="tofsim 대신 합성 격자(시험용)")
    r.add_argument("--backend", choices=["mujoco", "numpy"])
    r.add_argument("--tofsim", help="tofsim 루트(TOFSIM_ROOT)")
    r.add_argument("--fsm-config")
    r.add_argument("--seed", type=int)
    r.add_argument("--label")
    r.add_argument("--replay", action="store_true", help="프레임마다 Pose·격자·판정을 replay.jsonl 로(video 명령용, 실행당 ~20 MB)")
    r.add_argument("--presence", choices=["mmwave", "any"], help="hub 재실 판정: mmwave(우선, 기본 설정) | any(둘 중 하나, 이전 동작)")
    r.add_argument("--room-random", action="store_true", help="사용 환경(천장등·일광률·방 크기·무드등 세기)을 시드마다 뽑는다(devices.yaml room.randomize_ranges)")
    r.add_argument("--desk-max-cm", type=float, help="책상 재실 거리 상한(ingest.yaml desk_max_cm 덮어쓰기, 민감도 실험)")
    r.add_argument("--ingest-set", action="append", metavar="KEY=VALUE",
                   help="ingest.yaml 값 덮어쓰기(점 경로, 값은 YAML). 예: elapsed.reset_on_rest=true. 여러 번 줄 수 있다")
    r.add_argument("--vent-rule", action="store_true",
                   help="CO₂ 환기 타이밍 규칙 켜기(devices.yaml vent_control — FSM 밖, 깨어 있을 때 창문 환기 카드)")
    r.add_argument("--fatigue", action="store_true",
                   help="잠재 피로 켜기(reaction.yaml fatigue — 졸음·자세 무너짐·키 입력 저하가 피로 F 에서 생기고 정답 라벨을 남김)")
    r.add_argument("--policy-engine", choices=["hub", "simple", "none"], help="각본의 policy.engine 덮어쓰기")
    r.add_argument("--policy-profile", choices=["none", "synth", "global"], help="각본의 policy.profile 덮어쓰기")
    r.add_argument("--raise-errors", action="store_true")
    r.set_defaults(fn=cmd_run)
    v = sub.add_parser("video", help="replay.jsonl 로 MuJoCo 3D + 센서 격자 + 상태 영상(mp4)")
    v.add_argument("run_dir")
    v.add_argument("--out", help="mp4 경로(기본 <run_dir>/video.mp4)")
    v.add_argument("--speed", type=float, default=10.0, help="재생 배속(기본 10: 1 시간 각본 → 6 분)")
    v.add_argument("--fps", type=int, default=20)
    v.add_argument("--start", type=float, default=0.0, help="각본 시작 기준 몇 초부터")
    v.add_argument("--seconds", type=float, help="몇 초 분량만")
    v.add_argument("--tofsim")
    v.add_argument("--azimuth", type=float)
    v.add_argument("--elevation", type=float)
    v.add_argument("--distance", type=float)
    v.set_defaults(fn=cmd_video)
    a = sub.add_parser("ab")
    a.add_argument("scenario", nargs="+")
    a.add_argument("--out", default="runs/lifesim")
    a.add_argument("--model")
    a.add_argument("--posture", choices=["gru", "oracle"], default="gru")
    a.add_argument("--synthetic", action="store_true")
    a.add_argument("--backend", choices=["mujoco", "numpy"])
    a.add_argument("--tofsim")
    a.add_argument("--seed", type=int)
    a.add_argument("--seeds", type=int, default=1, help="시드 수(기본 시드부터 연속). A/B 는 같은 시드 집합을 쓴다")
    a.add_argument("--raise-errors", action="store_true")
    a.set_defaults(fn=cmd_ab)
    rp = sub.add_parser("replot")
    rp.add_argument("run_dir", nargs="+")
    rp.set_defaults(fn=cmd_replot)
    pl = sub.add_parser("play", help="video 와 같은 화면(3D+기기+센서 격자+상태)을 창에서 실시간 재생(PC)")
    pl.add_argument("run_dir")
    pl.add_argument("--speed", type=float, default=6.0)
    pl.add_argument("--fps", type=int, default=10)
    pl.add_argument("--start", type=float, default=0.0)
    pl.add_argument("--seconds", type=float)
    pl.add_argument("--once", action="store_true")
    pl.add_argument("--tofsim")
    pl.add_argument("--azimuth", type=float)
    pl.add_argument("--elevation", type=float)
    pl.add_argument("--distance", type=float)
    pl.set_defaults(fn=cmd_play)
    vw = sub.add_parser("viewer", help="MuJoCo 뷰어 창에서 replay 를 실시간 재생(PC)")
    vw.add_argument("run_dir")
    vw.add_argument("--speed", type=float, default=4.0)
    vw.add_argument("--start", type=float, default=0.0)
    vw.add_argument("--once", action="store_true")
    vw.add_argument("--tofsim")
    vw.set_defaults(fn=cmd_viewer)
    t = sub.add_parser("table")
    t.add_argument("summary", nargs="+")
    t.set_defaults(fn=cmd_table)
    dg = sub.add_parser("daygen", help="자유 생활 하루 각본(잠재 피로 켬) — 2단계 피로 모델 학습 데이터용")
    dg.add_argument("--seeds", required=True, help="예: 1-120 또는 1,5,9-12")
    dg.add_argument("--out", required=True)
    dg.add_argument("--desk-min-h", type=float, default=2.0)
    dg.add_argument("--desk-max-h", type=float, default=3.5)
    dg.add_argument("--vent-rule-p", type=float, default=0.5, help="CO₂ 환기 규칙을 켤 하루의 비율")
    dg.set_defaults(fn=cmd_daygen)
    args = ap.parse_args(argv)
    if args.cmd in ("run", "ab") and args.posture == "gru" and not args.model:
        args.posture = "oracle"
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
