import json

import pytest

from uart_mqtt_bridge import EnvelopeBuilder, LineRejected, parse_usb_line


def mmwave_line(**overrides):
    payload = {
        "t": "mmwave",
        "ms": 1234,
        "present": True,
        "motion_state": "still",
        "motion_level": 12,
        "distance_cm": 87,
        "resp_bpm": 15,
        "resp_valid": True,
        "heart_bpm": None,
        "heart_valid": False,
        "drowsy_state": "AWAKE",
        "valid": True,
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":"))


def test_mmwave_line_becomes_contract_envelope_without_ms():
    bridge = EnvelopeBuilder("esp32-a", boot_id="1234abcd", clock=lambda: 42.5)
    topic, envelope = bridge.convert(mmwave_line())

    assert topic == "deskmate/sensor/mmwave/esp32-a"
    assert envelope == {
        "schema_version": "1.0",
        "ts": 42.5,
        "node": "esp32-a",
        "boot_id": "1234abcd",
        "seq": 0,
        "data": {
            "present": True,
            "motion_state": "still",
            "motion_level": 12,
            "distance_cm": 87,
            "resp_bpm": 15,
            "resp_valid": True,
            "heart_bpm": None,
            "heart_valid": False,
            "drowsy_state": "AWAKE",
            "valid": True,
        },
    }
    assert "ms" not in envelope["data"]


def test_non_json_debug_line_is_ignored():
    assert parse_usb_line("C1001 ready") is None
    assert parse_usb_line('{ "t": "env" }') is None


def test_sequence_is_per_topic_and_increases_after_conversion():
    bridge = EnvelopeBuilder("esp32", boot_id="00000000", clock=lambda: 1.0)
    assert bridge.convert(mmwave_line())[1]["seq"] == 0
    assert bridge.convert('{"t":"env","ms":2}')[1]["seq"] == 0
    assert bridge.convert(mmwave_line(ms=3))[1]["seq"] == 1


def test_missing_env_sensors_are_null_and_invalid():
    _, data = parse_usb_line('{"t":"env","ms":5000}')
    assert data == {
        "co2_ppm": None,
        "temp_c": None,
        "humidity_pct": None,
        "lux": None,
        "co2_valid": False,
        "temp_valid": False,
        "humidity_valid": False,
        "lux_valid": False,
    }


@pytest.mark.parametrize(
    "line",
    [
        '{"t":"mmwave",',
        mmwave_line(motion_level=101),
        mmwave_line(motion_state="walking"),
        mmwave_line(present=1),
        '{"t":"env","ms":5,"co2_ppm":null,"co2_valid":true}',
    ],
)
def test_malformed_or_invalid_sensor_line_is_rejected(line):
    with pytest.raises(LineRejected):
        parse_usb_line(line)
