"""여러 세션의 규칙 판정을 지시 자세와 비교해 집계한다(시뮬레이션·실측 공통).

  python evaluate.py data/sessions/sim*
  python evaluate.py data/sessions/sim* --set lean_forward_mm=60 --set nod_amp_mm=60   # 임계값 바꿔 재판정

--set 을 주면 저장된 원본 8×8 을 펌웨어 TofPosture.cpp 에 다시 넣어 판정한다(녹화 다시 안 함).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tof_logger"))
from tofl.protocol import RULE_CLASS  # noqa: E402
from tofl.session import load_session  # noqa: E402


def step_elapsed(data: dict) -> np.ndarray:
    out = np.full(len(data["t_host"]), np.nan)
    key, start = None, 0.0
    for i, (s, ph) in enumerate(zip(data["step"], data["phase"])):
        if ph != "step":
            key = None
            continue
        if key != s:
            key, start = s, data["t_host"][i]
        out[i] = data["t_host"][i] - start
    return out


def commands(folder: Path, t_host: np.ndarray) -> dict[int, str]:
    """events.jsonl 의 명령을 프레임 번호로(그 시각 이후 첫 프레임)."""
    out = {}
    p = folder / "events.jsonl"
    if not p.exists():
        return out
    for line in open(p, encoding="utf-8"):
        e = json.loads(line)
        if e.get("kind") == "cmd":
            i = int(np.searchsorted(t_host, e["t_host"] - 1e-6))
            if i < len(t_host):
                out[i] = e["cmd"]
    return out


def parse_set(items: list[str]) -> dict[str, float]:
    cfg = {}
    for it in items:
        k, _, v = it.partition("=")
        cfg[k.strip()] = float(v)
    return cfg


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--skip", type=float, default=2.0, help="각 단계 처음 이 초는 제외")
    ap.add_argument("--set", action="append", default=[], help="TofPostureConfig 필드=값 (재판정)")
    a = ap.parse_args()
    cfg = parse_set(a.set)
    replay = None
    if cfg:
        from tofsim.rule import RuleReplay

        replay = RuleReplay()

    conf = defaultdict(Counter)
    per_session = []
    for folder in sorted(p for p in a.sessions if (p / "frames.jsonl").exists()):
        meta, data = load_session(folder)
        rule = data["rule_posture"]
        if replay is not None:
            rule = replay.run(data["ms"], data["raw_d"], data["status"], config=cfg,
                              cmds=commands(folder, data["t_host"]))["posture"]
        since = step_elapsed(data)
        truth = np.array([RULE_CLASS.get(p) if p else None for p in data["prompt"]], dtype=object)
        m = np.array([t is not None and np.isfinite(s) and s >= a.skip for t, s in zip(truth, since)])
        for t, r in zip(truth[m], rule[m]):
            conf[t][r] += 1
        acc = float(np.mean(truth[m] == rule[m])) if m.any() else float("nan")
        sim = meta.get("sim") or {}
        per_session.append((folder.name, acc, sim))

    if not per_session:
        print("세션 없음")
        return 1
    labels = [x for x in ("upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy",
                          "away") if x in conf]
    cols = labels + sorted({r for c in conf.values() for r in c} - set(labels))
    total = sum(sum(c.values()) for c in conf.values())
    right = sum(conf[t][t] for t in conf)
    print(f"세션 {len(per_session)}개, 채점 프레임 {total}, 전체 일치 {right / total * 100:.1f}%"
          + (f"  (재판정 {cfg})" if cfg else ""))
    print("  지시\\규칙      " + "".join(c[:9].rjust(10) for c in cols) + "    재현율")
    for t in labels:
        n = sum(conf[t].values())
        print("  " + t.ljust(14) + "".join(
            (f"{conf[t][c] / n * 100:9.0f}%" if conf[t][c] else "         .") for c in cols)
            + f"   {conf[t][t] / n * 100:5.1f}%")
    print("\n세션별 (일치율 · 사람 거리 · 센서 높이(책상 위) · 위쪽 기울기 · 머리카락 반사율)")
    for name, acc, sim in per_session:
        room, mount, person = sim.get("room", {}), sim.get("mount", {}), sim.get("person", {})
        extra = ""
        if room:
            extra = (f"  거리 {room['dist']:.2f} m  높이 {mount['z'] - room['desk_h']:.2f} m  "
                     f"기울기 {mount['pitch']:4.1f}°  머리카락 {person['hair']:.2f}")
        print(f"  {name:32s} {acc * 100:5.1f}%{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
