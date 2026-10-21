"""inference/types 의 SensorFrame·TickResult 와 3층을 잇는 어댑터. engine 은 수정하지 않는다.

결정 시점 = TickResult.actions 에 `route:<cause>` 가 있는 tick (CAUSE_ANALYSIS → ACTION_*).
SensorFrame 이 싣지 않는 값(자세 분포·환경 원값·시각·날씨)은 SituationExtras 로 받는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..inference.types import SensorFrame, TickResult
from ..ingest.cache import CacheView
from .features import Situation
from .profile import ProfileSummary, is_after_bedtime
from .weather import weather_from_sample


@dataclass
class SituationExtras:
    """프레임 밖에서 오는 상황 정보. 전부 선택."""
    posture_dist: dict[str, float] = field(default_factory=dict)
    posture_label: str = "unknown"
    posture_hold_min: float = 0.0
    slump_hold_min: float = 0.0
    drowsy: bool = False
    drowsy_count_30min: int = 0
    co2_ppm: float | None = None
    lux: float | None = None
    temp_c: float | None = None
    humidity_pct: float | None = None
    session_started_ts: float | None = None
    last_intervention_ts: float | None = None
    weather: dict[str, Any] | None = None


def extras_from_cache(view: CacheView, now: float, cfg: dict[str, Any], *, env_max_age: float = 30.0,
                      base: SituationExtras | None = None) -> SituationExtras:
    """SensorCache 스냅샷에서 환경 원값(유효 표시 true 만)과 날씨(stale 아니면)를 채운다.
    자세 분포·세션 시각 등 cache 에 없는 값은 base 에서 그대로 가져온다."""
    ex = base if base is not None else SituationExtras()
    env = view.fresh("env", now, env_max_age)
    if env is not None:
        d = env.data
        for key, valid in (("co2_ppm", "co2_valid"), ("temp_c", "temp_valid"),
                           ("humidity_pct", "humidity_valid"), ("lux", "lux_valid")):
            if d.get(valid) is True and d.get(key) is not None:
                setattr(ex, key, float(d[key]))
    ex.weather = weather_from_sample(getattr(view, "weather", None), now, cfg)
    return ex


def is_decision_tick(result: TickResult) -> bool:
    return any(isinstance(a, str) and a.startswith("route:") for a in result.actions)


def routed_cause(result: TickResult) -> str | None:
    for a in result.actions:
        if isinstance(a, str) and a.startswith("route:"):
            return a.split(":", 1)[1]
    return result.cause


def local_clock(ts: float, utc_offset_hours: float) -> tuple[float, int, int]:
    """(hour_local, weekday, month)."""
    dt = datetime.fromtimestamp(ts, timezone(timedelta(hours=utc_offset_hours)))
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    return hour, dt.weekday(), dt.month


def situation_from_tick(frame: SensorFrame, result: TickResult, extras: SituationExtras | None,
                        cfg: dict[str, Any], *, profile: ProfileSummary | None = None) -> Situation:
    ex = extras or SituationExtras()
    hour, weekday, month = local_clock(frame.now, float(cfg.get("local_utc_offset_hours", 9)))
    keystroke = frame.get("keystroke")
    elapsed = 0.0 if ex.session_started_ts is None else max(0.0, (frame.now - ex.session_started_ts) / 60.0)
    since = None if ex.last_intervention_ts is None else max(0.0, (frame.now - ex.last_intervention_ts) / 60.0)
    return Situation(
        ts=frame.now, hour_local=hour, weekday=weekday, month=month,
        posture_dist=dict(ex.posture_dist), posture_label=ex.posture_label,
        posture_hold_min=ex.posture_hold_min, slump_hold_min=ex.slump_hold_min,
        present=frame.present, drowsy=ex.drowsy, drowsy_count_30min=ex.drowsy_count_30min,
        co2_ppm=ex.co2_ppm, lux=ex.lux, temp_c=ex.temp_c, humidity_pct=ex.humidity_pct,
        pc_ratio=frame.pc_ratio, typing_active=bool(keystroke.available),
        c_fatigue=float(result.scores.c_fatigue), c_focus=float(result.scores.c_focus),
        session_elapsed_min=elapsed, since_last_intervention_min=since,
        after_bedtime=is_after_bedtime(hour, profile, cfg),
        weather=ex.weather,
    )


def fsm_record(result: TickResult, *, retry: bool = False) -> dict[str, Any]:
    """로그 `fsm` 객체."""
    state = getattr(result.state, "value", result.state)
    return {
        "state": state, "cause": routed_cause(result), "gate": getattr(result.gate, "value", result.gate),
        "retry": retry, "context": getattr(result.context, "value", result.context),
        "guards": list(getattr(result, "guards", []) or []),
    }


def arm_commands(device: dict[str, Any] | None) -> list[dict[str, Any]]:
    """arm 의 `device`(personalize.yaml) 를 control.yaml `actions` 와 같은 명령 spec 목록으로.

    조명은 `{target_id, brightness, undo: {brightness}}` → set_brightness 하나(색온도는 Pi 5 조명 어댑터가 아직
    set_color_temperature 를 받지 않아 보내지 않는다). 그 밖은 `{target_id, operation, value, undo_value}` 그대로.
    device 가 없으면(화면·음성·휴식 arm) 빈 목록 — 제어 명령 없이 카드·안내만 나간다.
    """
    if not device:
        return []
    if "operation" in device:
        return [{"target_id": device["target_id"], "operation": device["operation"],
                 "value": device.get("value"), "undo_value": device.get("undo_value")}]
    out: list[dict[str, Any]] = []
    undo = device.get("undo") or {}
    if "brightness" in device:
        out.append({"target_id": device["target_id"], "operation": "set_brightness",
                    "value": device["brightness"], "undo_value": undo.get("brightness")})
    return out

