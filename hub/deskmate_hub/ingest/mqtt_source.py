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
from .mqtt_lines import route_mqtt_message
from .protocol import (
    TOPIC_CONTROL_CMD, TOPIC_CONTROL_RESULT, TOPIC_FEEDBACK, TOPIC_HEALTH, TOPIC_REQUEST, TOPIC_SENSOR, TOPIC_SESSION_REPORT,
    TOPIC_STATE,
)

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

    def publish_control(self, command: dict[str, Any]) -> None:
        """제어 명령. envelope 로 감싸 QoS 1, retain 없음(재접속한 어댑터가 지난 명령을 재실행하면 안 된다)."""
        if self.connected:
            envelope = {"schema_version": "1.0", "ts": round(time.time(), 3), "node": "hub", "data": command}
            self._client.publish(TOPIC_CONTROL_CMD, json.dumps(envelope, ensure_ascii=False), qos=1, retain=False)

    def publish_report(self, envelope: dict[str, Any]) -> None:
        """세션 리포트. retain — 화면이 나중에 켜져도 마지막 세션 요약을 보여준다."""
        if self.connected:
            self._client.publish(TOPIC_SESSION_REPORT, json.dumps(envelope, ensure_ascii=False), qos=1, retain=True)

    def publish_request(self, envelope: dict[str, Any]) -> None:
        """사용자 확인 질문. retain 하지 않는다(재접속한 화면이 지난 질문을 다시 띄우면 안 된다)."""
        if self.connected:
            self._client.publish(TOPIC_REQUEST, json.dumps(envelope, ensure_ascii=False), qos=1, retain=False)

    # ---- callbacks ----
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self._log(f"[mqtt] connect failed: {reason_code}")
            return
        self.connected = True
        client.subscribe([(TOPIC_SENSOR, 0), (TOPIC_FEEDBACK, 1), (TOPIC_CONTROL_RESULT, 1)])
        client.publish(TOPIC_HEALTH, json.dumps({"ts": round(time.time(), 3), "node": "hub", "status": "online"}),
                       qos=1, retain=True)
        self._log(f"[mqtt] connected {self.host}:{self.port}")

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected = False
        self._log("[mqtt] disconnected (자동 재연결)")

    def _on_message(self, client, userdata, msg) -> None:
        route_mqtt_message(self.cache, msg.topic, msg.payload, time.time())
