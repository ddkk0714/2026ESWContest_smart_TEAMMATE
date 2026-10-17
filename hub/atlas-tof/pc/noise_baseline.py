#!/usr/bin/env python3
"""ToF 잡음 기준선: 장치를 벽 하나만 보게 두고 잰 오차를 "이 정도는 잡음" 으로 고정한다.

    # 1) 녹화 — Pi 4 중계기(uart_relay_board)의 HTTP /raw 를 읽는다. 센서별 새 프레임만 저장
    python noise_baseline.py record --url http://<Pi4>:8772/raw --seconds 60 --out wall65_normal.jsonl

    # 2) 분석 — 벽까지 거리(센서 유리면 기준 mm)를 알려 준다
    python noise_baseline.py analyze wall65_normal.jsonl --dist-mm 650 --out noise_baseline_wall65.json

절차·판정 기준은 docs/tof-noise-baseline.md. 표준 라이브러리만 쓴다(numpy 없음).

녹화 한 줄 = {"t": PC 시각 s, "sensor": 0|1, "frame_id": n, "dist_mm": [64], "status": [64]}.
분석 결과 JSON 은 센서별로
  - zone 별 유효율·평균·시간 표준편차(σ_t)·평균의 벽 대비 편차(평면·반경 두 기준)
  - 요약: σ_t 중앙값·p95, 프레임 간 변화 |Δ| p99, 결측률
  - noise_floor_mm = max(3·σ_t p95, |Δ| p99): zone 하나가 이보다 덜 움직이면 잡음으로 본다
  - sim_fit: 시뮬레이터 SensorModel 에 넣을 값(sigma_floor_mm, dropout)의 1차 추정
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.request

VALID_STATUS = (5, 6, 9, 10)
FOV_DEG = 45.0          # VL53L8CX 8×8 대각 아닌 가로·세로 시야
GRID = 8


def _pct(values, q):
    if not values:
        return float("nan")
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def record(args) -> int:
    last = {}
    n = {0: 0, 1: 0}
    t_end = time.time() + args.seconds
    with open(args.out, "w", encoding="utf-8") as out:
        while time.time() < t_end:
            try:
                j = json.load(urllib.request.urlopen(args.url, timeout=2))
            except Exception as e:  # 보드가 잠깐 안 받는 건 넘긴다
                print("read failed:", e, file=sys.stderr)
                time.sleep(0.5)
                continue
            for s in j.get("sensors", []):
                if not s.get("have"):
                    continue
                key = (s["frame_id"], s.get("frame_seq"))
                if last.get(s["id"]) == key:
                    continue
                last[s["id"]] = key
                out.write(json.dumps({"t": round(time.time(), 3), "sensor": s["id"], "frame_id": s["frame_id"],
                                      "dist_mm": s["dist_mm"], "status": s["status"]}, separators=(",", ":")) + "\n")
                n[s["id"]] = n.get(s["id"], 0) + 1
            time.sleep(args.poll)
    print(f"saved {args.out}: " + ", ".join(f"sensor {k} {v} frames" for k, v in sorted(n.items())))
    return 0


def _expected(dist_mm: float):
    """평평한 벽이 센서 축에 수직일 때 zone 가운데 방향의 거리. (평면 = 축 방향 성분, 반경 = 광선 길이)"""
    half = math.radians(FOV_DEG) / 2
    step = 2 * half / GRID
    flat, radial = [], []
    for r in range(GRID):
        el = half - step * (r + 0.5)
        for c in range(GRID):
            az = half - step * (c + 0.5)
            cosang = math.cos(el) * math.cos(az)
            flat.append(dist_mm)
            radial.append(dist_mm / cosang)
    return flat, radial


def analyze(args) -> int:
    frames = {}
    for line in open(args.recording, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        frames.setdefault(r["sensor"], []).append(r)
    if not frames:
        print("no frames", file=sys.stderr)
        return 1
    flat, radial = _expected(args.dist_mm)
    result = {"recording": args.recording, "dist_mm": args.dist_mm, "note": args.note, "sensors": {}}
    for sid, fr in sorted(frames.items()):
        nz = len(fr[0]["dist_mm"])
        per_zone = []
        sig, jumps, invalid, total = [], [], 0, 0
        for z in range(nz):
            vals, prev = [], None
            for f in fr:
                total += 1
                d, st = f["dist_mm"][z], f["status"][z]
                if d is None or d <= 0 or st not in VALID_STATUS:
                    invalid += 1
                    prev = None
                    continue
                vals.append(d)
                if prev is not None:
                    jumps.append(abs(d - prev))
                prev = d
            valid_rate = len(vals) / len(fr)
            mean = statistics.fmean(vals) if vals else None
            sd = statistics.pstdev(vals) if len(vals) > 1 else None
            if sd is not None:
                sig.append(sd)
            per_zone.append({
                "zone": z, "valid_rate": round(valid_rate, 3),
                "mean_mm": round(mean, 1) if mean is not None else None,
                "sigma_t_mm": round(sd, 2) if sd is not None else None,
                "bias_flat_mm": round(mean - flat[z], 1) if mean is not None else None,
                "bias_radial_mm": round(mean - radial[z], 1) if mean is not None else None,
            })
        sig_p95 = _pct(sig, 0.95)
        jump_p99 = _pct(jumps, 0.99)
        floor = max(3 * sig_p95, jump_p99) if sig else float("nan")
        bias_flat = [p["bias_flat_mm"] for p in per_zone if p["bias_flat_mm"] is not None]
        bias_rad = [p["bias_radial_mm"] for p in per_zone if p["bias_radial_mm"] is not None]
        summary = {
            "frames": len(fr),
            "seconds": round(fr[-1]["t"] - fr[0]["t"], 1),
            "invalid_rate": round(invalid / total, 4) if total else None,
            "sigma_t_median_mm": round(statistics.median(sig), 2) if sig else None,
            "sigma_t_p95_mm": round(sig_p95, 2),
            "jump_p99_mm": round(jump_p99, 1),
            "noise_floor_mm": round(floor, 1),
            "bias_flat_median_mm": round(statistics.median(bias_flat), 1) if bias_flat else None,
            "bias_radial_median_mm": round(statistics.median(bias_rad), 1) if bias_rad else None,
            "fixed_pattern_p95_mm": round(_pct([abs(b - statistics.median(bias_flat)) for b in bias_flat], 0.95), 1)
            if bias_flat else None,
        }
        result["sensors"][str(sid)] = {
            "summary": summary,
            "sim_fit": {"sigma_floor_mm": summary["sigma_t_median_mm"], "dropout": summary["invalid_rate"],
                        "at_dist_mm": args.dist_mm},
            "zones": per_zone,
        }
        print(f"\nsensor {sid}: {summary['frames']} frames / {summary['seconds']} s, invalid {summary['invalid_rate']}")
        print(f"  sigma_t median {summary['sigma_t_median_mm']} mm, p95 {summary['sigma_t_p95_mm']} mm, "
              f"|jump| p99 {summary['jump_p99_mm']} mm -> noise floor {summary['noise_floor_mm']} mm")
        print(f"  bias vs wall: flat {summary['bias_flat_median_mm']} mm, radial {summary['bias_radial_median_mm']} mm, "
              f"fixed pattern p95 {summary['fixed_pattern_p95_mm']} mm")
        print("  sigma_t per zone (mm, row 0 = top):")
        for r in range(GRID):
            row = per_zone[r * GRID:(r + 1) * GRID]
            print("   " + " ".join(f"{p['sigma_t_mm']:5.1f}" if p["sigma_t_mm"] is not None else "    ." for p in row))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\nwrote {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="Pi 4 중계기 /raw 를 읽어 jsonl 로 저장")
    r.add_argument("--url", default="http://172.20.10.3:8772/raw")
    r.add_argument("--seconds", type=float, default=60.0)
    r.add_argument("--poll", type=float, default=0.05, help="읽기 간격 s (센서 5 Hz 보다 촘촘히)")
    r.add_argument("--out", required=True)
    a = sub.add_parser("analyze", help="녹화를 분석해 noise_floor 등을 JSON 으로")
    a.add_argument("recording")
    a.add_argument("--dist-mm", type=float, required=True, help="센서 유리면에서 벽까지(축 방향) mm")
    a.add_argument("--note", default="", help="조명·벽 재질 등 메모")
    a.add_argument("--out", required=True)
    args = ap.parse_args()
    return record(args) if args.cmd == "record" else analyze(args)


if __name__ == "__main__":
    sys.exit(main())
