"""collector 엔트리포인트.

  python -m collector --broker <pi4-ip>
  python -m collector --broker auto      # 같은 Wi-Fi 에서 Pi 4 hub 브로커를 스스로 찾는다
브로커 미지정/미연결 시에도 로컬 로깅으로 단독 동작한다. 종료는 Ctrl+C.
"""
from __future__ import annotations

import argparse
import signal
import time

from .control import ControlState
from .capture import InputCapture
from .config import AUTO, TARGETS, Config
from .discover import AutoBroker
from .features import extract
from .publisher import FeaturePublisher


def _parse_args():
    p = argparse.ArgumentParser(prog="collector", description="DESKMATE 키스트로크 수집기")
    p.add_argument("--target", choices=sorted(TARGETS),
                   help="Pi 4 브로커 별칭: hotspot=<PI4_HOST> · lan=<PI4_HOST> (--broker 가 있으면 그쪽이 이긴다)")
    p.add_argument("--broker", help="MQTT 브로커 호스트(Pi4 IP). 'auto' 면 같은 Wi-Fi 에서 hub 를 찾는다. 미지정 시 localhost")
    p.add_argument("--port", type=int, help="브로커 포트(기본 1883)")
    p.add_argument("--node", help="노드 식별자(기본 pc-collector)")
    p.add_argument("--window", type=int, help="특징 윈도 길이(초, 기본 60)")
    p.add_argument("--period", type=float, help="발행 주기(초, 기본 1.0 = 1Hz)")
    p.add_argument("--local-only", action="store_true", help="capture/control test only; no MQTT or feature log")
    return p.parse_args()


def capture_payload(cap, cfg, now):
    since = now - cfg.window_s
    feats = extract(
        cap.snapshot(since_t=since),
        window_s=cfg.window_s,
        now_t=now,
        mouse_events=cap.mouse_snapshot(since_t=since),
        flight_gap_max_s=cfg.flight_gap_max_s,
        idle_gap_s=cfg.idle_gap_s,
    )
    payload = feats.to_payload(cfg.node)
    if not cap.keyboard_running():
        payload = {"node": cfg.node, "window_s": cfg.window_s, "valid": False,
            "keyboard_capture_enabled": False, "capture_status": "user_disabled" if not cap.keyboard_enabled else "capture_unavailable",
            "mouse_active": feats.mouse_active, "mouse_event_rate": feats.mouse_event_rate,
            "input_active": feats.mouse_active}
    else:
        payload.update(keyboard_capture_enabled=True, capture_status="enabled")
    return payload


def main():
    args = _parse_args()
    cfg = Config.resolve(args)
    control = ControlState()
    print("=" * 60)
    print("DESKMATE collector (키스트로크 수집)")
    print(f"  node   : {cfg.node}")
    print(f"  broker : {cfg.broker_host}:{cfg.broker_port}")
    print(f"  topic  : {cfg.topic}")
    print(f"  window : {cfg.window_s}s / period {cfg.publish_period_s}s")
    print("  (키/마우스 내용은 저장·전송하지 않음 — 타이밍·종류만)")
    print("=" * 60)

    cap = InputCapture()
    pub = None if args.local_only else FeaturePublisher(cfg)
    auto = AutoBroker(cfg.broker_port, TARGETS.values()) if not args.local_only and cfg.broker_host == AUTO else None
    running = {"on": True}
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__("on", False))

    cap.set_keyboard_enabled(control.requested())
    cap.start()
    control.update(cap, publish_enabled=not args.local_only)
    if pub is not None and auto is None:
        pub.start()          # auto 는 찾은 뒤 AutoBroker 가 retarget 으로 시작한다
    try:
        while running["on"]:
            time.sleep(cfg.publish_period_s)
            cap.set_keyboard_enabled(control.requested())
            now = time.monotonic()
            if auto is not None:
                auto.tick(pub, now)
            since = now - cfg.window_s
            payload = capture_payload(cap, cfg, now)
            if pub is not None:
                pub.publish(payload)
            control.update(cap, publish_enabled=not args.local_only)
    finally:
        print("\n[collector] shutting down...")
        cap.stop()
        if pub is not None:
            pub.stop()
        control.update(cap, running=False, publish_enabled=not args.local_only)
        control.close()


if __name__ == "__main__":
    main()
