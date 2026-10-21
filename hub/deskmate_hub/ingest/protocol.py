"""MQTT 토픽·payload 파싱 (paho 의존 없음 — 테스트와 UART 브리지가 공유)."""
from __future__ import annotations

import json

from .cache import Sample

TOPIC_SENSOR = "deskmate/sensor/#"
TOPIC_FEEDBACK = "deskmate/feedback/user"
TOPIC_STATE = "deskmate/state/phase"
TOPIC_REQUEST = "deskmate/interaction/request"
TOPIC_HEALTH = "deskmate/health/hub"
TOPIC_CONTROL_CMD = "deskmate/control/cmd"
TOPIC_CONTROL_RESULT = "deskmate/control/result"
TOPIC_WEATHER = "deskmate/env/weather"      # Pi 5 앱이 받은 바깥 날씨 (retain, 30 분). FSM 입력 아님 — personalize 전용

# hub 가 구독하는 토픽·QoS. PC paho(MqttSource)와 보드 C++ 브리지(hub/atlas mqtt_bridge.cpp)가 같은 목록을 지킨다.
SUBSCRIPTIONS = ((TOPIC_SENSOR, 0), (TOPIC_FEEDBACK, 1), (TOPIC_CONTROL_RESULT, 1), (TOPIC_WEATHER, 0))

_KIND_BY_TOPIC = {"mmwave": "mmwave", "env": "env", "keystroke": "keystroke"}


def parse_sensor_message(topic: str, payload: bytes, received: float) -> tuple[str, Sample] | None:
    """토픽·payload → (kind, Sample). 계약 밖이면 None. `deskmate/env/weather` 는 kind "weather"."""
    if topic == TOPIC_WEATHER:
        kind = "weather"
    else:
        parts = topic.split("/")
        if len(parts) < 3 or parts[0] != "deskmate" or parts[1] != "sensor":
            return None
        kind = _KIND_BY_TOPIC.get(parts[2])
        if kind is None:
            return None
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else body  # 평면 payload 허용
    ts = body.get("ts")
    seq = body.get("seq")
    return kind, Sample(
        received=received,
        ts=float(ts) if isinstance(ts, (int, float)) else received,
        seq=int(seq) if isinstance(seq, int) else None,
        data=data,
    )
