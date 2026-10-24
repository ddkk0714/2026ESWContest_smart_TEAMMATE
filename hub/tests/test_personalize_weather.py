"""날씨 토픽(deskmate/sensor/weather, Pi 5 앱) → SensorCache → 상황 벡터. stale 이면 계절만. 바람·겨울 제약."""
from __future__ import annotations

import copy
import json

import numpy as np

from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.mqtt_lines import MqttLineSource, route_mqtt_message
from deskmate_hub.ingest.protocol import parse_sensor_message
from deskmate_hub.personalize import (FEATURE_NAMES, InterventionPolicy, Situation, SituationExtras, extras_from_cache,
                                      load_personalize_config, vectorize, weather_from_sample)

CFG = load_personalize_config()
NOW = 1_792_561_200.0          # 2026-10-21 15:40 KST


def weather_payload(*, fetched_at, wind=5.2, condition="clear", temp=16.4, seq=17, **extra):
    data = {"temp_c": temp, "humidity": 58, "wind_ms": wind, "condition": condition, "season": "autumn",
            "fetched_at": fetched_at, "source": "test", **extra}
    return json.dumps({"schema_version": "1.0", "ts": fetched_at + 5, "node": "pi5-display",
                       "boot_id": "c41d9a02", "seq": seq, "data": data}).encode()


