"""MediaPipe 가 내는 랜드마크로 정답지를 만든다. C++ 포팅 채점용.

판정(`posture_pose`)·프레임 디코더·기하는 각자 정답지로 채점했지만, **모델 앞뒤
접착부**(앵커·디코딩·NMS·ROI·크롭·역투영)만은 실제 모델을 돌려 봐야 맞는지 안다.
접착부가 어긋나도 모델은 오류 없이 돌고 좌표만 조용히 틀어지기 때문이다.

입력은 **책상 카메라 영상이 아니라** matplotlib 이 들고 다니는 공개 사진
(`grace_hopper.jpg`, 미 해군 공개 자료)이다. 사람이 찍힌 진짜 사진이어야 포즈
모델이 돌고, 사적인 영상을 보드 밖으로 내보내지 않아도 된다. 카메라 입력과 같은
조건으로 맞추려고 160x120 흑백으로 줄여서 쓴다.

두 가지를 만든다.

  image  IMAGE 모드 한 장. 매 프레임 검출기를 도는 경로를 본다.
  video  같은 장면 20번 + **사람 없는 장면 20번**. VIDEO 모드라 추적 경로를 본다.
         앞의 20장에서 ROI 는 한 점에 수렴해야 하고, 뒤의 20장에서는 **사람을 놓아야
         한다.** 뒤쪽이 중요하다 - 랜드마크 모델은 크롭 안에 사람이 없어도 무언가는
         반드시 찍기 때문에, 존재 점수만 믿으면 판정이 "바른 자세" 로 굳는다.
         실기에서 그 버그를 봤고, 그래서 이 20장이 정답지에 있다.

    cd deskmate-app/display/atlas/camsvc
    ../../../../thermal-pose/.venv/Scripts/python.exe tools/gen_pose_landmarks_golden.py

채점은 보드에서 한다(호스트에는 TFLite 가 없다):

    deskmate_camsvc --replay test/frames            # 추적 켜짐 -> video 와 비교
    CAMSVC_NO_TRACKING=1 deskmate_camsvc --replay …  # 추적 꺼짐 -> image 와 비교
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

HERE = Path(__file__).resolve().parent
CAMSVC = HERE.parent


def find_thermal_pose() -> Path:
    """`thermal-pose` 를 위로 올라가며 찾는다. 이 레포는 git worktree 로도 쓰여서
    상대 깊이가 고정이 아니다."""
    for parent in [CAMSVC, *CAMSVC.parents]:
        candidate = parent / "thermal-pose"
        if candidate.is_dir():
            return candidate
        sibling = parent.parent / "thermal-pose"
        if sibling.is_dir():
            return sibling
    raise SystemExit("thermal-pose 폴더를 못 찾았습니다 (포즈 모델과 venv 가 거기 있습니다)")


THERMAL = find_thermal_pose()

# 카메라가 보내는 preview 와 같은 크기. 여기를 바꾸면 정답지도 다시 만들어야 한다.
WIDTH, HEIGHT = 160, 120
VIDEO_FRAMES = 20
BLANK_FRAMES = 20
FRAME_MS = 200  # preview 가 약 5fps 로 온다


def find_photo() -> Path:
    """사람이 찍힌 공개 사진. 이 PC 에 이미 있는 것만 쓴다."""
    candidates = [
        THERMAL / ".venv/Lib/site-packages/matplotlib/mpl-data/sample_data/grace_hopper.jpg",
        Path(sys.prefix) / "Lib/site-packages/matplotlib/mpl-data/sample_data/grace_hopper.jpg",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit(
        "표본 사진을 못 찾았습니다. matplotlib 의 grace_hopper.jpg 가 필요합니다:\n  "
        + "\n  ".join(str(p) for p in candidates)
    )


def write_pgm(path: Path, grey: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as file:
        file.write(b"P5\n%d %d\n255\n" % (grey.shape[1], grey.shape[0]))
        file.write(grey.tobytes())


def read_pgm(path: Path) -> np.ndarray:
    """C++ 이 읽는 것과 **같은 바이트**를 읽는다. 원본 jpg 를 다시 줄이면 안 된다 -
    리사이즈 구현이 조금만 달라도 입력이 달라져 채점이 무의미해진다."""
    data = path.read_bytes()
    fields: list[bytes] = []
    offset = 0
    while len(fields) < 4:
        while offset < len(data) and data[offset : offset + 1].isspace():
            offset += 1
        start = offset
        while offset < len(data) and not data[offset : offset + 1].isspace():
            offset += 1
        fields.append(data[start:offset])
    offset += 1  # 헤더 뒤 공백 하나
    width, height = int(fields[1]), int(fields[2])
    return np.frombuffer(data, dtype=np.uint8, count=width * height, offset=offset).reshape(
        height, width
    )


def empty_desk(index: int) -> np.ndarray:
    """사람이 없는 책상. 새까만 화면은 너무 쉬워서 시험이 안 된다 - 완만한 밝기
    기울기와 약한 잡음을 넣어 실제 프레임과 비슷한 통계를 준다."""
    rng = np.random.default_rng(7 + index)
    rows = np.linspace(90, 150, HEIGHT)[:, None]
    cols = np.linspace(-12, 12, WIDTH)[None, :]
    noise = rng.normal(0, 4, (HEIGHT, WIDTH))
    return np.clip(rows + cols + noise, 0, 255).astype(np.uint8)


def as_mp_image(grey: np.ndarray) -> mp.Image:
    # 카메라가 흑백이라 세 채널에 같은 값을 넣는다. C++ 쪽도 똑같이 한다.
    rgb = cv2.cvtColor(grey, cv2.COLOR_GRAY2RGB)
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))


def landmark_line(tag: str, index: int, result) -> str:
    poses = result.pose_landmarks
    if not poses:
        return f"{tag} {index} 0"
    # 판정이 쓰는 33개만 적는다. 보조점 2개는 MediaPipe 가 안 내준다.
    values = " ".join(f"{lm.x:.6f}:{lm.y:.6f}" for lm in poses[0][:33])
    return f"{tag} {index} 1 {values}"


def main() -> int:
    model = THERMAL / "pose_landmarker_full.task"
    if not model.is_file():
        raise SystemExit(f"포즈 모델이 없습니다: {model}")

    photo = find_photo()
    colour = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if colour is None:
        raise SystemExit(f"사진을 못 읽습니다: {photo}")
    grey = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
    grey = cv2.resize(grey, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)

    frames_dir = CAMSVC / "test" / "frames"
    # --replay 가 이름순으로 읽으므로 같은 장면을 번호만 바꿔 여러 장 둔다.
    for index in range(VIDEO_FRAMES):
        write_pgm(frames_dir / f"frame_{index:04d}.pgm", grey)
    for index in range(BLANK_FRAMES):
        write_pgm(frames_dir / f"frame_{VIDEO_FRAMES + index:04d}.pgm",
                  empty_desk(index))
    same = read_pgm(frames_dir / "frame_0000.pgm")
    if not np.array_equal(same, grey):
        raise SystemExit("PGM 왕복이 깨졌습니다")
    blanks = [read_pgm(frames_dir / f"frame_{VIDEO_FRAMES + i:04d}.pgm")
              for i in range(BLANK_FRAMES)]

    lines = [
        "# MediaPipe pose_landmarker_full 이 낸 정답지.",
        f"# 입력: {photo.name} -> {WIDTH}x{HEIGHT} 흑백 (test/frames/*.pgm 과 같은 바이트)",
        "# 형식: <모드> <프레임번호> <찾음> [x:y ...33개]",
        "",
    ]

    base = BaseOptions(model_asset_path=str(model))

    with vision.PoseLandmarker.create_from_options(
        vision.PoseLandmarkerOptions(
            base_options=base,
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    ) as landmarker:
        result = landmarker.detect(as_mp_image(same))
        lines.append(landmark_line("image", 0, result))

    with vision.PoseLandmarker.create_from_options(
        vision.PoseLandmarkerOptions(
            base_options=base,
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    ) as landmarker:
        for index in range(VIDEO_FRAMES):
            result = landmarker.detect_for_video(as_mp_image(same), index * FRAME_MS)
            lines.append(landmark_line("video", index, result))
        # 여기서 사람이 나간다. 이어지는 줄이 전부 0 이어야 정상이다.
        for index in range(BLANK_FRAMES):
            step = VIDEO_FRAMES + index
            result = landmarker.detect_for_video(
                as_mp_image(blanks[index]), step * FRAME_MS)
            lines.append(landmark_line("video", step, result))

    golden = CAMSVC / "test" / "pose_landmarks_golden.txt"
    golden.write_text("\n".join(lines) + "\n", encoding="utf-8")

    found = sum(1 for line in lines if line.startswith(("image", "video")) and " 1 " in line)
    left = sum(1 for line in lines
               if line.startswith("video ") and " 0" == line[-2:]
               and int(line.split()[1]) >= VIDEO_FRAMES)
    print(f"{photo.name} -> {WIDTH}x{HEIGHT}")
    print(f"사람 {VIDEO_FRAMES}장 + 빈 책상 {BLANK_FRAMES}장 · 포즈를 찾은 줄 {found}개")
    print(f"사람이 나간 뒤 놓은 프레임 {left}/{BLANK_FRAMES}장")
    print(f"정답지: {golden}")
    print(f"프레임: {frames_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
