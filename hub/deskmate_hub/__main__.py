"""DESKMATE Pi4 hub command line entry point."""
from __future__ import annotations

import argparse

from .demo import run_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="DESKMATE Raspberry Pi 4 FSM hub")
    sub = parser.add_subparsers(dest="command")
    demo = sub.add_parser("demo", help="합성 센서 입력으로 FSM과 화면 API 실행")
    demo.add_argument("--host", default="0.0.0.0", help="수신 주소 (기본: 0.0.0.0)")
    demo.add_argument("--port", type=int, default=8765, help="미리보기 API 포트")
    demo.add_argument("--interval", type=float, default=1.0, help="상태 간 실제 대기 초")
    demo.add_argument("--cycles", type=int, default=0, help="반복 횟수, 0은 무한 반복")
    args = parser.parse_args()

    if args.command in {None, "demo"}:
        run_demo(
            host=getattr(args, "host", "0.0.0.0"),
            port=getattr(args, "port", 8765),
            interval=getattr(args, "interval", 1.0),
            cycles=getattr(args, "cycles", 0),
        )


if __name__ == "__main__":
    main()
