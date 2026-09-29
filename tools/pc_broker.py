"""개발용 MQTT 브로커(PC) — amqtt 를 0.0.0.0:1883 에 띄우고 `deskmate/#` 전 메시지를 stdout(+JSONL)에 남긴다.

    python -m pip install amqtt paho-mqtt        # tools/requirements.txt 의 선택 항목
    python tools/pc_broker.py [--port 1883] [--log-dir logs] [--quiet]

왜 PC 인가 (2026-09-17 결정, 통합 MVP 까지): Pi 4·Pi 5 ATLAS 이미지에는 브로커가 없고 루트 FS 가 읽기 전용이라
브로커를 보드에 두려면 IPK 포팅이 필요하다. 그때까지 PC 가 브로커를 맡는다.

네트워크: PC 가 ASUS 공유기 안쪽(192.168.50.x)에 있으면 보드(172.16.34.x)는 PC 로 접속하지 못한다.
공유기에 포트포워딩 **TCP 1883 → <PC IP>:1883** 을 넣고, 보드는 공유기 WAN IP(예 172.16.34.176):1883 로 붙는다.
Windows 방화벽도 1883 인바운드를 허용해야 한다(관리자 PowerShell):
    New-NetFirewallRule -DisplayName "DESKMATE MQTT 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow

시연장에서 공유기가 바뀌면 보드 쪽 hub.env 의 DESKMATE_MQTT_HOST 와 display 빌드의 DESKMATE_MQTT_HOST 만 바꾼다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from amqtt.broker import Broker


async def serve(port: int, log_dir: Path | None, quiet: bool) -> None:
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"0.0.0.0:{port}"}},
                     "sys_interval": 0, "auth": {"allow-anonymous": True}, "topic-check": {"enabled": False}})
    await broker.start()
    log_file = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / f"mqtt-{time.strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8")

    tap = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pc-broker-tap-{os.getpid()}")

    def on_message(client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        if not quiet:
            print(f"{time.strftime('%H:%M:%S')} {'R' if msg.retain else ' '} {msg.topic} {payload[:160]}", flush=True)
        if log_file is not None:
            log_file.write(json.dumps({"ts": round(time.time(), 3), "topic": msg.topic, "retain": msg.retain,
                                       "payload": payload}, ensure_ascii=False) + "\n")
            log_file.flush()

    tap.on_message = on_message
    tap.connect("127.0.0.1", port)
    tap.subscribe("deskmate/#", qos=1)
    tap.loop_start()
    print(f"[pc_broker] listening 0.0.0.0:{port}  (Ctrl+C 로 종료)", file=sys.stderr, flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        tap.loop_stop()
        await broker.shutdown()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("DESKMATE_BROKER_PORT", "1883")))
    ap.add_argument("--log-dir", type=Path, default=None, help="deskmate/# 메시지를 날짜별 JSONL 로 남길 디렉터리")
    ap.add_argument("--quiet", action="store_true", help="stdout 에 메시지를 찍지 않음")
    args = ap.parse_args(argv)
    try:
        asyncio.run(serve(args.port, args.log_dir, args.quiet))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
