"""DESKMATE 허브 진입점.

    python -m deskmate_hub --replay logs/2026-08-01.jsonl   # 로그 리플레이(임계값 튜닝)
    python -m deskmate_hub --demo                            # 합성 세션 스모크
    python -m deskmate_hub --replay log.jsonl --quiet        # 요약만

실시간 허브 모드(MQTT ingest → engine → control)는 ingest 연결 후 구현한다.
"""
from __future__ import annotations

import argparse
import sys

from .inference import SessionRecorder, format_session_report, load_config
from .replay import format_report, iter_frames, replay


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deskmate_hub", description="DESKMATE 중앙 추론 허브")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--replay", metavar="LOG.jsonl", help="JSONL 센서 로그를 리플레이")
    src.add_argument("--demo", action="store_true", help="합성 세션으로 전체 경로 스모크 실행")
    p.add_argument("--config", metavar="fsm.yaml", help="FSM 설정 경로(기본: 패키지 config)")
    p.add_argument("--quiet", action="store_true", help="전이 트레이스 생략, 요약만 출력")
    p.add_argument("--report", action="store_true", help="세션 작업 리포트도 출력")
    args = p.parse_args(argv)

    cfg = load_config(args.config) if args.config else None

    if args.demo:
        from .demo import demo_frames
        frames = demo_frames()
    elif args.replay:
        try:
            with open(args.replay, encoding="utf-8") as fh:
                frames = list(iter_frames(fh))
        except (OSError, ValueError) as e:
            print(f"리플레이 실패: {e}", file=sys.stderr)
            return 1
    else:
        p.error("--replay 또는 --demo 중 하나가 필요하다 (실시간 허브 모드는 이후 구현)")
        return 2

    recorder = SessionRecorder() if args.report else None
    print(format_report(replay(frames, cfg, recorder=recorder), quiet=args.quiet))
    if recorder is not None:
        print()
        print(format_session_report(recorder.finalize()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
