import json

from deskmate_hub.ingest.cache import SensorCache
from deskmate_hub.service_bridge import _feed_mqtt_line


def test_native_mqtt_sensor_line_updates_cache():
    cache = SensorCache()
    payload = {
        "schema_version": "1.0",
        "ts": 123.5,
        "node": "pc-collector",
        "boot_id": "test",
        "seq": 7,
        "data": {"typing_active": True, "input_active": True, "idle_ratio": 0.2},
    }

    _feed_mqtt_line(
        cache,
        "MQTT\tdeskmate/sensor/keystroke\t" + json.dumps(payload) + "\n",
    )

    sample = cache.snapshot().keystroke
    assert sample is not None
    assert sample.ts == 123.5
    assert sample.seq == 7
    assert sample.data["typing_active"] is True


def test_native_mqtt_feedback_line_accepts_envelope():
    cache = SensorCache()
    payload = {
        "schema_version": "1.0",
        "data": {"request_id": "req-1", "verdict": "accept"},
    }

    _feed_mqtt_line(
        cache,
        "MQTT\tdeskmate/feedback/user\t" + json.dumps(payload) + "\n",
    )

    assert cache.pop_feedback() == {"request_id": "req-1", "verdict": "accept"}


def test_native_mqtt_malformed_line_does_not_ack(monkeypatch, capsys):
    import io
    import sys as _sys

    from deskmate_hub.service_bridge import BridgeStateStore, _read_commands

    class _Stdin:
        buffer = io.BytesIO(
            b"MQTT\tdeskmate/feedback/user\t{not json\n"
            b"MQTT\tdeskmate/sensor/keystroke\t{not json\n"
        )

    monkeypatch.setattr(_sys, "stdin", _Stdin())
    store = BridgeStateStore()
    cache = SensorCache()
    _read_commands(store, None, cache)

    captured = capsys.readouterr()
    assert "ACK" not in captured.out          # 수신 메시지는 명령이 아니므로 ACK 없음
    assert "MQTT 라인 무시" in captured.err   # feedback JSON 오류는 로그로만
    assert cache.pop_feedback() is None
    assert cache.snapshot().keystroke is None
