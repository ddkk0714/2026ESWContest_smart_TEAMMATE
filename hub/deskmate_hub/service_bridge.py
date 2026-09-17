"""Line-framed bridge used by the Atlas native HTTP service.

stdin  ← C++ 서비스:  POST	<id>	/api/feedback|/api/test-frame	<json>   (HTTP 요청 중계)
                      UART	{"type","seq","ts_ms","payload_hex"}         (UART2 프레임, CRC 통과분)
stdout → C++ 서비스:  STATE	<envelope json> · ACK	<id>	<status>

모드 (환경변수 DESKMATE_HUB_MODE):
  demo (기본) — 합성 세션 순환 + 센서 테스트 API (기존 동작)
  live        — UART 라인(+ DESKMATE_MQTT_HOST 가 있으면 MQTT 센서 토픽)을 SensorFrame 으로 만들어 FSM 실행,
                state/phase 를 STATE 라인과 MQTT(가능 시)로 발행
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


def _read_commands(store: BridgeStateStore, uart_source=None, mqtt_source=None) -> None:
    for raw_line in sys.stdin.buffer:
        request_id = "0"
        try:
            line = raw_line.decode("utf-8")
            if line.startswith("UART\t"):
                if uart_source is not None:
                    uart_source.feed_line(line)
                continue
            if line.startswith("MQTT\t"):
                if mqtt_source is not None:
                    mqtt_source.feed_line(line)
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


def _apply_bundled_env_defaults() -> None:
    """payload 에 동봉된 hub.env(hub/atlas/files/hub.env) 를 기본값으로 적용한다. 이미 설정된 환경변수(서비스 디렉터리의
    hub.env 를 main.cpp 가 먼저 적용)가 우선. 파일이 없으면(소스 트리 실행) 아무것도 하지 않는다."""
    # importlib.resources 는 tempfile→random→_random(C 확장) 을 끌어와 제한 Python 에서 죽는다. pkgutil 은 zipimport 의
    # get_data 만 쓴다.
    try:
        import pkgutil

        raw = pkgutil.get_data(__package__, "hub.env")
    except (OSError, ImportError, ValueError):
        return
    if not raw:
        return
    text = raw.decode("utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key:
            os.environ.setdefault(key, value)


def run_bridge(interval: float = 1.0) -> None:
    _apply_bundled_env_defaults()
    if os.environ.get("DESKMATE_HUB_MODE", "demo").lower() == "live":
        return _run_live_bridge()
    store = BridgeStateStore()
    reader = threading.Thread(target=_read_commands, args=(store,), daemon=True)
    reader.start()
    run_demo_loop(store, interval=interval, cycles=0)


def _report_runtime_capabilities() -> None:
    """보드 제한 Python 에서 어떤 모듈이 살아있는지 journal 에 남긴다(C 확장 부재 진단용)."""
    import importlib

    report = []
    for name in ("json", "socket", "select", "selectors", "threading", "uuid", "hashlib", "base64", "datetime",
                 "logging", "struct", "math", "random", "ssl", "paho.mqtt.client"):
        try:
            importlib.import_module(name)
            report.append(name)
        except Exception as exc:  # noqa: BLE001 — ImportError 외 초기화 예외도 기록
            report.append(f"{name}!({type(exc).__name__}: {exc})")
    print("[bridge] python", sys.version.split()[0], "modules:", " ".join(report), file=sys.stderr, flush=True)


def _run_live_bridge() -> None:
    """실센서 모드: stdin 의 UART/MQTT 라인 → LiveHub → STATE/REQUEST/REPORT/CMD 라인.

    MQTT 소켓은 C++ 서비스가 잡는다(보드 제한 Python 에 _socket 없음). 이 프로세스는 라인만 주고받는다:
      stdin  ← `UART\t{...}` (ESP32 프레임) · `MQTT\t<topic>\t<payload>` (센서·피드백·제어 결과)
      stdout → `STATE\t{...}` → state/phase(retain) · `REQUEST\t` → interaction/request ·
               `REPORT\t` → session/report(retain) · `CMD\t` → control/cmd
    """
    import time

    _report_runtime_capabilities()

    from .ingest import SensorCache
    from .ingest.mqtt_lines import MqttLineSource, control_envelope
    from .ingest.uart_source import UartLineSource
    from .live import LiveHub

    store = BridgeStateStore()
    cache = SensorCache()
    uart = UartLineSource(cache)
    mqtt_lines = MqttLineSource(cache)
    reader = threading.Thread(target=_read_commands, args=(store, uart, mqtt_lines), daemon=True)
    reader.start()

    def emit(prefix: str, envelope: dict[str, Any]) -> None:
        store.write_message(prefix + "\t" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))

    hub = LiveHub(cache, publish=store.publish,
                  publish_request=lambda envelope: emit("REQUEST", envelope),
                  publish_control=lambda command: emit("CMD", control_envelope(command)),
                  publish_report=lambda envelope: emit("REPORT", envelope), out=sys.stderr)
    next_tick = time.time()
    while True:
        feedback = store.pop_feedback()
        if feedback is not None:
            cache.put_feedback(feedback)
        hub.tick_once()
        if hub.seq % 30 == 0:
            st, ms = uart.stats, mqtt_lines.stats
            print(f"[bridge] uart frames={st.frames} dropped={st.dropped} hb={st.heartbeats} "
                  f"unknown={st.unknown_types} | mqtt lines={ms.lines} routed={ms.routed} rejected={ms.rejected}",
                  file=sys.stderr)
        next_tick += hub.period
        time.sleep(max(0.0, next_tick - time.time()))


if __name__ == "__main__":
    run_bridge()
