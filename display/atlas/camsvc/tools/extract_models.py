"""`pose_landmarker_full.task` 에서 모델 두 개를 꺼내 `models/` 에 둔다.

`.task` 는 그냥 zip 이고 안에 `pose_detector.tflite` 와
`pose_landmarks_detector.tflite` 가 들어 있다. 이 둘은 **커밋하지 않는다**(팀 규칙:
학습 모델 커밋 금지). 그래서 빌드·설치 전에 각자 한 번 꺼낸다.

    python tools/extract_models.py [.task 경로]

경로를 안 주면 `thermal-pose/pose_landmarker_full.task` 를 찾는다. 받는 곳은
https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAMSVC = HERE.parent
WANTED = ("pose_detector.tflite", "pose_landmarks_detector.tflite")


def find_task() -> Path:
    for parent in [CAMSVC, *CAMSVC.parents]:
        for candidate in (
            parent / "thermal-pose" / "pose_landmarker_full.task",
            parent.parent / "thermal-pose" / "pose_landmarker_full.task",
        ):
            if candidate.is_file():
                return candidate
    raise SystemExit(
        "pose_landmarker_full.task 를 못 찾았습니다. 경로를 인자로 주거나 "
        "thermal-pose/ 에 두세요."
    )


def main() -> int:
    task = Path(sys.argv[1]) if len(sys.argv) > 1 else find_task()
    if not task.is_file():
        raise SystemExit(f"파일이 없습니다: {task}")

    out = CAMSVC / "models"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(task) as archive:
        names = set(archive.namelist())
        missing = [name for name in WANTED if name not in names]
        if missing:
            raise SystemExit(
                f"{task.name} 안에 {', '.join(missing)} 가 없습니다. "
                f"들어 있는 것: {sorted(names)}"
            )
        for name in WANTED:
            data = archive.read(name)
            (out / name).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()[:16]
            print(f"  {name:34s} {len(data):>9,} bytes  {digest}")

    print(f"\n{task} -> {out}")
    print("보드에 넣기: README 의 '설치' 참고")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
