"""프로토콜 녹화본(raw.db3)을 현재 코드로 다시 처리하고, 원래 세션의 정답으로 평가.

랜드마크 추출·깊이 샘플링(landmarks.py)을 바꿨을 때 쓴다. 임계값·특징 계산만 바꿨으면
relabel.py 가 훨씬 빠르다.

  python tools/replay_eval.py data/sessions/<프로토콜 세션> [<이미 재처리한 세션>]

정답(phase, gt_*)은 재처리 결과와 프레임 타임스탬프(t_dev)로 맞춘다. 기준 자세도 원래
세션의 calib 구간 프레임으로 다시 잡으므로 원래 평가와 같은 조건이다.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evaluate import evaluate  # noqa: E402
from posture.app import load_config, run_session  # noqa: E402
from relabel import relabel  # noqa: E402

src = Path(sys.argv[1]).resolve()
cfg = load_config()
user = src.name.split("_")[0]
if len(sys.argv) > 2:  # 이미 재처리한 세션 폴더를 주면 정답 매칭·평가만 다시 한다
    out = Path(sys.argv[2]).resolve()
else:
    out = run_session(cfg, f"{user}replay", bag_in=src / "raw.db3", display=False, tag="replay")

orig = pd.read_csv(src / "frames.csv", usecols=["t_dev", "phase", "gt_posture", "gt_drowsy"])
new = pd.read_csv(out / "frames.csv").drop(columns=["phase", "gt_posture", "gt_drowsy"],
                                            errors="ignore")
merged = pd.merge_asof(new.sort_values("t_dev"), orig.sort_values("t_dev"), on="t_dev",
                       direction="nearest", tolerance=200.0)  # 실시간 실행이 건너뛴 프레임도 가까운 정답으로
print(f"정답 매칭: {merged.phase.notna().mean():.1%} 프레임")
merged.to_csv(out / "frames.csv", index=False)
evaluate(relabel(out, cfg))
print(f"재처리 세션: {out}")
