"""검증 프로토콜: 화면 안내대로 자세를 취하면 정답 라벨(gt_*)이 함께 기록되고, 끝나면 정확도를 평가.

  python protocol.py --user soyeon --record-bag --video
"""
import argparse

from evaluate import evaluate
from posture.app import Schedule, load_config, run_session


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--config")
    ap.add_argument("--record-bag", action="store_true", help="임계값 바꿔 재처리하려면 권장")
    ap.add_argument("--video", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    sdir = run_session(cfg, a.user, record_bag=a.record_bag, save_video=a.video,
                       schedule=Schedule(cfg), tag="protocol")
    evaluate(sdir / "frames.csv")


if __name__ == "__main__":
    main()
