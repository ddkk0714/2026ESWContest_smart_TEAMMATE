#!/usr/bin/env python3
"""Bridge DESKMATE ESP32 USB JSON lines to MQTT.

The conversion layer is deliberately independent from serial and MQTT so it can
be tested without hardware or a broker.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

MMWAVE_FIELDS = (
    "present",
    "motion_state",
    "motion_level",
    "distance_cm",
    "resp_bpm",
    "resp_valid",
    "heart_bpm",
    "heart_valid",
    "drowsy_state",
    "valid",
)
ENV_VALUE_FIELDS = ("co2_ppm", "temp_c", "humidity_pct", "lux")
ENV_VALID_FIELDS = ("co2_valid", "temp_valid", "humidity_valid", "lux_valid")
MOTION_STATES = {"none", "still", "active"}
DROWSY_STATES = {"NOPERSON", "NOLOCK", "WARMUP", "AWAKE", "DROWSY"}


class LineRejected(ValueError):
    """Raised when a serial line does not satisfy the USB line contract."""


def _strict_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise LineRejected(f"{field_name} must be boolean")
    return value


def _nullable_number(value: Any, field_name: str, *, integer: bool = False) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LineRejected(f"{field_name} must be numeric or null")
    if integer and not isinstance(value, int):
        raise LineRejected(f"{field_name} must be an integer or null")
    return value


def parse_usb_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """Return ``(kind, normalized data)`` or None for non-JSON log lines.

    Lines which look like sensor JSON but are malformed raise ``LineRejected``
    so the caller can distinguish ignored debug output from discarded frames.
    """

    text = line.strip()
    if not text.startswith('{"t":'):
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LineRejected("invalid JSON") from exc
    if not isinstance(raw, dict):
        raise LineRejected("root must be an object")
    kind = raw.get("t")
    if kind not in {"mmwave", "env"}:
        raise LineRejected("unknown sensor type")
    if not isinstance(raw.get("ms"), int) or isinstance(raw.get("ms"), bool):
        raise LineRejected("ms must be an integer")

    if kind == "mmwave":
        missing = [name for name in MMWAVE_FIELDS if name not in raw]
        if missing:
            raise LineRejected(f"missing fields: {', '.join(missing)}")
        data = {name: raw[name] for name in MMWAVE_FIELDS}
        _strict_bool(data["present"], "present")
        _strict_bool(data["resp_valid"], "resp_valid")
        _strict_bool(data["heart_valid"], "heart_valid")
        _strict_bool(data["valid"], "valid")
        if data["motion_state"] not in MOTION_STATES:
            raise LineRejected("invalid motion_state")
        if data["drowsy_state"] not in DROWSY_STATES:
            raise LineRejected("invalid drowsy_state")
        level = data["motion_level"]
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
            raise LineRejected("motion_level must be an integer from 0 to 100")
        for name in ("distance_cm", "resp_bpm", "heart_bpm"):
            _nullable_number(data[name], name, integer=True)
        return kind, data

    # Missing environmental sensors are represented as null + valid=false.
    data = {name: raw.get(name) for name in ENV_VALUE_FIELDS}
    data.update({name: raw.get(name, False) for name in ENV_VALID_FIELDS})
    _nullable_number(data["co2_ppm"], "co2_ppm", integer=True)
    for name in ("temp_c", "humidity_pct", "lux"):
        _nullable_number(data[name], name)
    for name in ENV_VALID_FIELDS:
        _strict_bool(data[name], name)
    value_for_valid = dict(zip(ENV_VALID_FIELDS, ENV_VALUE_FIELDS))
    for valid_name, value_name in value_for_valid.items():
        if data[valid_name] and data[value_name] is None:
            raise LineRejected(f"{valid_name} cannot be true when {value_name} is null")
    return kind, data


@dataclass
class EnvelopeBuilder:
    node: str
    boot_id: str = field(default_factory=lambda: secrets.token_hex(4))
    clock: Callable[[], float] = time.time
    _sequences: dict[str, int] = field(default_factory=dict, init=False)

    def convert(self, line: str) -> tuple[str, dict[str, Any]] | None:
        parsed = parse_usb_line(line)
        if parsed is None:
            return None
        kind, data = parsed
        topic = f"deskmate/sensor/{kind}/{self.node}"
        seq = self._sequences.get(topic, 0)
        self._sequences[topic] = seq + 1
        return topic, {
            "schema_version": "1.0",
            "ts": self.clock(),
            "node": self.node,
            "boot_id": self.boot_id,
            "seq": seq,
            "data": data,
        }


@dataclass
class Counters:
    received: int = 0
    published: int = 0
    discarded: int = 0


class DailyJsonlWriter:
    def __init__(self, directory: Path, node: str) -> None:
        self.directory = directory
        self.node = node
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, topic: str, envelope: Mapping[str, Any]) -> None:
        day = datetime.now().strftime("%Y%m%d")
        path = self.directory / f"{self.node}-{day}.jsonl"
        record = {"topic": topic, **envelope}
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


class MqttPublisher:
    def __init__(self, broker: str, port: int, node: str) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("paho-mqtt 2.x is required") from exc

        self._mqtt = mqtt
        self._connected = threading.Event()
        self._reconnects = 0
        self._ever_connected = False
        self._node = node
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskmate-{node}")
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.will_set(
            f"deskmate/health/{node}",
            json.dumps({"ts": time.time(), "node": node, "status": "offline", "reconnects": 0}),
            qos=1,
            retain=True,
        )
        self._client.connect_async(broker, port, keepalive=30)
        self._client.loop_start()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if reason_code != 0:
            return
        if self._ever_connected:
            self._reconnects += 1
        self._ever_connected = True
        self._connected.set()
        health = {"ts": time.time(), "node": self._node, "status": "online", "reconnects": self._reconnects}
        client.publish(
            f"deskmate/health/{self._node}",
            json.dumps(health, separators=(",", ":")),
            qos=1,
            retain=True,
        )

    def _on_disconnect(self, client: Any, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
        self._connected.clear()

    def publish(self, topic: str, envelope: Mapping[str, Any]) -> bool:
        if not self._connected.is_set():
            return False
        info = self._client.publish(
            topic,
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            qos=0,
            retain=False,
        )
        return info.rc == self._mqtt.MQTT_ERR_SUCCESS

    def close(self) -> None:
        if self._connected.is_set():
            health = {"ts": time.time(), "node": self._node, "status": "offline", "reconnects": self._reconnects}
            self._client.publish(
                f"deskmate/health/{self._node}",
                json.dumps(health, separators=(",", ":")),
                qos=1,
                retain=True,
            ).wait_for_publish(timeout=2.0)
        self._client.disconnect()
        self._client.loop_stop()


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=_env("DESKMATE_SERIAL_PORT", "DESKMATT_SERIAL_PORT"), required=False)
    parser.add_argument("--baud", type=int, default=int(_env("DESKMATE_SERIAL_BAUD", "DESKMATT_SERIAL_BAUD", default="115200")))
    parser.add_argument("--broker", default=_env("DESKMATE_MQTT_BROKER", "DESKMATT_MQTT_BROKER"), required=False)
    parser.add_argument("--mqtt-port", type=int, default=int(_env("DESKMATE_MQTT_PORT", "DESKMATT_MQTT_PORT", default="1883")))
    parser.add_argument("--node", default=_env("DESKMATE_NODE", "DESKMATT_NODE", default="esp32"))
    parser.add_argument("--log-dir", type=Path, default=Path(_env("DESKMATE_LOG_DIR", "DESKMATT_LOG_DIR", default="logs")))
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.port or not args.broker:
        raise SystemExit("--port and --broker are required (or set DESKMATE_* environment variables)")
    try:
        import serial
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("pyserial is required") from exc

    builder = EnvelopeBuilder(args.node)
    writer = DailyJsonlWriter(args.log_dir, args.node)
    mqtt = MqttPublisher(args.broker, args.mqtt_port, args.node)
    counters = Counters()
    stopping = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    last_report = time.monotonic()
    try:
        with serial.Serial(args.port, args.baud, timeout=1) as connection:
            while not stopping.is_set():
                raw = connection.readline()
                if raw:
                    counters.received += 1
                    try:
                        line = raw.decode("utf-8", errors="strict")
                        converted = builder.convert(line)
                    except (UnicodeDecodeError, LineRejected):
                        counters.discarded += 1
                        converted = None
                    if converted is not None:
                        topic, envelope = converted
                        writer.append(topic, envelope)
                        if mqtt.publish(topic, envelope):
                            counters.published += 1
                now = time.monotonic()
                if now - last_report >= 10:
                    print(
                        f"received={counters.received} published={counters.published} discarded={counters.discarded}",
                        flush=True,
                    )
                    last_report = now
    finally:
        mqtt.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
