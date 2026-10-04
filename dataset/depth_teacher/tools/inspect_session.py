"""세션 CSV 요약: 프레임 수, 유효 비율, 특징값 통계, 랜드마크 깊이 성공률.

  python tools/inspect_session.py data/sessions/<세션>
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from posture.landmarks import POINT_NAMES  # noqa: E402

sdir = Path(sys.argv[1])
csv = sdir / "frames_relabeled.csv" if (sdir / "frames_relabeled.csv").exists() else sdir / "frames.csv"
df = pd.read_csv(csv)
dur = df.t_host.iloc[-1] - df.t_host.iloc[0]
print(f"{len(df)} frames, {len(df) / max(dur, 1e-6):.1f} fps, valid {df.valid.mean():.0%}")
print("phase:", df.phase.value_counts().to_dict())
cols = ["head_dist", "head_fwd", "neck_angle", "head_pitch", "nose_drop", "sh_tilt", "sh_width", "head_roll"]
print(df[cols].describe().loc[["count", "mean", "std", "min", "max"]].round(3).to_string())
print("depth 성공률:", {n: round(df[f"{n}_z"].notna().mean(), 2) for n in POINT_NAMES})
run = df[df.phase != "calib"]
if "posture" in run and run.posture.notna().any():
    print("posture:", run.posture.value_counts().to_dict())
    print("drowsy 비율:", round(run.drowsy.mean(), 3))
