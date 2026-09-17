"""Line-framed bridge used by the Atlas native HTTP service.

stdin  ← C++ 서비스:  POST	<id>	/api/feedback|/api/test-frame	<json>   (HTTP 요청 중계)
                      UART	{"type","seq","ts_ms","payload_hex"}         (UART2 프레임, CRC 통과분)
stdout → C++ 서비스:  STATE	<envelope json> · ACK	<id>	<status>

모드 (환경변수 DESKMATE_HUB_MODE):
  demo (기본) — 합성 세션 순환 + 센서 테스트 API (기존 동작)
  live        — UART/MQTT 라인을 SensorFrame 으로 만들어 FSM 실행. Atlas native service에서는 C++가
                MQTT 연결을 소유하고 Python은 라인 프로토콜만 사용한다.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

from .demo import run_demo_loop
from .preview_protocol import PreviewStateStore, validate_test_frame


class BridgeStateStore(PreviewStateStore):
    def __init__(self) -> None:
        super().__init__()
        self._output_lock = threading.Lock()

    def write_message(self, message: str) -> None:
        with self._output_lock:
            sys.stdout.write(message + "\n")
            sys.stdout.flush()

    def publish(self, payload: dict[str, Any]) -> None:
        super().publish(payload)
        self.write_message(
            "STATE\t" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )


def _read_commands(store: BridgeStateStore, uart_source=None, mqtt_line_source=None) -> None:
    for raw_line in sys.stdin.buffer:
        request_id = "0"
        try:
            line = raw_line.decode("utf-8")
            if line.startswith("UART\t"):
                if uart_source is not None:
                    uart_source.feed_line(line)
                continue
            if line.startswith("MQTT\t"):
                if mqtt_line_source is not None:
                    mqtt_line_source.feed_line(line)
                continue
            kind, request_id, path, raw_body = line.rstrip("\n").split("\t", 3)
            if kind != "POST":
                raise ValueError("invalid command")
            body = json.loads(raw_body)
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            if path == "/api/feedback":
                if body.get("verdict") not in {"accept", "reject", "correct"}:
                    raise ValueError("invalid verdict")
                store.add_feedback(body)
            elif path == "/api/test-frame":
                validate_test_frame(body)
                store.add_test_frame(body)
            else:
                raise ValueError("invalid path")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
            store.write_message(f"ACK\t{request_id}\t400")
        else:
            store.write_message(f"ACK\t{request_id}\t202")


def run_bridge(interval: float = 1.0) -> None:
    if os.environ.get("DESKMATE_HUB_MODE", "demo").lower() == "live":
        return _run_live_bridge()
    store = BridgeStateStore()
    reader = threading.Thread(target=_read_commands, args=(store,), daemon=True)
    reader.start()
    run_demo_loop(store, interval=interval, cycles=0)


def _run_live_bridge() -> None:
    """실센서 모드: UART와 MQTT 라인 → LiveHub → C++ native MQTT 발행 라인."""
    import time

    from .ingest import SensorCache
    from .ingest.mqtt_lines import MqttLineSource
    from .ingest.uart_source import UartLineSource
    from .live import LiveHub

    store = BridgeStateStore()
    cache = SensorCache()
    uart = UartLineSource(cache)
    native_mqtt = os.environ.get("DESKMATE_NATIVE_MQTT") == "1"
    mqtt_lines = MqttLineSource(cache) if native_mqtt else None
    reader = threading.Thread(
        target=_read_commands,
        args=(store, uart, mqtt_lines),
        daemon=True,
    )
    reader.start()

    mqtt_source = None
    host = os.environ.get("DESKMATE_MQTT_HOST")
    if host and not native_mqtt:
        try:
            from .ingest.mqtt_source import MqttSource

            mqtt_source = MqttSource(cache, host, int(os.environ.get("DESKMATE_MQTT_PORT", "1883")),
                                     on_log=lambda m: print(m, file=sys.stderr))
            mqtt_source.start()
        except Exception as exc:  # noqa: BLE001 — paho 부재 등. MQTT 없이도 FSM 은 돈다.
            print(f"[bridge] MQTT 비활성: {exc}", file=sys.stderr)
            mqtt_source = None

    def publish(envelope: dict[str, Any]) -> None:
        store.publish(envelope)
        if mqtt_source is not None:
            mqtt_source.publish_state(envelope)

    def publish_request(envelope: dict[str, Any]) -> None:
        store.write_message("REQUEST\t" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        if mqtt_source is not None:
            mqtt_source.publish_request(envelope)

    hub = LiveHub(cache, publish=publish, publish_request=publish_request, out=sys.stderr)
    next_tick = time.time()
    try:
        while True:
            feedback = store.pop_feedback()
            if feedback is not None:
                cache.put_feedback(feedback)
            hub.tick_once()
            if hub.seq % 30 == 0:
                st = uart.stats
                mqtt_stats = mqtt_lines.stats if mqtt_lines is not None else None
                mqtt_status = (
                    f" | mqtt lines={mqtt_stats.lines} routed={mqtt_stats.routed} "
                    f"rejected={mqtt_stats.rejected}"
                    if mqtt_stats is not None
                    else ""
                )
                print(
                    f"[bridge] uart frames={st.frames} dropped={st.dropped} hb={st.heartbeats} "
                    f"unknown={st.unknown_types}{mqtt_status}",
                    file=sys.stderr,
                )
            next_tick += hub.period
            time.sleep(max(0.0, next_tick - time.time()))
    finally:
        if mqtt_source is not None:
            mqtt_source.stop()


if __name__ == "__main__":
    run_bridge()
