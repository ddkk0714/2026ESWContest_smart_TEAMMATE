"""자세 확인: 장면(옆·정면)과 가상 L8 8×8 거리맵을 한 장의 PNG 로, 또는 MuJoCo 뷰어로.

  python view.py --png poses.png                      # 자세 전부, 기본 사람·방·설치
  python view.py --png chin.png --labels chin_rest --seed 3 --pitch 20
  python view.py --viewer --labels face_down          # 마우스로 돌려 보기
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tof_logger"))
from tofl.protocol import LABELS  # noqa: E402

from tofsim.l8 import L8, Mount  # noqa: E402
from tofsim.pose import chair_tilt_for, sample_pose  # noqa: E402
from tofsim.scene import Room, build_scene  # noqa: E402


def heat(d: np.ndarray, size: int) -> np.ndarray:
    """8×8 mm → RGB 타일. 가까울수록 밝은 노랑, 무효는 검정."""
    v = np.clip((d.astype(float) - 200) / 1600, 0, 1)
    rgb = np.stack([1 - 0.3 * v, 1 - 0.8 * v, 0.25 + 0.5 * v], axis=-1)
    rgb[d < 0] = 0
    img = np.kron(rgb, np.ones((size // d.shape[0], size // d.shape[1], 1)))
    return (img * 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--labels", nargs="*", default=[x for x in LABELS if x != "away"])
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--dist", type=float, default=0.62)
    ap.add_argument("--mount-z", type=float, default=0.15, help="책상 위 센서 높이(m)")
    ap.add_argument("--pitch", type=float, default=10.0)
    ap.add_argument("--png", type=Path)
    ap.add_argument("--viewer", action="store_true")
    a = ap.parse_args()
    room = Room(dist=a.dist, desk_edge=a.dist - 0.22)
    sc = build_scene(r=room)
    l8 = L8(Mount(z=room.desk_h + a.mount_z, pitch=a.pitch))
    rng = np.random.default_rng(a.seed)

    def pose(label: str) -> None:
        side = "right" if label.endswith(":right") else "left"
        q = sample_pose(sc, label.split(":")[0], np.random.default_rng(a.seed), chin_side=side)
        sc.set_q(q)
        sc.set_chair_tilt(chair_tilt_for(q, room.chair_tilt_deg))

    if a.viewer:
        from mujoco import viewer

        pose(a.labels[0])
        viewer.launch(sc.model, sc.data)
        return 0
    if not a.png:
        ap.error("--png 또는 --viewer")
    from PIL import Image, ImageDraw

    size = 240
    r = mujoco.Renderer(sc.model, size, size)
    cols = []
    for lab in a.labels:
        pose(lab)
        tiles = []
        for az, el in ((90, -5), (15, -10)):
            cam = mujoco.MjvCamera()
            cam.lookat[:] = [room.dist - 0.05, 0, room.desk_h + 0.25]
            cam.distance, cam.azimuth, cam.elevation = 1.5, az, el
            r.update_scene(sc.data, camera=cam)
            tiles.append(r.render())
        d, _ = l8.measure(sc, True, rng)
        tiles.append(heat(d, size))
        im = Image.fromarray(np.concatenate(tiles, axis=0))
        ImageDraw.Draw(im).text((4, 4), lab, fill=(255, 255, 0))
        cols.append(np.asarray(im))
    Image.fromarray(np.concatenate(cols, axis=1)).save(a.png)
    print(a.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
