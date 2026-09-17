"""iLink Bluetooth lamp adapter for DESKMATE feedback.

The adapter is intentionally run on a PC with Bluetooth, not on the Pi 4
native runtime.  It subscribes to the retained ``state/phase`` MQTT message
and sends only reversible lamp feedback.  ``bleak`` is imported lazily so the
pure protocol helpers and hub test suite do not need Bluetooth support.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TOPIC_STATE = "deskmate/state/phase"
TOPIC_COMMAND = "deskmate/control/cmd"
SERVICE_UUID = "0000a032-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000a040-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000a042-0000-1000-8000-00805f9b34fb"


def _frame(*body: int) -> bytes:
    """Build an iLink command frame. Checksum = 0xFF - (sum of every byte incl. 55 AA and the mode byte),
    the rule the lamp uses in its own status frames and in donandren/ilink_light. For 0x01 frames the
    55+AA+01 prefix sums to 0x100 so it cancels out; for 0x03 (RGB) frames it does not."""
    if any(not isinstance(value, int) or not 0 <= value <= 0xFF for value in body):
        raise ValueError("iLink frame bytes must be integers from 0 to 255")
    head = bytes((0x55, 0xAA, *body))
    return head + bytes(((0xFF - sum(head)) & 0xFF,))


def power(on: bool) -> bytes:
    return _frame(0x01, 0x08, 0x05, 0x01 if on else 0x00)


def brightness(value: int) -> bytes:
    return _frame(0x01, 0x08, 0x01, _byte(value, "brightness"))


def rgb(red: int, green: int, blue: int) -> bytes:
    return _frame(0x03, 0x08, 0x02, _byte(red, "red"), _byte(green, "green"), _byte(blue, "blue"))


def color_temperature(level: int) -> bytes:
    if not isinstance(level, int) or not 1 <= level <= 5:
        raise ValueError("color temperature level must be 1 (6000K) through 5 (3000K)")
    return _frame(0x01, 0x08, 0x09, level)


def status_request() -> bytes:
    return _frame(0x01, 0x08, 0x15, 0x06)


def _byte(value: int, name: str) -> int:
    if not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError(f"{name} must be an integer from 0 to 255")
    return value


def plan_phase_feedback(envelope: dict[str, Any], config: dict[str, Any]) -> list[bytes]:
    """Translate a state/phase envelope to reversible iLink frames.

    Unknown or malformed phases deliberately produce no command.  Values are
    read from YAML rather than baked into the controller.
    """
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else envelope
    phase = data.get("phase") if isinstance(data, dict) else None
    rule = config.get("phase_feedback", {}).get(phase)
    if not isinstance(rule, dict):
        return []
    return plan_light_command(rule)


def plan_light_command(command: dict[str, Any]) -> list[bytes]:
    """Validate a declarative light command and return its BLE frames."""
    frames: list[bytes] = []
    if "power" in command:
        if not isinstance(command["power"], bool):
            raise ValueError("power must be true or false")
        frames.append(power(command["power"]))
    if command.get("power") is False:
        return frames
    if "brightness" in command:
        frames.append(brightness(command["brightness"]))
    if "rgb" in command:
        value = command["rgb"]
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError("rgb must be a three-item list")
        frames.append(rgb(*value))
    if "color_temperature" in command:
        frames.append(color_temperature(command["color_temperature"]))
    return frames


def plan_control_command(envelope: dict[str, Any]) -> list[bytes]:
    """Handle an explicit ``deskmate/control/cmd`` command for ``desk_lamp``."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else envelope
    if not isinstance(data, dict) or data.get("target") != "desk_lamp":
        return []
    command, value = data.get("cmd"), data.get("value")
    if command == "power":
        return plan_light_command({"power": value})
    if command == "set_brightness":
        return plan_light_command({"power": True, "brightness": value})
    if command == "set_rgb":
        return plan_light_command({"power": True, "rgb": value})
    if command == "set_color_temperature":
        return plan_light_command({"power": True, "color_temperature": value})
    return []


class IlinkBleWriter:
    """Short-lived BLE connection per feedback event; works with desktop BLE."""

    def __init__(self, address: str, *, write_with_response: bool = False) -> None:
        self.address = address
        self.write_with_response = write_with_response

    async def send(self, frames: list[bytes]) -> None:
        if not frames:
            return
        try:
            from bleak import BleakClient
        except ImportError as exc:  # pragma: no cover - requires desktop optional dependency
            raise RuntimeError("Bluetooth mode requires 'pip install bleak'") from exc
        async with BleakClient(self.address) as client:
            for frame in frames:
                await client.write_gatt_char(WRITE_UUID, frame, response=self.write_with_response)


@dataclass
class IlinkMqttController:
    config: dict[str, Any]
    dry_run: bool = False

    def __post_init__(self) -> None:
        bluetooth = self.config.get("bluetooth", {})
        self._enabled = bool(self.config.get("enabled", False))
        self._address = str(bluetooth.get("address", "")).strip()
        self._writer = IlinkBleWriter(self._address, write_with_response=bool(bluetooth.get("write_with_response", False)))
        self._last_phase: str | None = None
        self._last_sent = 0.0

    def handle(self, topic: str, payload: bytes) -> None:
        try:
            envelope = json.loads(payload.decode("utf-8"))
            if not isinstance(envelope, dict):
                return
            if topic == TOPIC_STATE:
                data = envelope.get("data") if isinstance(envelope.get("data"), dict) else envelope
                phase = data.get("phase") if isinstance(data, dict) else None
                if not isinstance(phase, str) or phase == self._last_phase:
                    return
                self._last_phase = phase
                frames = plan_phase_feedback(envelope, self.config)
            elif topic == TOPIC_COMMAND:
                frames = plan_control_command(envelope)
            else:
                return
            self._send(frames, topic)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            logging.warning("iLink command ignored: %s", exc)

    def _send(self, frames: list[bytes], source: str) -> None:
        if not frames:
            return
        if not self._enabled:
            logging.info("iLink feedback disabled in control.yaml (%s)", source)
            return
        cooldown = float(self.config.get("cooldown_sec", 5))
        now = time.monotonic()
        if now - self._last_sent < cooldown:
            logging.info("iLink command suppressed by cooldown (%s)", source)
            return
        self._last_sent = now
        if self.dry_run:
            logging.info("[dry-run] %s: %s", source, " ".join(frame.hex(" ") for frame in frames))
            return
        if not self._address:
            logging.warning("iLink Bluetooth address is empty; command not sent")
            return
        asyncio.run(self._writer.send(frames))


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError("control config must be a YAML object")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DESKMATE iLink Bluetooth lamp controller (PC only)")
    parser.add_argument("--broker", required=True, help="Pi 4 MQTT broker IP or hostname")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--config", default=str(Path(__file__).with_name("control.yaml")))
    parser.add_argument("--dry-run", action="store_true", help="print BLE frames without connecting to a lamp")
    args = parser.parse_args(argv)
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise SystemExit("MQTT mode requires paho-mqtt") from exc
    controller = IlinkMqttController(load_config(args.config), dry_run=args.dry_run)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="deskmate-ilink-light")
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.on_connect = lambda client, userdata, flags, reason_code, properties: client.subscribe([(TOPIC_STATE, 1), (TOPIC_COMMAND, 1)])
    client.on_message = lambda client, userdata, message: controller.handle(message.topic, message.payload)
    client.connect_async(args.broker, args.port, keepalive=30)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
