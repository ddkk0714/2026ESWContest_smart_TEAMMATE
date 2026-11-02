"""허브 대신 제어 명령 하나를 보내고 결과를 기다린다 — 실기·시연 확인용.

    python tools/send_control.py --broker <Pi 4 IP> vent_fan set_power on
    python tools/send_control.py --broker <Pi 4 IP> vent_fan set_level 3
    python tools/send_control.py --broker <Pi 4 IP> vent_fan set_mode natural
    python tools/send_control.py --broker <Pi 4 IP> vent_fan set_timer 1800

hub 디스패처가 ACTION_ENV 에서 내는 것과 같은 `deskmate/control/cmd` 를 한 번 발행하고(gate auto,
requires_confirmation false, 만료 = 지금 + --timeout 초) `deskmate/control/result` 에서 같은 command_id 를 기다린다.
FSM 을 거치지 않으므로 경로 확인용이다 — 평소에는 hub 만 명령을 낸다. 계약: docs/mqtt-topics.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from typing import Any

TOPIC_CMD = "deskmate/control/cmd"
TOPIC_RESULT = "deskmate/control/result"


def parse_value(raw: str) -> Any:
    """숫자는 정수로(set_level 3), 나머지는 문자열로(set_power on)."""
    return int(raw) if raw.lstrip("-").isdigit() else raw


def build_command(target_id: str, operation: str, value: Any, *, timeout_s: float,
                  now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    return {"schema_version": "1.0", "ts": round(now, 3), "node": "hub",
            "data": {"command_id": uuid.uuid4().hex[:12], "target_id": target_id, "operation": operation,
                     "value": value, "origin_state": "MANUAL_TEST", "cause": "manual", "gate": "auto",
                     "requires_confirmation": False, "expires_ts_ms": int((now + timeout_s) * 1000)}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target_id", help="vent_fan · desk_lamp …")
    ap.add_argument("operation", help="set_power · set_level · set_speed · set_mode · set_timer · set_brightness")
    ap.add_argument("value")
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--timeout", type=float, default=15.0, help="결과를 기다릴 초 (= 명령 만료)")
    args = ap.parse_args(argv)
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise SystemExit("pip install paho-mqtt")

    envelope = build_command(args.target_id, args.operation, parse_value(args.value), timeout_s=args.timeout)
    command_id = envelope["data"]["command_id"]
    done = threading.Event()
    result: dict[str, Any] = {}

    def on_connect(c: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        c.subscribe(TOPIC_RESULT, qos=1)
        c.publish(TOPIC_CMD, json.dumps(envelope), qos=1)
        data = envelope["data"]
        print(f"→ {data['target_id']}.{data['operation']}={data['value']!r} (command_id {command_id})", flush=True)

    def on_message(c: Any, userdata: Any, msg: Any) -> None:
        try:
            body = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and data.get("command_id") == command_id:
            result.update(data, node=body.get("node"))
            done.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"send-control-{command_id}")
    client.on_connect, client.on_message = on_connect, on_message
    client.connect(args.broker, args.port, keepalive=30)
    client.loop_start()
    try:
        if not done.wait(args.timeout):
            print(f"✗ {args.timeout:.0f} 초 안에 결과 없음 — 그 기기를 맡은 쪽(Pi 5 앱 · ble_fan.py bridge)이 떠 있는지 확인",
                  file=sys.stderr)
            return 2
    finally:
        client.loop_stop()
        client.disconnect()
    mark = "✓" if result.get("status") == "succeeded" else "✗"
    print(f"{mark} {result.get('status')} actual={result.get('actual_value')!r} "
          f"error={result.get('error_code')} node={result.get('node')}")
    return 0 if result.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
