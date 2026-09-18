"""MQTT 메시지 → SensorCache 라우팅 (paho 의존 없음).

두 경로가 같은 함수를 쓴다.
- PC 개발: `MqttSource`(paho) 의 on_message.
- Pi 4 보드: 제한 Python 에 `_socket` 이 없어 C++ 네이티브 서비스가 브로커를 붙들고, 수신 메시지를
  `MQTT\\t<topic>\\t<payload>` 한 줄로 stdin 에 넘긴다 → `MqttLineSource.feed_line`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .cache import SensorCache
from .protocol import TOPIC_FEEDBACK, parse_sensor_message

TOPIC_CONTROL_CMD = "deskmate/control/cmd"
TOPIC_CONTROL_RESULT = "deskmate/control/result"
TOPIC_SESSION_REPORT = "deskmate/session/report"

LINE_PREFIX = "MQTT\t"


def route_mqtt_message(cache: SensorCache, topic: str, payload: bytes, now: float | None = None) -> str | None:
    """토픽별로 cache 에 반영하고 종류("mmwave"/"env"/"keystroke"/"feedback"/"control_result")를 돌려준다.
    계약 밖이면 None."""
    now = time.time() if now is None else now
    if topic in (TOPIC_FEEDBACK, TOPIC_CONTROL_RESULT):
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        if not isinstance(data, dict):
            return None
        if topic == TOPIC_FEEDBACK:
            cache.put_feedback(data)
            return "feedback"
        put_control_result = getattr(cache, "put_control_result", None)
        if put_control_result is None:
            return None
        put_control_result(data)
        return "control_result"
    parsed = parse_sensor_message(topic, payload, now)
    if parsed is None:
        return None
    kind, sample = parsed
    cache.put(kind, sample)
    return kind


@dataclass
class MqttLineStats:
    lines: int = 0
    routed: int = 0
    rejected: int = 0


class MqttLineSource:
    """C++ 서비스가 넘기는 `MQTT\\t<topic>\\t<payload>` 라인을 cache 에 반영한다."""

    def __init__(self, cache: SensorCache) -> None:
        self.cache = cache
        self.stats = MqttLineStats()

    def feed_line(self, line: str, now: float | None = None) -> str | None:
        self.stats.lines += 1
        if not line.startswith(LINE_PREFIX):
            self.stats.rejected += 1
            return None
        parts = line.rstrip("\r\n").split("\t", 2)
        if len(parts) != 3 or not parts[1]:
            self.stats.rejected += 1
            return None
        kind = route_mqtt_message(self.cache, parts[1], parts[2].encode("utf-8"), now)
        if kind is None:
            self.stats.rejected += 1
        else:
            self.stats.routed += 1
        return kind


def control_envelope(command: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """`deskmate/control/cmd` envelope (data-spec §10). MqttSource.publish_control 과 같은 모양."""
    return {"schema_version": "1.0", "ts": round(time.time() if now is None else now, 3), "node": "hub",
            "data": command}
