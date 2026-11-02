"""send_control 이 hub 디스패처와 같은 모양의 명령을 만들어 Pi 5 앱·ble_fan bridge 의 문을 통과하는지."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ble_fan import plan_command
from send_control import build_command, parse_value


def test_parse_value_keeps_numbers_as_ints():
    assert parse_value("3") == 3
    assert parse_value("on") == "on"
    assert parse_value("natural") == "natural"


def test_command_passes_the_adapter_gate():
    envelope = build_command("vent_fan", "set_level", 3, timeout_s=15, now=1000.0)
    data = envelope["data"]
    assert envelope["node"] == "hub" and data["gate"] == "auto" and data["requires_confirmation"] is False
    assert data["expires_ts_ms"] == 1_015_000
    fresh = build_command("vent_fan", "set_level", 3, timeout_s=15)["data"]
    assert plan_command(fresh, int(time.time() * 1000)) == (bytes([3, 3]), None)
    assert build_command("vent_fan", "set_power", "on", timeout_s=15)["data"]["command_id"] != data["command_id"]
