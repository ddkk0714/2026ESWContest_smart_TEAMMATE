"""ToF 잡음 측정: 중계기 :8772/raw 를 N 초 읽어 센서별 칸(8×8) 통계를 남긴다.

    python -I tof_noise.py --raw http://172.20.10.3:8772 --seconds 60 --label wall65_fluorescent --out noise

벽만 보게 둔 상태(PLACEMENT_BASELINE_NOISE_PROCESS.md §2)에서 돌린다. 결과 JSON 에 칸별 평균·표준편차·분위·드롭률,
상태값 분포, 프레임 간격, 1분 드리프트, 두 센서 가운데 4×4 차이를 적는다. 표준 라이브러리만 쓴다.
"""
import argparse, json, math, os, statistics, time, urllib.request

VALID = {5, 6, 9, 10}

def fetch(base):
    with urllib.request.urlopen(base + "/raw", timeout=3) as r:
        return json.load(r)

def pct(xs, q):
    if not xs: return None
    s = sorted(xs); i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="http://172.20.10.3:8772")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--label", default="wall65")
    ap.add_argument("--out", default="noise")
    ap.add_argument("--interval", type=float, default=0.1, help="폴링 간격 s (센서 5 Hz 보다 촘촘히)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    frames = {0: [], 1: []}          # (t_host, frame_seq, dist[64], status[64])
    last_seq = {0: None, 1: None}
    t0 = time.time(); polls = 0; errors = 0
    print(f"recording {a.seconds:.0f} s from {a.raw} ...", flush=True)
    while time.time() - t0 < a.seconds:
        try:
            j = fetch(a.raw); polls += 1
            for s in j.get("sensors", []):
                if not s.get("have"): continue
                sid = s["id"]
                if last_seq[sid] == s["frame_seq"]: continue
                last_seq[sid] = s["frame_seq"]
                frames[sid].append((time.time(), s["frame_seq"], s["dist_mm"], s.get("status", [])))
        except Exception as e:
            errors += 1
        time.sleep(a.interval)
    result = {"label": a.label, "raw": a.raw, "seconds": a.seconds, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
              "polls": polls, "poll_errors": errors, "sensors": {}}
    centers = {}
    for sid, fl in frames.items():
        if len(fl) < 5:
            result["sensors"][str(sid)] = {"frames": len(fl), "note": "too few frames"}; continue
        n = len(fl); z = len(fl[0][2])
        per = [[] for _ in range(z)]; drops = [0] * z; st_hist = {}
        for _, _, d, st in fl:
            for i in range(z):
                s = st[i] if i < len(st) else 5
                st_hist[s] = st_hist.get(s, 0) + 1
                if d[i] > 0 and s in VALID: per[i].append(d[i])
                else: drops[i] += 1
        mean = [statistics.fmean(p) if p else None for p in per]
        std = [statistics.pstdev(p) if len(p) > 1 else None for p in per]
        p05 = [pct(p, 0.05) for p in per]; p95 = [pct(p, 0.95) for p in per]
        drop_rate = [drops[i] / n for i in range(z)]
        stds = [s for s in std if s is not None]
        ts = [f[0] for f in fl]; gaps = [(ts[i] - ts[i-1]) * 1000 for i in range(1, n)]
        seqs = [f[1] for f in fl]; seq_gaps = sum(1 for i in range(1, n) if (seqs[i] - seqs[i-1]) & 0xFFFF > 1)
        # 1분 드리프트: 전반/후반 평균 차 (전체 유효 칸 평균)
        half = n // 2
        def frame_mean(sub):
            vals = [d[i] for _, _, d, st in sub for i in range(z) if d[i] > 0 and (st[i] if i < len(st) else 5) in VALID]
            return statistics.fmean(vals) if vals else None
        m1, m2 = frame_mean(fl[:half]), frame_mean(fl[half:])
        dur_min = max((ts[-1] - ts[0]) / 60, 1e-6)
        drift = (m2 - m1) / (dur_min / 2) if (m1 is not None and m2 is not None) else None
        # 가운데 4×4 중앙값 (row 2~5, col 2~5)
        w = 8 if z == 64 else int(math.sqrt(z))
        cvals = [v for i, p in enumerate(per) if 2 <= i // w <= 5 and 2 <= i % w <= 5 for v in p]
        centers[sid] = statistics.median(cvals) if cvals else None
        result["sensors"][str(sid)] = {
            "frames": n, "hz": (n - 1) / max(ts[-1] - ts[0], 1e-6), "seq_gaps": seq_gaps,
            "frame_gap_ms": {"median": statistics.median(gaps) if gaps else None, "p95": pct(gaps, 0.95), "max": max(gaps) if gaps else None},
            "sigma_noise_mm": statistics.median(stds) if stds else None,
            "zone_std_mm": std, "zone_mean_mm": mean, "zone_p05_mm": p05, "zone_p95_mm": p95,
            "dropout_rate": drop_rate, "dropout_median": statistics.median(drop_rate),
            "weak_zones": [i for i in range(z) if drop_rate[i] > 0.02],
            "status_hist": {str(k): v for k, v in sorted(st_hist.items())},
            "drift_mm_per_min": drift, "center4x4_median_mm": centers[sid],
        }
    if centers.get(0) is not None and centers.get(1) is not None:
        result["sensor_delta_mm"] = centers[0] - centers[1]
    path = os.path.join(a.out, f"{a.label}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(t0))}.json")
    with open(path, "w", encoding="utf-8") as f: json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"saved {path}")
    for sid, r in result["sensors"].items():
        if "sigma_noise_mm" not in r: print(f"sensor {sid}: {r}"); continue
        print(f"sensor {sid}: frames {r['frames']} ({r['hz']:.2f} Hz, seq gaps {r['seq_gaps']}) | sigma_noise {r['sigma_noise_mm']:.1f} mm "
              f"| dropout median {r['dropout_median']*100:.2f} % weak zones {len(r['weak_zones'])} | drift {r['drift_mm_per_min'] if r['drift_mm_per_min'] is None else round(r['drift_mm_per_min'],1)} mm/min "
              f"| center4x4 {r['center4x4_median_mm']} mm | status {r['status_hist']}")
    if "sensor_delta_mm" in result: print(f"sensor0 - sensor1 (center 4x4): {result['sensor_delta_mm']:.0f} mm")

if __name__ == "__main__":
    main()
