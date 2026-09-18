#!/usr/bin/env python3
"""스켈레톤 자세 판정의 골든 벡터를 뽑는다. C++ 포팅을 채점하는 기준이다.

    python gen_pose_golden.py <posture_pose.py 가 있는 폴더> <출력.txt>

원본은 `76EHwan/pico_esp32-cam_ftdi` 의 `tools/posture_pose.py` 다. 그 판정이
다른 노트북에서 검증된 것이라, 보드로 옮기면서 **결과가 달라지지 않았다는 증명**이
필요하다. 여기서 입력과 기대 출력을 한 파일로 고정한다.

형식은 C++ 에서 파싱하기 쉬운 줄 단위 텍스트다. JSON 파서를 서비스에 끌어들이지
않으려는 것.

    CASE <이름>
    REF <idx>:<x>:<y> ...
    STEP <t> <idx>:<x>:<y> ... | <label> <candidate> <held> <head_drop> \
         <head_drop_delta> <scale_ratio> <wrist_to_head> <arm_folded>

좌표는 어깨 폭으로 나눠 쓰이므로 단위는 아무거나 상관없다(0~1 정규화 좌표를 쓴다).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

NOSE, L_SHOULDER, R_SHOULDER = 0, 11, 12
L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 13, 14, 15, 16
FPS = 10.0


def pose(*, head_y=0.20, shoulder_y=0.42, half_width=0.09, nose_x=0.50,
         wrist=None, elbow_below=True, arms=True):
    """앉은 사람 하나. 좌표는 0~1.

    half_width 를 줄이면 멀어진 것이다(어깨가 좁아 보인다) - 판정이 젖힘을 가르는
    바로 그 축이다. head_y 를 내리면 고개를 숙인 것이다.
    """
    pts = {
        NOSE: (nose_x, head_y),
        L_SHOULDER: (0.5 - half_width, shoulder_y),
        R_SHOULDER: (0.5 + half_width, shoulder_y),
    }
    if arms:
        pts[L_ELBOW] = (0.5 - half_width - 0.02,
                        shoulder_y + (0.12 if elbow_below else -0.10))
        pts[R_ELBOW] = (0.5 + half_width + 0.02,
                        shoulder_y + (0.12 if elbow_below else -0.10))
        left = wrist if wrist else (0.5 - half_width - 0.04, shoulder_y + 0.24)
        pts[L_WRIST] = left
        pts[R_WRIST] = (0.5 + half_width + 0.04, shoulder_y + 0.24)
    return pts


def hold(frames, pts_fn, seconds):
    for i in range(int(seconds * FPS)):
        frames.append(pts_fn(i / FPS))


CASES = {}


def case(name):
    def wrap(fn):
        frames = []
        fn(frames)
        CASES[name] = frames
        return fn
    return wrap


@case("upright")
def _(frames):
    hold(frames, lambda s: pose(), 4.0)


@case("slump")
def _(frames):
    hold(frames, lambda s: pose(), 2.0)
    # 코가 어깨선까지 내려온다. 어깨 폭은 그대로 - 접힌 것이지 멀어진 게 아니다.
    hold(frames, lambda s: pose(head_y=0.40), 4.0)


@case("recline")
def _(frames):
    hold(frames, lambda s: pose(), 2.0)
    # 어깨가 좁아진다 = 멀어졌다. 머리도 조금 내려간다(목 단축).
    hold(frames, lambda s: pose(half_width=0.070, head_y=0.26), 4.0)


@case("chin_rest")
def _(frames):
    hold(frames, lambda s: pose(), 2.0)
    # 손목이 머리 옆으로 오고 팔꿈치는 어깨 아래 - 턱 괴기.
    hold(frames, lambda s: pose(head_y=0.26, wrist=(0.45, 0.24)), 4.0)


@case("hand_wave")
def _(frames):
    # 손을 들어 흔들면 손목이 머리 근처를 지나지만 팔꿈치가 위에 있다.
    hold(frames, lambda s: pose(), 1.5)
    hold(frames, lambda s: pose(wrist=(0.45, 0.22), elbow_below=False), 3.0)


@case("absent")
def _(frames):
    hold(frames, lambda s: pose(), 1.5)
    for _ in range(20):
        frames.append({})


@case("nod_then_slump")
def _(frames):
    # 꾸벅임 한 번은 hold 타이머가 삼켜야 하고, 이어지는 진짜 엎드림은 잡아야 한다.
    hold(frames, lambda s: pose(), 2.0)
    hold(frames, lambda s: pose(head_y=0.40), 0.8)
    hold(frames, lambda s: pose(), 1.5)
    hold(frames, lambda s: pose(head_y=0.40), 4.0)


@case("recline_then_back")
def _(frames):
    hold(frames, lambda s: pose(), 2.0)
    hold(frames, lambda s: pose(half_width=0.070, head_y=0.26), 3.0)
    hold(frames, lambda s: pose(), 3.0)


def fmt_pts(pts):
    return " ".join(f"{i}:{x:.6f}:{y:.6f}" for i, (x, y) in sorted(pts.items()))


def fmt_num(v):
    if v != v:
        return "nan"
    if v == math.inf:
        return "inf"
    return f"{v:.6f}"


def main(src_dir: str, out_path: str) -> int:
    sys.path.insert(0, src_dir)
    from posture_pose import PostureTracker  # noqa: E402

    lines = ["# tools/gen_pose_golden.py 가 만든다. 손으로 고치지 말 것.",
             "# 원본: 76EHwan/pico_esp32-cam_ftdi tools/posture_pose.py"]
    for name, frames in CASES.items():
        tracker = PostureTracker()
        reference = pose()
        if not tracker.capture_reference(reference):
            raise SystemExit(f"{name}: 기준 자세를 못 잡았다")
        lines.append(f"CASE {name}")
        lines.append(f"REF {fmt_pts(reference)}")
        t = 0.0
        for pts in frames:
            t += 1.0 / FPS
            state = tracker.update(pts, t)
            m = state.metrics
            lines.append(
                # 시각은 자르지 않는다. hold 타이머가 1.5초 경계에서 갈리므로
                # 소수 6자리로 줄이면 한 프레임이 통째로 어긋난다.
                f"STEP {t!r} {fmt_pts(pts)} | {state.label} {state.candidate} "
                f"{state.held_for:.6f} {fmt_num(m.head_drop)} "
                f"{fmt_num(m.head_drop_delta)} {fmt_num(m.scale_ratio)} "
                f"{fmt_num(m.wrist_to_head)} {1 if m.arm_folded else 0}")
        print(f"{name:18s} {len(frames):4d}프레임")
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    steps = sum(1 for line in lines if line.startswith("STEP"))
    print(f"\n{out_path} — {len(CASES)}개 시나리오 · {steps}프레임")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
