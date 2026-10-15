"""세션 점검: 프레임 품질, 규칙 판정 vs 지시 자세, 자세별 평균 거리맵(방향 확인).

  python inspect_session.py data/sessions/<세션>
  python inspect_session.py data/sessions/<세션> --flip-cols 0     # 방향을 바꿔 다시 보기
  python inspect_session.py data/sessions/<세션> --npz out.npz      # 학습용 배열로 내보내기
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from tofl.protocol import RULE_CLASS
from tofl.session import export_npz, load_session


def step_elapsed(data: dict) -> np.ndarray:
    """프레임마다 '지금 단계(step 구간)가 시작된 뒤 지난 초'. 단계 밖이면 nan."""
    out = np.full(len(data["t_host"]), np.nan)
    in_step = data["phase"] == "step"
    start, key = None, None
    for i, (s, ok) in enumerate(zip(data["step"], in_step)):
        if not ok:
            key = None
            continue
        if key != s:
            key, start = s, data["t_host"][i]
        out[i] = data["t_host"][i] - start
    return out


def rule_report(data: dict, skip_s: float) -> str:
    since = step_elapsed(data)
    rows = []
    mask = np.zeros(len(since), dtype=bool)
    truth = np.array([RULE_CLASS.get(p) if p else None for p in data["prompt"]], dtype=object)
    for i in range(len(since)):
        mask[i] = truth[i] is not None and np.isfinite(since[i]) and since[i] >= skip_s
    if not mask.any():
        return "규칙과 비교할 지시 자세 구간이 없다(자유 기록이거나 프로토콜이 너무 짧음)."
    t, r = truth[mask], data["rule_posture"][mask]
    labels = sorted(set(t) | set(r), key=lambda x: (x not in RULE_CLASS.values(), x))
    rows.append(f"규칙 판정 vs 지시 자세 (각 단계 처음 {skip_s:g} s 제외, {mask.sum()} 프레임)")
    rows.append(f"  전체 일치 {np.mean(t == r) * 100:5.1f}%")
    width = max(len(x) for x in labels) + 1
    rows.append("  " + "지시\\규칙".ljust(width) + "".join(x[:9].rjust(10) for x in labels))
    for a in labels:
        if a not in set(t):
            continue
        sel = t == a
        cnt = Counter(r[sel])
        rows.append("  " + a.ljust(width) + "".join(
            (f"{cnt.get(b, 0) / sel.sum() * 100:9.0f}%" if cnt.get(b) else "         .")
            for b in labels) + f"   ({sel.sum()})")
    others = Counter(p for p in data["prompt"][np.isfinite(since)] if p and RULE_CLASS.get(p) is None)
    if others:
        rows.append("  규칙에 없는 자세(학습용으로만): "
                    + ", ".join(f"{k} {v}" for k, v in sorted(others.items())))
    return "\n".join(rows)


def ascii_map(m: np.ndarray) -> str:
    """거리맵(mm)을 cm 로. 무효는 ' --'."""
    lines = []
    for row in m:
        lines.append(" ".join(" --" if not np.isfinite(v) else f"{v / 10:3.0f}" for v in row))
    return "\n".join("    " + x for x in lines)


def mean_maps(data: dict, skip_s: float) -> str:
    since = step_elapsed(data)
    out = ["자세별 평균 거리(cm, 장면 방향: 위 = 천장, 왼쪽 = 센서가 바라볼 때 왼쪽)",
           "  바른 자세에서 머리가 위쪽 가운데에 가까운 값으로 보여야 방향이 맞다.",
           "  턱괴기 '왼손'은 사람 기준 왼쪽 = 센서에서 보면 오른쪽 열에 팔 기둥이 생긴다."]
    steps = sorted({int(s) for s in data["step"][np.isfinite(since)]})
    for s in steps:
        sel = (data["step"] == s) & np.isfinite(since) & (since >= skip_s)
        if not sel.any():
            continue
        with np.errstate(all="ignore"):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                m = np.nanmedian(data["dist"][sel], axis=0)
        out.append(f"\n  [{s + 1}] {data['prompt'][sel][0]}  ({sel.sum()} 프레임)")
        out.append(ascii_map(m))
    return "\n".join(out)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("session", type=Path)
    ap.add_argument("--skip", type=float, default=2.0, help="각 단계 처음 이 초는 채점 제외(전환 반응)")
    ap.add_argument("--flip-rows", type=int, choices=(0, 1), help="meta 의 방향 대신 이 값")
    ap.add_argument("--flip-cols", type=int, choices=(0, 1))
    ap.add_argument("--no-maps", action="store_true", help="자세별 평균 거리맵 생략")
    ap.add_argument("--npz", type=Path, help="학습용 배열로 내보낼 경로")
    a = ap.parse_args()
    fr = None if a.flip_rows is None else bool(a.flip_rows)
    fc = None if a.flip_cols is None else bool(a.flip_cols)
    meta, data = load_session(a.session, flip_rows=fr, flip_cols=fc)

    n = len(data["t_host"])
    dur = data["t_host"][-1] - data["t_host"][0] if n > 1 else 0.0
    dev_ms = np.diff(data["ms"])
    stats = meta.get("stats", {})
    print(f"세션 {a.session.name}  ({meta.get('source')}, 메모: {meta.get('note') or '-'})")
    print(f"  장비 {meta.get('device')}")
    print(f"  방향 {meta['orientation_used']}")
    print(f"  프레임 {n}, {dur:.1f} s, 평균 {n / dur if dur else 0:.1f} fps, "
          f"프레임 간격 중앙값 {np.median(dev_ms) if len(dev_ms) else 0:.0f} ms (장비 시계)")
    print(f"  seq 갭 {stats.get('seq_gaps')} (빠진 프레임 {stats.get('missing_frames')}), "
          f"센서 재부팅 {stats.get('seq_resets')}, 센서 오류 {stats.get('errors')}, "
          f"종료 {meta.get('end_reason')}")
    valid = np.isfinite(data["dist"])
    print(f"  유효 zone {valid.mean() * 100:.1f}%  (행별 "
          + " ".join(f"{v * 100:.0f}" for v in valid.mean(axis=(0, 2))) + ")")
    print(f"  규칙 기준선 확보 프레임 {data['rule_baseline'].mean() * 100:.0f}%")
    print()
    print(rule_report(data, a.skip))
    if not a.no_maps and (data["phase"] == "step").any():
        print()
        print(mean_maps(data, a.skip))
    if a.npz:
        out = export_npz(a.session, a.npz, flip_rows=fr, flip_cols=fc)
        print(f"\n내보냄: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