def env_payload():
    return json.dumps({"ts": NOW - 3, "node": "esp32-b", "seq": 1, "data": {
        "co2_ppm": 1100, "temp_c": 24.0, "humidity_pct": 48, "lux": 300,
        "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True}}).encode()


def greedy(seed=0):
    cfg = copy.deepcopy(CFG)
    cfg["bandit"]["algorithm"] = "greedy"
    return InterventionPolicy(cfg, rng=np.random.default_rng(seed))


def test_weather_topic_is_routed_and_becomes_situation_features():
    cache = SensorCache()
    assert route_mqtt_message(cache, "deskmate/sensor/weather", weather_payload(fetched_at=NOW - 600), NOW - 500) == "weather"
    assert route_mqtt_message(cache, "deskmate/sensor/env/esp32-b", env_payload(), NOW - 3) == "env"
    assert cache.weather is not None and cache.weather.seq == 17
    view = cache.snapshot()
    ex = extras_from_cache(view, NOW, CFG, base=SituationExtras(posture_label="upright"))
    assert ex.co2_ppm == 1100 and ex.temp_c == 24.0 and ex.lux == 300
    assert ex.weather is not None and ex.weather["windy"] is True and ex.weather["rain"] is False
    assert ex.weather["outdoor_temp_c"] == 16.4 and ex.weather["condition"] == "clear" and ex.weather["source"] == "test"
    sit = Situation(ts=NOW, hour_local=15.7, weekday=2, month=10, temp_c=ex.temp_c, co2_ppm=ex.co2_ppm, weather=ex.weather)
    named = dict(zip(FEATURE_NAMES, vectorize(sit, CFG["features"])))
    assert named["windy"] == 1.0 and named["rain"] == 0.0
    assert named["outdoor_temp_diff"] == (16.4 - 24.0) / 10
    assert "weather" not in sit.missing()
    # 보드 경로(C++ 브리지 라인)도 같은 토픽을 받는다
    line_cache = SensorCache()
    src = MqttLineSource(line_cache)
    assert src.feed_line("MQTT\tdeskmate/sensor/weather\t" + weather_payload(fetched_at=NOW - 60).decode(), NOW) == "weather"
    # 계약 밖 이름(한때 검토했던 deskmate/env/weather)은 받지 않는다 — sensor/# 아래 고정
    assert parse_sensor_message("deskmate/env/weather", weather_payload(fetched_at=NOW - 60), NOW) is None
    assert route_mqtt_message(SensorCache(), "deskmate/env/weather", weather_payload(fetched_at=NOW - 60), NOW) is None


def test_stale_or_missing_weather_falls_back_to_season_only():
    cache = SensorCache()
    stale_age = CFG["weather"]["stale_sec"] + 60
    route_mqtt_message(cache, "deskmate/sensor/weather", weather_payload(fetched_at=NOW - stale_age), NOW - 1)
    view = cache.snapshot()
    assert weather_from_sample(view.weather, NOW, CFG) is None          # retain 된 옛 표본은 무시
    ex = extras_from_cache(view, NOW, CFG)
    assert ex.weather is None
    assert extras_from_cache(SensorCache().snapshot(), NOW, CFG).weather is None   # 토픽 자체가 없음
    sit = Situation(ts=NOW, hour_local=15.7, weekday=2, month=1, temp_c=22.0, weather=ex.weather)
    assert "weather" in sit.missing() and sit.season == "winter"
    named = dict(zip(FEATURE_NAMES, vectorize(sit, CFG["features"])))
    assert named["windy"] == 0.0 and named["rain"] == 0.0 and named["outdoor_temp_diff"] == 0.0
    assert named["season_cos"] == 1.0                                   # 1월 → 계절 특징은 여전히 있다
    # stale 이어도 정책은 계절 규칙(겨울 환기 5분)으로 돈다
    p = greedy()
    d = p.decide(Situation(ts=NOW, hour_local=15.7, weekday=2, month=1, co2_ppm=1500, temp_c=22.0, lux=300,
                           c_fatigue=0.8, session_elapsed_min=60, weather=None),
                 cause="environment", gate="suggest", session_id="s")
    assert "weather" not in d.excluded.values()
    if d.arm == "ventilate_suggest":
        assert d.ventilate_minutes == CFG["weather"]["ventilate_minutes_winter"]


def test_fetched_at_missing_uses_envelope_ts_and_bad_payload_is_ignored():
    cache = SensorCache()
    body = json.dumps({"ts": NOW - 100, "node": "pi5-display", "seq": 1,
                       "data": {"temp_c": 10, "wind_ms": 1.0, "condition": "cloudy"}}).encode()
    route_mqtt_message(cache, "deskmate/sensor/weather", body, NOW - 99)
    w = weather_from_sample(cache.snapshot().weather, NOW, CFG)
    assert w is not None and w["fetched_at"] == NOW - 100 and w["windy"] is False and w["condition"] == "cloudy"
    bad = json.dumps({"ts": NOW, "data": {"temp_c": "warm", "wind_ms": None, "condition": "typhoon"}}).encode()
    route_mqtt_message(cache, "deskmate/sensor/weather", bad, NOW)
    assert weather_from_sample(cache.snapshot().weather, NOW, CFG) is None


def base_sit(month, **kw):
    kw.setdefault("weather", None)
    return Situation(ts=NOW, hour_local=15.7, weekday=2, month=month, co2_ppm=1300, temp_c=24.0, lux=300,
                     humidity_pct=50, c_fatigue=0.8, session_elapsed_min=60, **kw)


def test_windy_prefers_ventilation_over_air_fan():
    calm = {"windy": False, "rain": False, "pm_bad": False, "outdoor_temp_c": 16.0}
    windy = {**calm, "windy": True}
    p = greedy()
    d_calm = p.decide(base_sit(10, weather=calm), cause="environment", gate="suggest", session_id="a")
    d_windy = p.decide(base_sit(10, weather=windy), cause="environment", gate="suggest", session_id="b")
    assert d_calm.arm == "air_fan_up"
    assert d_windy.arm == "ventilate_suggest" and "바람 불어 환기 먼저" in d_windy.explanation
    assert d_windy.ventilate_minutes == CFG["weather"]["ventilate_minutes_default"]
    assert d_windy.scores["ventilate_suggest"] > d_calm.scores["ventilate_suggest"]
    assert d_windy.scores["air_fan_up"] < d_calm.scores["air_fan_up"]


def test_rain_and_hot_outside_exclude_ventilation_and_winter_shortens_it():
    p = greedy()
    rain = {"windy": False, "rain": True, "pm_bad": False, "outdoor_temp_c": 16.0}   # 바람 없이 비 → 환기 제외, 공기청정기
    d = p.decide(base_sit(10, weather=rain), cause="environment", gate="suggest", session_id="r")
    assert d.excluded.get("ventilate_suggest") == "weather" and d.arm == "air_fan_up"
    hot = {"windy": True, "rain": False, "pm_bad": False, "outdoor_temp_c": 33.0}
    d = p.decide(base_sit(7, weather=hot), cause="environment", gate="suggest", session_id="h")
    assert d.excluded.get("ventilate_suggest") == "weather"
    pm = {"windy": True, "rain": False, "pm_bad": True, "outdoor_temp_c": 16.0}
    d = p.decide(base_sit(3, weather=pm), cause="environment", gate="suggest", session_id="p")
    assert d.excluded.get("ventilate_suggest") == "weather"
    winter = {"windy": True, "rain": False, "pm_bad": False, "outdoor_temp_c": -2.0}
    d = p.decide(base_sit(1, weather=winter), cause="environment", gate="suggest", session_id="w")
    assert d.arm == "ventilate_suggest" and d.ventilate_minutes == CFG["weather"]["ventilate_minutes_winter"]
    assert "겨울이라 5분만" in d.explanation and d.to_dict()["ventilate_minutes"] == 5
