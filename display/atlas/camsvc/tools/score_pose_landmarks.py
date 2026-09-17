"""보드가 낸 랜드마크를 MediaPipe 정답지와 맞춰 본다.

`deskmate_camsvc --replay` 출력은 보드에서 나오고(호스트에는 TFLite 가 없다)
정답지는 `gen_pose_landmarks_golden.py` 가 PC 에서 만든다. 둘을 여기서 붙인다.

    tools/score_pose_landmarks.py <replay출력> --mode image|video

거리는 랜드마크 좌표(0~1) 그대로다. 160x120 에서 0.01 은 가로로 1.6픽셀쯤 된다.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent / "test" / "pose_landmarks_golden.txt"

# 랜드마크 번호 -> 판정이 부르는 이름. 이 일곱 개만 자세를 정한다.
JUDGED = {
    0: "코",
    11: "왼어깨",
    12: "오른어깨",
    13: "왼팔꿈치",
    14: "오른팔꿈치",
    15: "왼손목",
    16: "오른손목",
}


def parse_golden(path: Path, mode: str) -> list[list[tuple[float, float]] | None]:
    rows: list[list[tuple[float, float]] | None] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] != mode:
            continue
        if parts[2] == "0":
            rows.append(None)
            continue
        rows.append([tuple(float(v) for v in token.split(":")) for token in parts[3:]])
    return rows


def parse_replay(path: Path) -> list[tuple[str, float, float, list[tuple[float, float]] | None]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        # 이름 찾음 검출점수 존재점수 [x:y ...]
        if len(parts) < 4 or ":" in parts[0]:
            continue
        name, found, best, presence = parts[0], parts[1], float(parts[2]), float(parts[3])
        points = (
            [tuple(float(v) for v in token.split(":")) for token in parts[4:]]
            if found == "1"
            else None
        )
        rows.append((name, best, presence, points))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", type=Path)
    parser.add_argument("--mode", choices=["image", "video"], default="video")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    # 0.02 면 160픽셀 폭에서 3픽셀쯤. 모델 자체가 프레임마다 그만큼은 흔들린다.
    parser.add_argument("--tolerance", type=float, default=0.02)
    args = parser.parse_args()

    golden = parse_golden(args.golden, args.mode)
    replay = parse_replay(args.replay)
    if not golden:
        print(f"정답지에 {args.mode} 줄이 없습니다: {args.golden}")
        return 2
    if not replay:
        print(f"채점할 출력이 비었습니다: {args.replay}")
        return 2

    # image 정답지는 한 줄뿐이다. 모든 프레임을 그 한 줄과 견준다.
    if len(golden) == 1:
        golden = golden * len(replay)

    worst_overall = 0.0
    bad_frames = 0
    per_landmark: dict[int, float] = {}

    print(f"{'프레임':<16}{'찾음':>5}{'검출':>8}{'존재':>8}{'판정7 최대':>12}{'전체 최대':>12}")
    for index, (name, best, presence, points) in enumerate(replay):
        want = golden[index] if index < len(golden) else None
        if want is None or points is None:
            state = "정답지 없음" if want is None else "못 찾음"
            print(f"{name:<16}{'-':>5}{best:>8.3f}{presence:>8.3f}{state:>12}")
            bad_frames += 1
            continue

        judged_worst = 0.0
        overall_worst = 0.0
        for landmark, (gx, gy) in enumerate(want):
            if landmark >= len(points):
                break
            distance = math.dist(points[landmark], (gx, gy))
            overall_worst = max(overall_worst, distance)
            if landmark in JUDGED:
                judged_worst = max(judged_worst, distance)
                per_landmark[landmark] = max(per_landmark.get(landmark, 0.0), distance)
        worst_overall = max(worst_overall, judged_worst)
        if judged_worst > args.tolerance:
            bad_frames += 1
        print(
            f"{name:<16}{'예':>5}{best:>8.3f}{presence:>8.3f}"
            f"{judged_worst:>12.4f}{overall_worst:>12.4f}"
        )

    print()
    if per_landmark:
        print("판정이 쓰는 점별 최대 차이")
        for landmark, distance in sorted(per_landmark.items()):
            print(f"  {JUDGED[landmark]:<10}{distance:.4f}")
    print()
    print(f"판정 7점 최대 차이 {worst_overall:.4f} · 허용 {args.tolerance}")
    if bad_frames:
        print(f"어긋난 프레임 {bad_frames}개 — 포팅이 MediaPipe 와 다릅니다")
        return 1
    print("MediaPipe 와 같은 자리에 찍힌다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
