"""실시간 자세/졸음 판단 + 라벨 기록.

  python run.py --user soyeon                 # 저장된 기준 자세 사용 (없으면 준비 5초 + 측정 8초)
  python run.py --user soyeon --recalibrate   # 기준 자세 다시 측정
  python run.py --user soyeon --record-bag    # 원본 RGB-D(.db3)도 저장 (용량 큼, 약 2.7GB/분)
  python run.py --user soyeon --bag data/sessions/.../raw.db3   # 녹화본 재처리
"""
import argparse

from posture.app import load_config, run_session


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--config")
    ap.add_argument("--bag", help="재생할 녹화 파일 (.db3, 구버전 .bag)")
    ap.add_argument("--record-bag", action="store_true")
    ap.add_argument("--video", action="store_true", help="검토용 컬러 영상(mp4) 저장")
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--no-display", action="store_true")
    ap.add_argument("--seconds", type=float, help="이 시간 후 자동 종료")
    a = ap.parse_args()
    sdir = run_session(load_config(a.config), a.user, bag_in=a.bag, record_bag=a.record_bag,
                       save_video=a.video, recalibrate=a.recalibrate, display=not a.no_display,
                       tag="replay" if a.bag else "live", max_seconds=a.seconds)
    print(f"저장 완료: {sdir}")


if __name__ == "__main__":
    main()
