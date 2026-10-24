"""방 물리 도우미 — 절대습도·상대습도 변환, 바깥 일광(시각·계절·날씨) → 책상 위 조도. 숫자는 전부 가정(config/devices.yaml `room`).

DeviceWorld.step 이 1 초마다 부른다. 모델(1차, 한 덩어리 공기):
  CO₂   사람 발생(L/h ÷ 방 부피) − 공기 교환(닫힘 누설 / 창문) × (실내 − 바깥)
  온도  외피 결합(벽, 느림) + 창문 결합 + 냉난방(설정 온도 쪽으로) + 재실 발열(사람·PC)
  습도  절대습도로 계산: 사람 수분 발생 + 공기 교환(바깥 절대습도 쪽으로) + 벽·가구 흡방습(시작값 쪽으로) → 실내 온도에서 RH
  조도  창 일광(바깥 수평 조도 × 일광률) + 천장등(사람이 어두우면 켠다) + 무드등
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any


def sat_ah(temp_c: float) -> float:
    """포화 절대습도 g/m³ (Magnus)."""
    return 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5)) * 100.0 * 2.1674 / (273.15 + temp_c)


def ah_from_rh(temp_c: float, rh: float) -> float:
    return sat_ah(temp_c) * max(0.0, rh) / 100.0


def rh_from_ah(temp_c: float, ah: float) -> float:
    return max(1.0, min(100.0, 100.0 * ah / sat_ah(temp_c)))


def outdoor_lux(now: float, season: str, condition: str, cfg: dict[str, Any]) -> float:
    """바깥 수평 조도(lx). 일출~일몰 사이 사인 곡선 × 계절 정점 × 날씨 계수."""
    t = _dt.datetime.fromtimestamp(now)
    h = t.hour + t.minute / 60.0 + t.second / 3600.0
    rise, sset = (cfg.get("sun_hours") or {}).get(season, [6.5, 18.5])
    if not (rise < h < sset):
        return 0.0
    s = math.sin(math.pi * (h - rise) / (sset - rise))
    peak = float((cfg.get("peak_outdoor_lux") or {}).get(season, 75000))
    k = float((cfg.get("condition_factor") or {}).get(str(condition), 0.6))
    return peak * k * s ** 1.3


def indoor_daylight(now: float, weather: dict[str, Any], cfg: dict[str, Any]) -> float:
    return outdoor_lux(now, str(weather.get("season", "autumn")), str(weather.get("condition", "clear")), cfg) * float(
        cfg.get("daylight_factor", 0.015))
