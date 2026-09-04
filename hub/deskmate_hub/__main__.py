"""DESKMATE Pi 4 hub command line entry point.

    python -m deskmate_hub --replay logs/session.jsonl
    python -m deskmate_hub --demo
    python -m deskmate_hub demo --host 0.0.0.0 --port 8765

The positional ``demo`` command runs the Atlas preview API. ``--demo`` keeps
the deterministic replay smoke test provided by the hub replay workflow.
"""
from __future__ import annotations

import argparse
import sys

from .inference import load_config
from .replay import format_report, iter_frames, replay


def _run_preview(argv: list[str]) -> int:
    from .demo import run_demo

    parser = argparse.ArgumentParser(
        prog="deskmate_hub demo",
        description="합성 센서 입력으로 FSM과 Atlas 화면 API 실행",
    )
    parser.add_argument("--host", default="0.0.0.0", help="수신 주소 (기본: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="미리보기 API 포트")
    parser.add_argument("--interval", type=float, default=1.0, help="상태 간 실제 대기 초")
    parser.add_argument("--cycles", type=int, default=0, help="반복 횟수, 0은 무한 반복")
    args = parser.parse_args(argv)
    run_demo(args.host, args.port, args.interval, args.cycles)
    return 0


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    if command_args[:1] == ["bridge"]:
        from .service_bridge import run_bridge

        run_bridge()
        return 0
    if command_args[:1] == ["demo"]:
        return _run_preview(command_args[1:])

    parser = argparse.ArgumentParser(prog="deskmate_hub", description="DESKMATE 중앙 추론 허브")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--replay", metavar="LOG.jsonl", help="JSONL 센서 로그를 리플레이")
    source.add_argument("--demo", action="store_true", help="합성 세션으로 전체 경로 스모크 실행")
    parser.add_argument("--config", metavar="fsm.yaml", help="FSM 설정 경로(기본: 패키지 config)")
    parser.add_argument("--quiet", action="store_true", help="전이 트레이스 생략, 요약만 출력")
    args = parser.parse_args(command_args)

    config = load_config(args.config) if args.config else None
    if args.demo:
        from .demo import demo_frames

        frames = demo_frames()
    elif args.replay:
        try:
            with open(args.replay, encoding="utf-8") as handle:
                frames = list(iter_frames(handle))
        except (OSError, ValueError) as exc:
            print(f"리플레이 실패: {exc}", file=sys.stderr)
            return 1
    else:
        parser.error("--replay, --demo 또는 demo 명령 중 하나가 필요하다")
        return 2

    print(format_report(replay(frames, config), quiet=args.quiet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
