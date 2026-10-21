"""날씨 토픽(`deskmate/env/weather`, Pi 5 앱 발행, 30 분 retain) → 상황 벡터의 날씨 항목.

docs/mqtt-topics.md `deskmate/env/weather`, docs/personalization-spec.md §7.
stale(`weather.stale_sec`, 기본 2 h) 이면 None 을 돌려주고 상황 벡터는 계절(월)만으로 돈다.
"""
from __future__ import annotations

from typing import Any

from ..ingest.cache import Sample

CONDITIONS = ("clear", "cloudy", "rain", "snow", "fog", "storm")   # 계약 enum (10-21 확정). 그 밖은 "unknown" 으로 둔다


def normalize_weather(data: dict[str, Any], *, fetched_at: float | None, now: float,
                      cfg: dict[str, Any]) -> dict[str, Any] | None:
    """payload data → Situation.weather. stale·계약 밖이면 None."""
    wcfg = cfg["weather"]
    if fetched_at is None or now - float(fetched_at) > float(wcfg["stale_sec"]) or fetched_at > now + 300:
        return None
    try:
        temp = data.get("temp_c")
        wind = data.get("wind_ms")
        humidity = data.get("humidity")
        condition = str(data.get("condition") or "unknown").lower()
        if condition not in CONDITIONS:
            condition = "unknown"          # 계약 밖 값은 비·눈 판정에서 제외되고 로그에만 남는다
        out = {
            "windy": wind is not None and float(wind) >= float(wcfg["windy_ms"]),
            "rain": condition in tuple(wcfg["rain_conditions"]),
            "pm_bad": bool(data.get("pm_bad", False)),
            "outdoor_temp_c": None if temp is None else float(temp),
            "wind_ms": None if wind is None else float(wind),
            "humidity_pct": None if humidity is None else float(humidity),
            "condition": condition,
            "season": data.get("season"),
            "fetched_at": float(fetched_at),
            "source": str(data.get("source") or ""),
        }
    except (TypeError, ValueError):
        return None
    return out


def weather_from_sample(sample: Sample | None, now: float, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """SensorCache.weather 표본 → Situation.weather. 없거나 stale 이면 None."""
    if sample is None or not isinstance(sample.data, dict):
        return None
    fetched = sample.data.get("fetched_at", sample.ts)
    try:
        fetched_f = float(fetched) if fetched is not None else None
    except (TypeError, ValueError):
        fetched_f = None
    return normalize_weather(sample.data, fetched_at=fetched_f, now=now, cfg=cfg)
