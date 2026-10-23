"""블루투스 선풍기 도구: 프레임·상태 해석이 펌웨어 규격과 맞고, vent_fan 어댑터가 control 계약대로 답하는지."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ble_fan
from ble_fan import Bridge, FanState, decode_state, plan_command


def now_ms():
    return int(time.time() * 1000)


def command(**overrides):
    cmd = {"command_id": "c1", "target_id": "vent_fan", "operation": "set_power", "value": "on",
           "gate": "auto", "requires_confirmation": False, "expires_ts_ms": now_ms() + 15000}
    cmd.update(overrides)
    return cmd


def test_frames_match_firmware_opcodes():
    assert ble_fan.power(True) == bytes([1, 1])
    assert ble_fan.power(False) == bytes([1, 0])
    assert ble_fan.speed(60) == bytes([2, 60])
    assert ble_fan.level(3) == bytes([3, 3])
    assert ble_fan.mode("natural") == bytes([4, 1])
    assert ble_fan.direction("reverse") == bytes([5, 1])
    assert ble_fan.timer(600) == bytes([6, 0x58, 0x02])


@pytest.mark.parametrize("call", [lambda: ble_fan.speed(101), lambda: ble_fan.speed(True), lambda: ble_fan.level(4),
                                  lambda: ble_fan.mode("turbo"), lambda: ble_fan.timer(70000)])
def test_frames_reject_out_of_range(call):
    with pytest.raises(ValueError):
        call()


def test_decode_state_layout():
    # 펌웨어 test_encode 와 같은 바이트: speed 55, natural, reverse, 600 s
    state = decode_state(bytes([1, 1, 55, 2, 1, 1, 0x58, 0x02]))
    assert state == FanState(power=True, speed=55, level=2, mode="natural", direction="reverse", timer_s=600)
    assert state.payload()["set_power"] == "on"
    with pytest.raises(ValueError):
        decode_state(bytes([2, 0, 0, 0, 0, 0, 0, 0]))
    with pytest.raises(ValueError):
        decode_state(bytes([1, 0]))


def test_plan_command_accepts_contract_operations():
    assert plan_command(command(), now_ms()) == (bytes([1, 1]), None)
    assert plan_command(command(value="off", gate="undo"), now_ms()) == (bytes([1, 0]), None)
    assert plan_command(command(operation="set_speed", value=70), now_ms()) == (bytes([2, 70]), None)
    assert plan_command(command(operation="set_timer", value=300), now_ms()) == (bytes([6, 0x2c, 0x01]), None)


@pytest.mark.parametrize("overrides, error", [
    ({"requires_confirmation": True}, "invalid_command"),
    ({"requires_confirmation": None}, "invalid_command"),
    ({"gate": "none"}, "invalid_command"),
    ({"expires_ts_ms": None}, "invalid_command"),
    ({"expires_ts_ms": 1}, "expired"),
    ({"operation": "power_cycle"}, "unsupported_operation_or_value"),
    ({"value": "maybe"}, "unsupported_operation_or_value"),
    ({"operation": "set_speed", "value": 150}, "unsupported_operation_or_value"),
])
def test_plan_command_refuses(overrides, error):
    assert plan_command(command(**overrides), now_ms()) == (None, error)


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), retain))

    def results(self):
        return [p["data"] for t, p, _ in self.published if t == ble_fan.TOPIC_RESULT]


class FakeLink:
    def __init__(self, state=None, error=None):
        self.state = state or FanState(True, 60, 2, "normal", "forward", 0)
        self.error = error
        self.frames = []

    async def send(self, frame):
        self.frames.append(frame)
        if self.error:
            raise self.error
        return self.state


def run(bridge, cmd, retained=False):
    payload = json.dumps({"schema_version": "1.0", "node": "hub", "data": cmd}).encode()
    asyncio.run(bridge.handle(payload, retained))


def test_bridge_success_publishes_result_and_state():
    mqtt, link = FakeMqtt(), FakeLink()
    bridge = Bridge(mqtt, link, "ble-fan")
    run(bridge, command())
    assert link.frames == [bytes([1, 1])]
    result = mqtt.results()[0]
    assert result["command_id"] == "c1" and result["status"] == "succeeded" and result["actual_value"] == "on"
    state = [(p, r) for t, p, r in mqtt.published if t == ble_fan.TOPIC_STATE]
    assert state and state[0][0]["set_power"] == "on" and state[0][1] is True


def test_bridge_ignores_other_targets_and_retained():
    mqtt, link = FakeMqtt(), FakeLink()
    bridge = Bridge(mqtt, link, "ble-fan")
    run(bridge, command(target_id="desk_lamp"))
    run(bridge, command(command_id="c2"), retained=True)
    assert link.frames == [] and mqtt.published == []


def test_bridge_duplicate_does_not_rewrite_ble():
    mqtt, link = FakeMqtt(), FakeLink()
    bridge = Bridge(mqtt, link, "ble-fan")
    run(bridge, command())
    run(bridge, command())
    assert len(link.frames) == 1
    assert [r["status"] for r in mqtt.results()] == ["succeeded", "succeeded"]


def test_bridge_reports_ble_failure_and_mismatch():
    mqtt = FakeMqtt()
    run(Bridge(mqtt, FakeLink(error=OSError("gone")), "ble-fan"), command())
    assert mqtt.results()[-1]["error_code"] == "bluetooth_write_failed"
    off_state = FanState(False, 60, 0, "normal", "forward", 0)
    run(Bridge(mqtt, FakeLink(state=off_state), "ble-fan"), command(command_id="c3"))
    assert mqtt.results()[-1]["status"] == "failed"
    assert mqtt.results()[-1]["error_code"] == "state_mismatch" and mqtt.results()[-1]["actual_value"] == "off"


def test_bridge_refuses_without_touching_ble():
    mqtt, link = FakeMqtt(), FakeLink()
    run(Bridge(mqtt, link, "ble-fan"), command(requires_confirmation=True))
    assert link.frames == [] and mqtt.results()[0]["error_code"] == "invalid_command"
