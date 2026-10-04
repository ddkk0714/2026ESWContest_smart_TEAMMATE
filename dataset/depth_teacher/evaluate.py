"""프로토콜 세션 평가: 정답(gt_*) vs 파이프라인 라벨.

  python evaluate.py data/sessions/<세션>/frames.csv
"""
import argparse
from pathlib import Path

import pandas as pd

from posture.labeler import FLAGS


def evaluate(csv_path, skip_s=2.0):
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)
    # 단계 번호: 전환(transition) 구간이 끝날 때마다 새 단계
    df["step_id"] = ((df.phase == "step") & (df.phase.shift() != "step")).cumsum()
    st = df[df.phase == "step"].copy()
    if st.empty:
        print("평가할 프로토콜 구간이 없습니다.")
        return None
    # 안내를 보고 자세를 바꾸는 데 걸리는 시간은 채점하지 않는다 (사람 반응이 늦을 수 있음)
    t = st.t_host
    st = st[t - t.groupby(st.step_id).transform("min") >= skip_s]
    lines = [f"(각 단계 처음 {skip_s:g}초 제외)"]
    invalid = (st.valid != 1).mean()
    lines.append(f"측정 불가 프레임 비율: {invalid:.1%}")

    p = st[st.gt_posture.notna() & (st.valid == 1)]
    if not p.empty:
        acc = (p.gt_posture == p.posture).mean()
        lines.append(f"\n[자세 단일 라벨] 프레임 정확도 {acc:.1%}  (n={len(p)})")
        cm = pd.crosstab(p.gt_posture, p.posture, normalize="index").round(2)
        lines.append("혼동행렬 (행=정답, 열=예측, 행 정규화)\n" + cm.to_string())

        lines.append("\n[플래그 재현율] 해당 자세 구간에서 그 플래그가 켜진 비율")
        for g in sorted(set(p.gt_posture) - {"normal"}):
            if g in FLAGS:
                lines.append(f"  {g:13s} {p.loc[p.gt_posture == g, g].mean():.1%}")
        n = p[p.gt_posture == "normal"]
        if not n.empty:
            lines.append("\n[오탐] 정상 자세 구간에서 각 플래그가 켜진 비율")
            for k in FLAGS:
                lines.append(f"  {k:13s} {n[k].mean():.1%}")

    d = st[st.gt_drowsy.notna() & (st.valid == 1)]
    if not d.empty:
        gt, pr = d.gt_drowsy.astype(int), d.drowsy.fillna(0).astype(int)
        tp = int(((gt == 1) & (pr == 1)).sum())
        rec = tp / max(int((gt == 1).sum()), 1)
        prec = tp / max(int((pr == 1).sum()), 1)
        lines.append(f"\n[졸음] 프레임 재현율 {rec:.1%}, 정밀도 {prec:.1%}"
                     f"  (졸음 구간 최대 끄덕임 수 {d.loc[gt == 1, 'nods_window'].max()})")

    report = "\n".join(lines)
    print(report)
    (csv_path.parent / f"report_{csv_path.stem}.txt").write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--skip", type=float, default=2.0, help="각 단계 앞에서 채점하지 않을 초")
    a = ap.parse_args()
    evaluate(a.csv, a.skip)
