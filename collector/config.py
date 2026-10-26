"""collector 설정 — 기본값 ← 환경변수 ← CLI 인자 순으로 덮어쓴다."""
from __future__ import annotations

import os
from dataclasses import dataclass


# --target 별칭 → Pi 4 브로커 주소. 핫스팟 So 에서는 Pi 4 wlan0, 연구실 LAN 에서는 eth0(Pi 5 직결) — 10-26 실기
TARGETS = {
    "hotspot": "172.20.10.3",
    "lan": "172.16.34.199",
}


@dataclass
class Config:
    node: str = "pc-collector"
    broker_host: str = "localhost"
    broker_port: int = 1883
    topic: str = "deskmate/sensor/keystroke"       # docs/mqtt-topics.md 확정 규약
    health_topic: str = "deskmate/health/pc-collector"
    window_s: int = 60
    publish_period_s: float = 1.0                   # 팀 규약: 1Hz
    flight_gap_max_s: float = 2.0                   # 이보다 큰 키 간격은 '멈춤'
    idle_gap_s: float = 3.0                         # 이 이상 공백이면 idle
    qos: int = 0                                    # 센서 스트림 QoS 0
    log_dir: str = "logs"

    @classmethod
    def resolve(cls, args=None) -> "Config":
        c = cls(
            node=os.environ.get("DESKMATE_NODE", cls.node),
            broker_host=os.environ.get("DESKMATE_BROKER", cls.broker_host),
            broker_port=int(os.environ.get("DESKMATE_BROKER_PORT", cls.broker_port)),
            topic=os.environ.get("DESKMATE_TOPIC", cls.topic),
            window_s=int(os.environ.get("DESKMATE_WINDOW_S", cls.window_s)),
            publish_period_s=float(os.environ.get("DESKMATE_PERIOD_S", cls.publish_period_s)),
        )
        if args is not None:
            if getattr(args, "target", None):
                c.broker_host = TARGETS[args.target]
            if getattr(args, "broker", None):
                c.broker_host = args.broker
            if getattr(args, "port", None):
                c.broker_port = args.port
            if getattr(args, "node", None):
                c.node = args.node
            if getattr(args, "window", None):
                c.window_s = args.window
            if getattr(args, "period", None):
                c.publish_period_s = args.period
        c.health_topic = f"deskmate/health/{c.node}"
        return c
