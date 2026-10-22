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
        src = None
        if args.synthetic:
            from tools.life_sim.synthetic import SyntheticPerson
            src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=float(sc["fps"]))
        label = args.label or (f"{sc['persona']}_{sc['name']}" + (f"_s{args.seed}" if args.seed is not None else ""))
        try:
            res = run_scenario(sc, out_dir=args.out, model_path=args.model, posture=args.posture, source=src,
                               backend=args.backend, tofsim_root=args.tofsim, label=label)
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
            from tools.life_sim.synthetic import SyntheticPerson
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
    r.add_argument("--raise-errors", action="store_true")
    r.set_defaults(fn=cmd_run)
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
    t = sub.add_parser("table")
    t.add_argument("summary", nargs="+")
    t.set_defaults(fn=cmd_table)
    args = ap.parse_args(argv)
    if args.cmd in ("run", "ab") and args.posture == "gru" and not args.model:
        args.posture = "oracle"
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
