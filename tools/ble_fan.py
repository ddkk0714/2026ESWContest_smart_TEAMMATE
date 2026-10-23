"""DESKMATE 블루투스 선풍기(Pico 2 W + L9110) — PC 제어 도구 겸 `vent_fan` 어댑터.

    pip install bleak paho-mqtt
    python tools/ble_fan.py scan
    python tools/ble_fan.py on | off | status | watch
    python tools/ble_fan.py speed 60 | level 2 | mode natural | dir reverse | timer 600
    python tools/ble_fan.py send "on; speed 40; timer 30m"
    python tools/ble_fan.py bridge --broker <Pi 4 IP>

`bridge` 는 `deskmate/control/cmd` 에서 target_id=vent_fan 만 BLE 로 실행하고 `deskmate/control/result` 로 답한다
(계약: docs/mqtt-topics.md). 선풍기 상태는 `deskmate/control/state/vent_fan` 에 retain 으로 남긴다(mock_plug 와 같은 자리).
선풍기 버튼으로 바꾼 상태도 알림으로 받아 같은 토픽에 올린다. 펌웨어·배선·GATT 규격: firmware/pico2w_ble_fan/README.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Callable

DEVICE_NAME = "DESKMATE-FAN"
SERVICE_UUID = "f1970001-51e1-4b0e-a7f1-8d27d8e34683"
COMMAND_UUID = "f1970002-51e1-4b0e-a7f1-8d27d8e34683"
STATE_UUID = "f1970003-51e1-4b0e-a7f1-8d27d8e34683"

TARGET_ID = "vent_fan"
TOPIC_CMD = "deskmate/control/cmd"
TOPIC_RESULT = "deskmate/control/result"
TOPIC_STATE = f"deskmate/control/state/{TARGET_ID}"

OP_POWER, OP_SPEED, OP_LEVEL, OP_MODE, OP_DIR, OP_TIMER = range(1, 7)
MODES = ("normal", "natural")
DIRECTIONS = ("forward", "reverse")
STATE_VERSION = 1


def _check(value: Any, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def power(on: bool) -> bytes:
    return bytes((OP_POWER, 1 if on else 0))


def speed(percent: int) -> bytes:
    return bytes((OP_SPEED, _check(percent, 0, 100, "speed")))


def level(value: int) -> bytes:
    return bytes((OP_LEVEL, _check(value, 0, 3, "level")))


def mode(name: str) -> bytes:
    if name not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    return bytes((OP_MODE, MODES.index(name)))


def direction(name: str) -> bytes:
    if name not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    return bytes((OP_DIR, DIRECTIONS.index(name)))


def timer(seconds: int) -> bytes:
    return bytes((OP_TIMER,)) + _check(seconds, 0, 65535, "timer seconds").to_bytes(2, "little")


@dataclass(frozen=True)
class FanState:
    power: bool
    speed: int
    level: int
    mode: str
    direction: str
    timer_s: int

    def payload(self) -> dict[str, Any]:
        """`control/state/vent_fan` 본문. set_power 키는 mock_plug 가 남기던 모양과 맞춘다."""
        return {"set_power": "on" if self.power else "off", **asdict(self), "ts": round(time.time(), 3)}


def decode_state(raw: bytes | bytearray) -> FanState:
    if len(raw) < 8 or raw[0] != STATE_VERSION:
        raise ValueError(f"unexpected fan state {bytes(raw).hex(' ')}")
    return FanState(power=bool(raw[1]), speed=raw[2], level=raw[3],
                    mode=MODES[raw[4]] if raw[4] < len(MODES) else str(raw[4]),
                    direction=DIRECTIONS[raw[5]] if raw[5] < len(DIRECTIONS) else str(raw[5]),
                    timer_s=int.from_bytes(raw[6:8], "little"))


# operation → (BLE 프레임을 만드는 함수, 결과 actual_value 를 상태에서 읽는 함수)
OPERATIONS: dict[str, tuple[Callable[[Any], bytes], Callable[[FanState], Any]]] = {
    "set_power": (lambda v: power({"on": True, "off": False}[v]), lambda s: "on" if s.power else "off"),
    "set_speed": (speed, lambda s: s.speed if s.power else 0),
    "set_level": (level, lambda s: s.level),
    "set_mode": (mode, lambda s: s.mode),
    "set_timer": (timer, lambda s: s.timer_s),
}


def plan_command(cmd: dict[str, Any], now_ms: int) -> tuple[bytes | None, str | None]:
    """vent_fan 명령 하나를 BLE 프레임으로. 실행하면 안 되면 (None, error_code).

    Pi 5 앱의 desk_lamp 처리와 같은 문을 쓴다: 확인이 필요한 명령·모르는 gate·기한 지난 명령은 실행하지 않는다.
    """
    expires = cmd.get("expires_ts_ms")
    if (not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires)
            or cmd.get("gate") not in ("auto", "suggest", "undo") or cmd.get("requires_confirmation") is not False):
        return None, "invalid_command"
    if expires <= now_ms:
        return None, "expired"
    operation = OPERATIONS.get(cmd.get("operation"))
    if operation is None:
        return None, "unsupported_operation_or_value"
    try:
        return operation[0](cmd.get("value")), None
    except (KeyError, ValueError, TypeError):
        return None, "unsupported_operation_or_value"


def actual_value(cmd: dict[str, Any], state: FanState) -> Any:
    return OPERATIONS[cmd["operation"]][1](state)


def value_matches(cmd: dict[str, Any], state: FanState) -> bool:
    want, got = cmd.get("value"), actual_value(cmd, state)
    if cmd.get("operation") == "set_timer":
        return abs(got - want) <= 2  # 쓰기와 읽기 사이에 초가 흐른다
    return got == want


def result_envelope(command_id: Any, status: str, *, actual: Any = None, error_code: str | None = None,
                    node: str = "ble-fan") -> dict[str, Any]:
    now = time.time()
    return {"schema_version": "1.0", "ts": round(now, 3), "node": node,
            "data": {"command_id": command_id, "status": status, "actual_value": actual,
                     "error_code": error_code, "completed_ts_ms": int(now * 1000)}}


class FanLink:
    """선풍기 하나와의 BLE 연결. 끊기면 다음 명령 때 다시 붙는다."""

    def __init__(self, address: str | None = None, name: str = DEVICE_NAME,
                 on_state: Callable[[FanState], None] | None = None, scan_timeout: float = 10.0) -> None:
        self.address, self.name, self.on_state, self.scan_timeout = address, name, on_state, scan_timeout
        self._client: Any = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def _connect(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:  # pragma: no cover - PC 선택 의존
            raise RuntimeError("Bluetooth 는 'pip install bleak' 가 필요합니다") from exc
        if self.address:
            device = await BleakScanner.find_device_by_address(self.address, timeout=self.scan_timeout)
        else:
            device = await BleakScanner.find_device_by_filter(
                lambda d, adv: d.name == self.name or adv.local_name == self.name
                or SERVICE_UUID in (adv.service_uuids or []), timeout=self.scan_timeout)
        if device is None:
            raise ConnectionError(f"{self.address or self.name} 을(를) 찾지 못했습니다 — 전원·거리·다른 연결(휴대폰 등) 확인")
        client = BleakClient(device, disconnected_callback=lambda _: logging.info("fan disconnected"))
        await client.connect()
        if self.on_state:
            await client.start_notify(STATE_UUID, lambda _, data: self._notify(data))
        self._client = client
        logging.info("fan connected: %s", device.address)

    def _notify(self, data: bytearray) -> None:
        try:
            self.on_state(decode_state(data))
        except ValueError as exc:
            logging.warning("%s", exc)

    async def ensure(self) -> None:
        if not self.connected:
            await self._connect()

    async def send(self, frame: bytes) -> FanState:
        """쓰기(응답 있음 — 펌웨어가 거절하면 예외) 뒤 상태를 다시 읽어 돌려준다. 끊긴 연결이면 한 번 다시 붙는다."""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    await self.ensure()
                    await self._client.write_gatt_char(COMMAND_UUID, frame, response=True)
                    return decode_state(await self._client.read_gatt_char(STATE_UUID))
                except Exception:
                    if attempt == 2 or self.connected:
                        raise
                    self._client = None
            raise AssertionError("unreachable")

    async def read(self) -> FanState:
        async with self._lock:
            await self.ensure()
            return decode_state(await self._client.read_gatt_char(STATE_UUID))

    async def close(self) -> None:
        if self.connected:
            await self._client.disconnect()


class Bridge:
    """control/cmd(vent_fan) → BLE → control/result. MQTT 콜백 스레드에서 받은 것을 asyncio 루프 하나에서 차례로 실행."""

    def __init__(self, client: Any, link: FanLink, node: str) -> None:
        self.client, self.link, self.node = client, link, node
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def publish_state(self, state: FanState) -> None:
        self.client.publish(TOPIC_STATE, json.dumps(state.payload()), qos=0, retain=True)

    def _publish_result(self, envelope: dict[str, Any]) -> None:
        self.client.publish(TOPIC_RESULT, json.dumps(envelope), qos=1)

    async def handle(self, payload: bytes, retained: bool) -> None:
        if retained:  # 브로커에 남은 옛 명령은 실행하지 않는다
            return
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        cmd = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        if not isinstance(cmd, dict) or cmd.get("target_id") != TARGET_ID:
            return
        command_id = cmd.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            return
        if command_id in self._results:  # QoS 1 중복: BLE 를 다시 쓰지 않고 같은 결과를 재발행
            self._publish_result(self._results[command_id])
            return
        envelope = await self._execute(cmd)
        self._results[command_id] = envelope
        while len(self._results) > 128:
            self._results.popitem(last=False)
        self._publish_result(envelope)
        data = envelope["data"]
        logging.info("%s.%s=%r (%s) → %s %s", TARGET_ID, cmd.get("operation"), cmd.get("value"), cmd.get("gate"),
                     data["status"], data["error_code"] or "")

    async def _execute(self, cmd: dict[str, Any]) -> dict[str, Any]:
        frame, error = plan_command(cmd, int(time.time() * 1000))
        if error:
            return result_envelope(cmd["command_id"], "failed", error_code=error, node=self.node)
        try:
            state = await self.link.send(frame)
        except Exception as exc:  # noqa: BLE001 - BLE 백엔드마다 예외 종류가 다르다
            logging.warning("BLE write failed: %s", exc)
            expired = cmd["expires_ts_ms"] <= time.time() * 1000
            return result_envelope(cmd["command_id"], "failed", node=self.node,
                                   error_code="expired" if expired else "bluetooth_write_failed")
        self.publish_state(state)
        if not value_matches(cmd, state):
            return result_envelope(cmd["command_id"], "failed", actual=actual_value(cmd, state),
                                   error_code="state_mismatch", node=self.node)
        return result_envelope(cmd["command_id"], "succeeded", actual=actual_value(cmd, state), node=self.node)


async def _run_bridge(args: argparse.Namespace) -> int:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise SystemExit("bridge 는 'pip install paho-mqtt' 가 필요합니다") from exc
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{args.node}-{int(time.time())}")
    health = f"deskmate/health/{args.node}"
    client.will_set(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)
    link = FanLink(args.address, args.name)
    bridge = Bridge(client, link, args.node)
    link.on_state = lambda state: (bridge.publish_state(state), logging.info("fan state %s", state))

    def on_connect(c: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        c.subscribe(TOPIC_CMD, qos=1)
        c.publish(health, json.dumps({"ts": time.time(), "node": args.node, "status": "online"}), qos=1, retain=True)
        logging.info("MQTT %s:%d connected, waiting for %s target_id=%s", args.broker, args.port, TOPIC_CMD, TARGET_ID)

    client.on_connect = on_connect
    client.on_message = lambda c, u, msg: loop.call_soon_threadsafe(queue.put_nowait, (msg.payload, bool(msg.retain)))
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(args.broker, args.port, keepalive=30)
    client.loop_start()
    try:
        while True:
            try:
                payload, retained = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                if not link.connected:  # 명령이 없을 때도 붙어 있어야 버튼 조작이 MQTT 에 보인다
                    try:
                        bridge.publish_state(await link.read())
                    except Exception as exc:  # noqa: BLE001
                        logging.info("fan not reachable yet: %s", exc)
                continue
            await bridge.handle(payload, retained)
    finally:
        client.publish(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)
        client.loop_stop()
        await link.close()


async def _scan(timeout: float) -> int:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    hits = [(d, adv) for d, adv in found.values()
            if d.name == DEVICE_NAME or adv.local_name == DEVICE_NAME or SERVICE_UUID in (adv.service_uuids or [])]
    for device, adv in hits:
        print(f"{device.address}  {adv.local_name or device.name}  RSSI {adv.rssi}")
    if not hits:
        print(f"{DEVICE_NAME} 없음 — 보드 LED 가 1초마다 깜빡이는지(광고 중), 휴대폰 등 다른 기기가 붙어 있지 않은지 확인")
    return 0 if hits else 1


async def _run_once(args: argparse.Namespace) -> int:
    if args.command == "scan":
        return await _scan(args.scan_timeout)
    link = FanLink(args.address, args.name, scan_timeout=args.scan_timeout)
    try:
        if args.command == "watch":
            link.on_state = lambda state: print(state, flush=True)
            print(await link.read(), flush=True)
            print("버튼을 누르거나 다른 기기에서 바꾸면 여기 찍힙니다. Ctrl+C 로 끝.", flush=True)
            while link.connected:
                await asyncio.sleep(1)
            return 1
        frame = {
            "on": lambda: power(True), "off": lambda: power(False),
            "speed": lambda: speed(int(args.value)), "level": lambda: level(int(args.value)),
            "mode": lambda: mode(args.value), "dir": lambda: direction(args.value),
            "timer": lambda: timer(int(args.value)), "send": lambda: args.value.encode("ascii"),
        }.get(args.command)
        state = await (link.send(frame()) if frame else link.read())
        print(state)
        return 0
    finally:
        await link.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "status", "watch", "on", "off", "speed", "level", "mode", "dir",
                                        "timer", "send", "bridge"])
    ap.add_argument("value", nargs="?", help="speed 0-100 · level 0-3 · mode normal|natural · dir forward|reverse · "
                                             "timer 초 · send 텍스트 명령")
    ap.add_argument("--address", default=os.environ.get("DESKMATE_FAN_ADDRESS"),
                    help="선풍기 BLE 주소(없으면 이름 DESKMATE-FAN 으로 찾음)")
    ap.add_argument("--name", default=DEVICE_NAME)
    ap.add_argument("--scan-timeout", type=float, default=10.0)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--node", default="ble-fan")
    args = ap.parse_args(argv)
    if args.command in ("speed", "level", "mode", "dir", "timer", "send") and args.value is None:
        ap.error(f"{args.command} 에는 값이 필요합니다")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return asyncio.run(_run_bridge(args) if args.command == "bridge" else _run_once(args))
    except KeyboardInterrupt:
        return 0
    except (ValueError, ConnectionError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
