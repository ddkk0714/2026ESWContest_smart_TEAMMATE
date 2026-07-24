"""collector 엔트리포인트.

  python -m collector --broker <pi4-ip>
브로커 미지정/미연결 시에도 로컬 로깅으로 단독 동작한다. 종료는 Ctrl+C.
"""
from __future__ import annotations

import argparse
import signal
import time

from .capture import InputCapture
from .config import Config
from .features import extract
from .publisher import FeaturePublisher


def _parse_args():
    p = argparse.ArgumentParser(prog="collector", description="DESKMATE 키스트로크 수집기")
    p.add_argument("--broker", help="MQTT 브로커 호스트(Pi4 IP). 미지정 시 localhost")
    p.add_argument("--port", type=int, help="브로커 포트(기본 1883)")
    p.add_argument("--node", help="노드 식별자(기본 pc-collector)")
    p.add_argument("--window", type=int, help="특징 윈도 길이(초, 기본 60)")
    p.add_argument("--period", type=float, help="발행 주기(초, 기본 1.0 = 1Hz)")
    return p.parse_args()


def main():
    cfg = Config.resolve(_parse_args())
    print("=" * 60)
    print("DESKMATE collector (키스트로크 수집)")
    print(f"  node   : {cfg.node}")
    print(f"  broker : {cfg.broker_host}:{cfg.broker_port}")
    print(f"  topic  : {cfg.topic}")
    print(f"  window : {cfg.window_s}s / period {cfg.publish_period_s}s")
    print("  (키/마우스 내용은 저장·전송하지 않음 — 타이밍·종류만)")
    print("=" * 60)

    cap = InputCapture()
    pub = FeaturePublisher(cfg)
    running = {"on": True}
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__("on", False))

    cap.start()
    pub.start()
    try:
        while running["on"]:
            time.sleep(cfg.publish_period_s)
            now = time.monotonic()
            since = now - cfg.window_s
            feats = extract(
                cap.snapshot(since_t=since),
                window_s=cfg.window_s,
                now_t=now,
                mouse_events=cap.mouse_snapshot(since_t=since),
                flight_gap_max_s=cfg.flight_gap_max_s,
                idle_gap_s=cfg.idle_gap_s,
            )
            pub.publish(feats.to_payload(cfg.node))
    finally:
        print("\n[collector] shutting down...")
        cap.stop()
        pub.stop()


if __name__ == "__main__":
    main()
