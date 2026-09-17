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