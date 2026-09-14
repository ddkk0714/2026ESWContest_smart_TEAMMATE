"""MQTT 구독 → SensorCache, 그리고 hub 발행(state/phase · health).

토픽·payload 는 docs/mqtt-topics.md 를 따른다. 센서 토픽은 공통 envelope
(schema_version/ts/node/boot_id/seq/data) 또는 collector 의 평면 payload 둘 다 받는다.
paho-mqtt 2.x. import 는 run 명령에서만 일어나므로 FSM 테스트에는 paho 가 필요 없다.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .cache import SensorCache
from .protocol import TOPIC_FEEDBACK, TOPIC_HEALTH, TOPIC_SENSOR, TOPIC_STATE, parse_sensor_message

class MqttSource:
    """센서·피드백을 구독해 cache 에 넣고, 상태를 발행한다."""

    def __init__(self, cache: SensorCache, host: str, port: int = 1883, *, client_id: str = "deskmate-hub",
                 on_log: Callable[[str], None] | None = None) -> None:
        self.cache = cache
        self.host, self.port = host, port
        self._log = on_log or (lambda msg: None)
        self.connected = False
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.will_set(TOPIC_HEALTH, json.dumps({"node": "hub", "status": "offline"}), qos=1, retain=True)

    # ---- lifecycle ----
    def start(self) -> None:
        self._client.connect_async(self.host, self.port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        try:
            if self.connected:
                self._client.publish(TOPIC_HEALTH, json.dumps({"node": "hub", "status": "offline"}), qos=1, retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001 — 종료 경로
            pass

    # ---- publish ----
    def publish_state(self, envelope: dict[str, Any]) -> None:
        if self.connected:
            self._client.publish(TOPIC_STATE, json.dumps(envelope, ensure_ascii=False), qos=1, retain=True)

    # ---- callbacks ----
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self._log(f"[mqtt] connect failed: {reason_code}")
            return
        self.connected = True
        client.subscribe([(TOPIC_SENSOR, 0), (TOPIC_FEEDBACK, 1)])
        client.publish(TOPIC_HEALTH, json.dumps({"ts": round(time.time(), 3), "node": "hub", "status": "online"}),
                       qos=1, retain=True)
        self._log(f"[mqtt] connected {self.host}:{self.port}")

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected = False
        self._log("[mqtt] disconnected (자동 재연결)")

    def _on_message(self, client, userdata, msg) -> None:
        now = time.time()
        if msg.topic == TOPIC_FEEDBACK:
            try:
                body = json.loads(msg.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
            if isinstance(data, dict):
                self.cache.put_feedback(data)
            return
        parsed = parse_sensor_message(msg.topic, msg.payload, now)
        if parsed is not None:
            kind, sample = parsed
            self.cache.put(kind, sample)
