"""모의 스마트 플러그 — `deskmate/control/cmd` 를 받아 `deskmate/control/result` 로 응답한다.

    python tools/mock_plug.py --broker <ip> [--delay 0.5] [--fail-target desk_lamp]

실기 플러그 모델이 정해지기 전(agent-briefing D11) hub 제어 경로(ACTION_ENV → 명령 → 결과 → MONITOR)를
브로커 위에서 리허설하기 위한 도구다. 상태표를 stdout 에 찍고 `deskmate/control/state/<target>` 에 retain 으로 남겨
Node-RED 에서 "지금 팬이 켜져 있다"를 볼 수 있게 한다. 운영 경로에는 포함하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt

TOPIC_CMD = "deskmate/control/cmd"
TOPIC_RESULT = "deskmate/control/result"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--delay", type=float, default=0.5, help="명령 수신 → 결과 발행 지연(초)")
    ap.add_argument("--fail-target", action="append", default=[], help="이 target 은 항상 failed 로 응답(복구 경로 시험)")
    ap.add_argument("--node", default="mock-plug")
    args = ap.parse_args(argv)

    state: dict[str, dict] = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{args.node}-{int(time.time())}")
    health = f"deskmate/health/{args.node}"
    client.will_set(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)

    def respond(cmd: dict) -> None:
        time.sleep(args.delay)
        target = cmd.get("target_id", "?")
        ok = target not in args.fail_target
        if ok:
            state.setdefault(target, {})[cmd.get("operation")] = cmd.get("value")
            client.publish(f"deskmate/control/state/{target}", json.dumps(state[target]), qos=0, retain=True)
        result = {"command_id": cmd.get("command_id"), "status": "succeeded" if ok else "failed",
                  "actual_value": cmd.get("value") if ok else None,
                  "error_code": None if ok else "mock_failure", "completed_ts_ms": int(time.time() * 1000)}
        client.publish(TOPIC_RESULT, json.dumps({"schema_version": "1.0", "ts": round(time.time(), 3),
                                                 "node": args.node, "data": result}), qos=1)
        print(f"[plug] {target}.{cmd.get('operation')}={cmd.get('value')!r} ({cmd.get('gate')}) → {result['status']}   state={state}",
              flush=True)

    def on_message(c, u, msg):
        try:
            body = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        cmd = body.get("data") if isinstance(body.get("data"), dict) else body
        if not isinstance(cmd, dict) or "command_id" not in cmd:
            return
        threading.Thread(target=respond, args=(cmd,), daemon=True).start()

    def on_connect(c, u, flags, rc, props):
        c.subscribe(TOPIC_CMD, qos=1)
        c.publish(health, json.dumps({"ts": time.time(), "node": args.node, "status": "online"}), qos=1, retain=True)
        print(f"[plug] connected {args.broker}:{args.port}, waiting for {TOPIC_CMD}", file=sys.stderr, flush=True)

    client.on_connect, client.on_message = on_connect, on_message
    client.connect(args.broker, args.port, keepalive=30)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        client.publish(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
