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
# 이 파일은 밴딧 수식·날씨·페르소나를 환경 arm(선풍기·환기)으로도 시험한다 — 10-25 기본값(policy.causes 에 environment 없음)은
# test_policy_causes.py 가 따로 본다. 여기서는 모든 원인을 정책에 맡긴다.
CFG["policy"] = {"causes": ["environment", "posture", "cognitive"]}
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
        assert d.ventilate_minutes == CFG["ventilation"]["base_minutes"]["winter"]


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


def test_windy_prefers_ventilation_over_fan():
    calm = {"windy": False, "rain": False, "pm_bad": False, "outdoor_temp_c": 16.0}
    windy = {**calm, "windy": True}
    p = greedy()
    d_calm = p.decide(base_sit(10, weather=calm), cause="environment", gate="suggest", session_id="a")
    d_windy = p.decide(base_sit(10, weather=windy), cause="environment", gate="suggest", session_id="b")
    assert d_calm.arm == "fan_on"
    assert d_windy.arm == "ventilate_suggest" and "바람 불어 환기 먼저" in d_windy.explanation
    v = CFG["ventilation"]                         # 가을 15 분 × 바람 → 짧게
    assert d_windy.ventilate_minutes == round(v["base_minutes"]["autumn"] * v["windy_factor"])
    assert d_windy.scores["ventilate_suggest"] > d_calm.scores["ventilate_suggest"]
    assert d_windy.scores["fan_on"] < d_calm.scores["fan_on"]


def test_rain_and_hot_outside_exclude_ventilation_and_winter_shortens_it():
    p = greedy()
    rain = {"windy": False, "rain": True, "pm_bad": False, "outdoor_temp_c": 16.0}   # 바람 없이 비 → 창문 대신 팬·공기청정기
    d = p.decide(base_sit(10, weather=rain), cause="environment", gate="suggest", session_id="r")
    assert d.excluded.get("ventilate_suggest") == "weather" and d.arm in ("fan_on", "air_fan_up")
    assert "air_fan_up" in d.candidates                     # 창문을 못 여니 공기청정기도 후보
    hot = {"windy": True, "rain": False, "pm_bad": False, "outdoor_temp_c": 33.0}
    d = p.decide(base_sit(7, weather=hot), cause="environment", gate="suggest", session_id="h")
    assert d.excluded.get("ventilate_suggest") == "weather"
    pm = {"windy": True, "rain": False, "pm_bad": True, "outdoor_temp_c": 16.0}
    d = p.decide(base_sit(3, weather=pm), cause="environment", gate="suggest", session_id="p")
    assert d.excluded.get("ventilate_suggest") == "weather"
    winter = {"windy": True, "rain": False, "pm_bad": False, "outdoor_temp_c": -2.0}
    d = p.decide(base_sit(1, weather=winter), cause="environment", gate="suggest", session_id="w")
    v = CFG["ventilation"]                         # 겨울 5 분 × 바람 × 실내외 26 °C 차 → 하한
    assert d.arm == "ventilate_suggest" and d.ventilate_minutes == v["min_minutes"]
    assert "겨울" in d.explanation and "실내와 26 °C 차" in d.explanation and d.to_dict()["ventilate_minutes"] == v["min_minutes"]


def plan(month=10, *, temp=24.0, **w):
    return greedy().ventilation_plan(Situation(ts=NOW, hour_local=15.7, weekday=2, month=month, temp_c=temp,
                                               weather=w or None))


def test_weather_sets_window_ventilation_minutes():
    v = CFG["ventilation"]
    assert plan(10)[0] == v["base_minutes"]["autumn"]                      # 날씨 없음 → 계절(달)만
    assert plan(10, season="summer")[0] == v["base_minutes"]["summer"]     # 날씨 토픽 season 이 달보다 우선
    calm, why = plan(10, wind_ms=0.5, outdoor_temp_c=18.0)
    assert calm == round(v["base_minutes"]["autumn"] * v["calm_factor"]) and "바람 잔잔" in why
    windy = plan(10, windy=True, wind_ms=6.0, outdoor_temp_c=18.0)[0]
    assert windy < v["base_minutes"]["autumn"] < calm                      # 바람 세면 짧게, 잔잔하면 길게
    hot = plan(7, outdoor_temp_c=35.0, temp=22.0)
    assert hot[0] == round(v["base_minutes"]["summer"] * v["temp_gap_factor"]) and "바깥 35 °C(실내와 13 °C 차)" in hot[1]
    humid = plan(6, outdoor_temp_c=24.0, humidity_pct=90.0)
    assert humid[0] == round(v["base_minutes"]["summer"] * v["humid_factor"])
    worst = plan(1, windy=True, wind_ms=9.0, outdoor_temp_c=-10.0, humidity_pct=90.0)[0]
    assert worst == v["min_minutes"]                                       # 하한
    assert plan(4, wind_ms=0.0, season="spring")[0] <= v["max_minutes"]


def test_purifier_only_when_outdoor_air_keeps_windows_shut():
    """공기청정기는 CO₂ 를 못 내린다 — CO₂ 높음만으로는 후보가 아니고, 비·미세먼지로 창문을 못 열 때만 후보."""
    from deskmate_hub.personalize.adapter import arm_commands
    p = greedy()
    clear = {"windy": False, "rain": False, "pm_bad": False, "outdoor_temp_c": 16.0}
    for w in (None, clear):
        d = p.decide(base_sit(10, weather=w), cause="environment", gate="suggest", session_id="c")
        assert d.excluded.get("air_fan_up") == "outdoor_air_ok" and d.arm != "air_fan_up"
    d = p.decide(base_sit(3, weather={**clear, "pm_bad": True}), cause="environment", gate="suggest", session_id="p")
    assert "air_fan_up" in d.candidates and d.excluded.get("ventilate_suggest") == "weather"
    arms = {a["id"]: a for a in CFG["arms"]}
    assert arm_commands(arms["air_fan_up"]["device"]) == [
        {"target_id": "air_purifier", "operation": "set_mode", "value": "high", "undo_value": "auto"}]
    assert arm_commands(arms["fan_on"]["device"])[0]["target_id"] == "vent_fan"
