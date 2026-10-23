"""posture_sim CLI.

    python -m tools.posture_sim run <scenario.yaml> [...] --out runs/posture_sim --model models/opt_l8v5_c256_best.pt [--seed N]
    python -m tools.posture_sim run <scenario.yaml> --synthetic --posture oracle      # tofsim·torch 없이(시험·점검)
    python -m tools.posture_sim run <scenario.yaml> --presence any                    # 재실 규칙 비교(mmwave | any)
    python -m tools.posture_sim table runs/posture_sim/*/summary.json
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
    from tools.posture_sim.runner import run_posture
    from tools.posture_sim.scenario import load_scenario
    if args.tofsim:
        os.environ["TOFSIM_ROOT"] = args.tofsim
    rc = 0
    for path in args.scenario:
        sc = load_scenario(path)
        if args.seed is not None:
            sc["seed"] = int(args.seed)
        if args.presence:
            sc["sensors_resolved"]["presence"]["priority"] = args.presence
        src = None
        if args.synthetic:
            from tools.posture_sim.synthetic import SyntheticPerson
            src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=float(sc["fps"]))
        label = args.label or (sc["name"] + (f"_pres-{args.presence}" if args.presence else "")
                               + (f"_s{args.seed}" if args.seed is not None else ""))
        try:
            res = run_posture(sc, out_dir=args.out, model_path=args.model, posture=args.posture, source=src,
                              backend=args.backend, tofsim_root=args.tofsim, label=label)
        except Exception as exc:  # noqa: BLE001
            print(f"[posture_sim] {path}: 실패 — {exc}", file=sys.stderr)
            if args.raise_errors:
                raise
            rc = 1
            continue
        pr = res["presence"]
        print(f"[posture_sim] {res['run_dir']}: posture acc {res['posture']['acc_sec']:.3f}, "
              f"presence decision {pr['decision']['acc']} / OR {pr['or']['acc']}, wall {res['wall_s']} s")
    return rc


def cmd_table(args: argparse.Namespace) -> int:
    paths = sorted(p for pat in args.summaries for p in glob.glob(pat))
    print("| run | 자세 정확도 | misplaced 오탐 | 재실(결정) | 재실(OR) | 재실(ToF) | 자리비움 부재까지(s) | 복귀 재실까지(s) |")
    print("|---|---|---|---|---|---|---|---|")
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            s = json.load(fh)
        pr = s["presence"]
        eps = pr["away_episodes"]
        print(f"| {os.path.basename(os.path.dirname(p))} | {s['posture']['acc_sec']} | {s['posture']['misplaced_fp']} | "
              f"{pr['decision']['acc']} | {pr['or']['acc']} | {pr['tof']['acc']} | {[e['absent_after_s'] for e in eps]} | "
              f"{[e['present_after_return_s'] for e in eps]} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.posture_sim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="각본을 돌려 자세·재실 판정을 평가")
    r.add_argument("scenario", nargs="+")
    r.add_argument("--out", default="runs/posture_sim")
    r.add_argument("--model", help="PostureNet 체크포인트(.pt). 없으면 oracle")
    r.add_argument("--posture", choices=["gru", "oracle"], default="gru")
    r.add_argument("--synthetic", action="store_true", help="tofsim 대신 합성 격자(시험용)")
    r.add_argument("--backend", choices=["mujoco", "numpy"])
    r.add_argument("--tofsim", help="tofsim 루트(TOFSIM_ROOT)")
    r.add_argument("--seed", type=int)
    r.add_argument("--presence", choices=["mmwave", "any"], help="책상 재실 규칙(기본: config/sensors.yaml presence.priority)")
    r.add_argument("--label")
    r.add_argument("--raise-errors", action="store_true")
    r.set_defaults(func=cmd_run)
    t = sub.add_parser("table", help="summary.json 들을 표로")
    t.add_argument("summaries", nargs="+")
    t.set_defaults(func=cmd_table)
    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
