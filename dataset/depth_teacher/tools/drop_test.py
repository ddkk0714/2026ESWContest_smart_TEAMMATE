"""프레임을 건너뛸 때 MediaPipe 시각 전달 방식에 따라 측정 성공률이 어떻게 달라지는지 비교.

실시간 실행은 처리가 느리면 프레임을 건너뛴다(10-04 2차 녹화+영상 저장 시 21fps).
녹화본에서 3프레임 중 1개를 일부러 버리고, 같은 프레임을 두 방식으로 처리한다.
  old: 처리한 프레임 번호 x 33ms (예전 코드)
  new: 카메라 프레임 시각 (현재 코드)

  python tools/drop_test.py data/sessions/<세션> [최대 프레임 수]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from posture.app import load_config  # noqa: E402
from posture.camera import RealSenseCamera, orientation_from_up  # noqa: E402
from posture.geometry import WorldFrame, compute_features  # noqa: E402
from posture.landmarks import LandmarkDetector, lift_to_3d  # noqa: E402

src = Path(sys.argv[1])
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
cfg = load_config()
meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
rot, up = orientation_from_up(meta.get("up_raw"))
wf = WorldFrame(up or [0.0, -1.0, 0.0])
cam = RealSenseCamera(bag_in=src / "raw.db3", rotate180=rot)
old, new = LandmarkDetector(cfg, ROOT), LandmarkDetector(cfg, ROOT)
stats = {"old": [0, 0], "new": [0, 0]}
missing = {"old": {}, "new": {}}
k = 0
while True:
    fr = cam.read()
    if fr is None or fr.index >= limit:
        break
    if fr.index % 3 == 2:  # 3개 중 1개 버림 -> 약 20fps
        continue
    for name, det, ts in (("old", old, k * 1000 / 30), ("new", new, fr.t_dev)):
        if name == "old":
            det._t0 = 0.0  # 예전 방식: 번호 기반 시각을 그대로 쓴다
        pts = det.detect(fr.color, ts)
        P = lift_to_3d(pts, fr.depth_m, cam.intrinsics, cfg)
        ok = compute_features({n: wf.to_world(v) for n, v in P.items()}) is not None
        stats[name][0] += ok
        stats[name][1] += 1
        for n in ("sh_l", "sh_r", "nose", "forehead", "chin"):
            if n not in pts:
                missing[name][n] = missing[name].get(n, 0) + 1
    k += 1
cam.stop()
for name in ("old", "new"):
    good, total = stats[name]
    miss = {n: f"{c / total:.1%}" for n, c in missing[name].items()}
    print(f"{name}: 측정 성공 {good / total:.1%} ({good}/{total})  2D 누락 {miss}")
