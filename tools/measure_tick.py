"""판정 주기 측정 — hub tick 한 번(캐시 스냅샷 → 특징 → FSM → 요약·발행 준비)에 걸리는 시간의 분포.

    python tools/measure_tick.py [--scenarios demo,short] [--seeds 1-5] [--json]

개발 원칙 6 "판정 사이클 ≤ 500 ms" 를 PC 에서 먼저 확인한다. tools/demo_dryrun.py 로 합성 세션을 돌리며
LiveHub.tick_once 만 잰다(MQTT 왕복·디스플레이 렌더링은 제외). Pi 4 실측은 보드에서 같은 수치를 다시 잰다 —
보드 제한 Python 은 PC 보다 훨씬 느리므로 여기 수치는 하한일 뿐이다.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from demo_dryrun import run_dryrun  # noqa: E402

BUDGET_MS = 500.0


def _seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def percentile(values: list[float], q: float) -> float:
    """선형 보간 백분위(numpy 없이)."""
    if not values:
        return float("nan")
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def measure(scenarios: list[str], seeds: list[int]) -> dict:
    times: list[float] = []
    for name in scenarios:
        for seed in seeds:
            run_dryrun(name, seed=seed, tick_times=times)
    ms = [t * 1000 for t in times]
    return {
        "ticks": len(ms),
        "p50_ms": round(percentile(ms, 0.50), 3),
        "p95_ms": round(percentile(ms, 0.95), 3),
        "p99_ms": round(percentile(ms, 0.99), 3),
        "max_ms": round(max(ms), 3) if ms else None,
        "budget_ms": BUDGET_MS,
        "within_budget": bool(ms) and percentile(ms, 0.95) <= BUDGET_MS,
        "scenarios": scenarios, "seeds": seeds,
        "host": f"{platform.system()} {platform.machine()} Python {platform.python_version()}",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", default="demo,short")
    ap.add_argument("--seeds", default="1-5")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = measure(args.scenarios.split(","), _seeds(args.seeds))
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"tick {res['ticks']}회 · p50 {res['p50_ms']} ms · p95 {res['p95_ms']} ms · p99 {res['p99_ms']} ms · "
              f"max {res['max_ms']} ms (예산 {BUDGET_MS:.0f} ms, {'통과' if res['within_budget'] else '초과'})")
        print(res["host"])
    return 0 if res["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
