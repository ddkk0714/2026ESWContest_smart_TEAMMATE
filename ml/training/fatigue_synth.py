"""합성 피로 세션 — fatigue_logger 와 같은 파일 모양. 파이프라인 시험용이며 성능 근거가 아니다.

잠재 KSS 가 수면 부족·시간대·경과 시간으로 오르고, 센서 요약이 그 값을 따라 움직인다(타자 느려짐,
고개 숙임·턱 괴기 증가, 체동 감소, CO₂ 상승). 응답은 잠재값에 잡음을 더해 반올림하고 일부는 놓친다.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

POSTURES = ("upright", "lean_forward", "forward_head", "head_down", "chin_rest", "recline", "lean_left")


def _session(rng: random.Random, pid: str, bias: float, typing: float, index: int, day0: float) -> list[dict]:
    hour = rng.choice((9.5, 13.5, 22.0))
    start = day0 + index * 86400 + hour * 3600
    sid = f"{pid}-{int(start * 1000)}"
    usual = 7.0
    sleep = round(rng.uniform(4.5, 8.5) * 2) / 2
    minutes = rng.randint(80, 120)
    base = 2.5 + 0.7 * (usual - sleep) + bias + (1.2 if hour == 13.5 else 0.0) + (1.0 if hour == 22.0 else 0.0)
    drift = rng.uniform(0.02, 0.045)
    rows: list[dict] = []

    def latent(t: float) -> float:
        return max(1.0, min(9.0, base + drift * (t - start) / 60.0))

    def ask(t: float) -> int:
        return int(max(1, min(9, round(latent(t) + rng.gauss(0, 0.7)))))

    def label(event: str, t: float, **extra) -> dict:
        return {"kind": "label", "rx_ts": t, "event": event, "session_id": sid, "profile_id": pid, "ts": t, **extra}

    rows.append(label("session_start", start, kss=ask(start), sleep_hours_last_night=sleep,
                      profile={"chronotype": "neither", "usual_sleep_h": usual, "work_type": "coding"}))
    noise = 0.0
    next_prompt = start + 900
    end = start + minutes * 60
    t = start + 10
    while t < end:
        noise = 0.9 * noise + rng.gauss(0, 0.1)
        f = (latent(t) - 1) / 8 + noise * 0.1
        weights = [max(0.02, w) for w in (0.7 - 0.5 * f, 0.1, 0.05 + 0.05 * f, 0.03 + 0.3 * f,
                                          0.03 + 0.15 * f, 0.05, 0.04)]
        posture = rng.choices(POSTURES, weights)[0]
        flight = typing * (1 + 0.4 * f) * rng.uniform(0.92, 1.08)
        summary = {
            "present": True,
            "tof": {"posture": posture, "posture_confidence": 0.8, "nod_rate_hz": max(0.0, 0.05 * f + rng.gauss(0, 0.01)),
                    "drowsy": rng.random() < 0.15 * f, "motion_score": max(0.0, 0.5 - 0.3 * f + rng.gauss(0, 0.05))},
            "mmwave": {"motion_level": max(0.0, 30 - 15 * f + rng.gauss(0, 3)), "resp_bpm": 15 - 2 * f + rng.gauss(0, 0.5),
                       "resp_valid": rng.random() < 0.7, "heart_bpm": 72, "heart_valid": False},
            "keystroke": {"valid": rng.random() < 0.9, "window_s": 60, "event_count": int(max(0, 180 * (1 - 0.4 * f) + rng.gauss(0, 15))),
                          "dwell_mean_ms": 95 * (1 + 0.2 * f), "dwell_std_ms": 20, "flight_mean_ms": flight,
                          "flight_std_ms": flight * (0.35 + 0.2 * f), "idle_ratio": min(1.0, 0.15 + 0.3 * f),
                          "correction_rate": 0.05 + 0.06 * f},
            "co2_ppm": 600 + 4 * (t - start) / 60 + rng.gauss(0, 15), "temp_c": 24.0, "humidity_pct": 45.0, "lux": 300.0,
        }
        rows.append({"kind": "tick", "rx_ts": t, "ts": t, "fsm_state": "FOCUS_PC",
                     "c_fatigue": max(0.0, min(1.0, 0.2 + 0.6 * f + rng.gauss(0, 0.05))), "sensor_summary": summary})
        if t >= next_prompt:
            if rng.random() < 0.9:
                answered = t + rng.uniform(3, 30)
                rows.append(label("kss", answered, kss=ask(t), prompt_ts=t, response_ms=int((answered - t) * 1000)))
            else:
                rows.append(label("prompt_missed", t + 180, prompt_ts=t))
            next_prompt = t + 900
        t += 10
    rows.append(label("session_end", end, kss=ask(end)))
    rows.sort(key=lambda r: r["rx_ts"])
    return rows


def write_synthetic(out_dir: str | Path, *, profiles: int = 6, sessions: int = 4, seed: int = 1) -> Path:
    rng = random.Random(seed)
    out = Path(out_dir)
    day0 = 1_792_800_000.0  # 2026-10 무렵, 시각 특징이 하루 안에서 돌게
    day0 -= day0 % 86400
    for p in range(profiles):
        pid = f"{'ABCDEF'[p % 6]}-s{p:03d}"
        bias, typing = rng.gauss(0, 0.8), rng.uniform(120, 200)
        for s in range(sessions):
            rows = _session(rng, pid, bias, typing, p * sessions + s, day0)
            path = out / pid / f"{rows[0]['session_id']}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="ml/datasets/fatigue/synth")
    ap.add_argument("--profiles", type=int, default=6)
    ap.add_argument("--sessions", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(write_synthetic(a.out, profiles=a.profiles, sessions=a.sessions, seed=a.seed))
