"""피로 모델 입력 — state/phase tick 들로 5분 창 특징을 만든다. 표준 라이브러리만(Pi 4 제한 Python).

학습(ml/training/fatigue_dataset.py)과 기기 추론이 이 함수 하나를 같이 쓴다. tick 은
`tools/fatigue_logger.py` 가 남기는 모양(`ts`, `c_fatigue`, `sensor_summary` …)이고 hub 안에서는
state envelope 의 `data` 에 `ts` 를 붙인 것과 같다.

값이 없으면 None 이다(0 으로 채우지 않는다 — 0 은 '타이핑 리듬 완벽' 처럼 다른 뜻이 된다).
RELATIVE 특징은 세션 처음 baseline_sec 구간 대비 차이로 바꾼다. 사람마다 다른 평소 값(타자 속도,
기대는 습관)을 지우고 '세션 안에서 얼마나 변했나' 만 남기려는 것이다.
"""
from __future__ import annotations

import math
import time
from typing import Any, Iterable

WINDOW_SEC = 300.0
BASELINE_SEC = 600.0

# ToF MLP 10클래스(config/tof_mlp.json) → 데이터가 적어서 6묶음으로 줄인다. away 는 재실 쪽에서 본다.
POSTURE_GROUPS = {
    "upright": "upright",
    "lean_forward": "forward", "forward_head": "forward",
    "head_down": "head_down", "face_down": "head_down",
    "chin_rest": "chin_rest",
    "recline": "recline",
    "lean_left": "side", "lean_right": "side",
}
_GROUPS = ("upright", "forward", "head_down", "chin_rest", "recline", "side")

SENSOR_FEATURES = (
    *(f"pos_{g}" for g in _GROUPS),
    "pos_switch_per_min", "tof_nod_rate", "tof_drowsy", "tof_motion",
    "mm_motion", "mm_motion_sd", "mm_resp_bpm", "mm_heart_bpm",
    "ks_events_per_min", "ks_dwell_mean", "ks_dwell_sd", "ks_flight_mean", "ks_flight_cv",
    "ks_idle", "ks_correction",
    "env_co2", "env_co2_slope", "env_temp", "env_humidity", "env_lux",
    "present_ratio", "fsm_c_fatigue",
)
CONTEXT_FEATURES = (
    "elapsed_min", "hour_sin", "hour_cos",
    "kss_start", "sleep_last_night", "sleep_debt", "chronotype",
)
FEATURES = SENSOR_FEATURES + CONTEXT_FEATURES
# 사람·자리마다 절대값이 크게 다른 것. 환경은 절대 농도 자체가 근거라 그대로 둔다.
RELATIVE = frozenset({
    "pos_upright", "pos_forward", "pos_head_down", "pos_switch_per_min", "tof_motion",
    "mm_motion", "mm_resp_bpm", "mm_heart_bpm",
    "ks_events_per_min", "ks_dwell_mean", "ks_flight_mean", "ks_flight_cv", "ks_idle", "ks_correction",
})
CHRONOTYPE = {"morning": -1.0, "neither": 0.0, "evening": 1.0}


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _sd(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _slope_per_min(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    mt = sum(t for t, _ in points) / len(points)
    mv = sum(v for _, v in points) / len(points)
    den = sum((t - mt) ** 2 for t, _ in points)
    if den <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in points) / den * 60.0


def sensor_features(ticks: Iterable[dict[str, Any]]) -> dict[str, float | None]:
    """tick 묶음(보통 5분) → SENSOR_FEATURES. 시간 순서로 받는다."""
    ticks = list(ticks)
    out: dict[str, float | None] = {k: None for k in SENSOR_FEATURES}
    if not ticks:
        return out
    postures: list[str] = []
    nod, drowsy, tmotion = [], [], []
    mm_motion, resp, heart = [], [], []
    ks_rate, dwell, dwell_sd, flight, flight_cv, idle, corr = [], [], [], [], [], [], []
    co2, temp, hum, lux, cfat = [], [], [], [], []
    present = []
    for t in ticks:
        s = t.get("sensor_summary") or {}
        ts = _num(t.get("ts"))
        if "present" in s:
            present.append(1.0 if s["present"] else 0.0)
        if _num(t.get("c_fatigue")) is not None:
            cfat.append(float(t["c_fatigue"]))
        tof = s.get("tof") or {}
        group = POSTURE_GROUPS.get(str(tof.get("posture")))
        if group is not None:
            postures.append(group)
        for src, dst in ((tof.get("nod_rate_hz"), nod), (tof.get("motion_score"), tmotion)):
            if _num(src) is not None:
                dst.append(float(src))
        if isinstance(tof.get("drowsy"), bool):
            drowsy.append(1.0 if tof["drowsy"] else 0.0)
        mm = s.get("mmwave") or {}
        if _num(mm.get("motion_level")) is not None:
            mm_motion.append(float(mm["motion_level"]))
        if mm.get("resp_valid") is True and _num(mm.get("resp_bpm")):
            resp.append(float(mm["resp_bpm"]))
        if mm.get("heart_valid") is True and _num(mm.get("heart_bpm")):
            heart.append(float(mm["heart_bpm"]))
        ks = s.get("keystroke") or {}
        if ks.get("valid") is True:
            win, events = _num(ks.get("window_s")), _num(ks.get("event_count"))
            if win and events is not None:
                ks_rate.append(events / win * 60.0)
            for key, dst in (("dwell_mean_ms", dwell), ("dwell_std_ms", dwell_sd), ("flight_mean_ms", flight),
                             ("idle_ratio", idle), ("correction_rate", corr)):
                if _num(ks.get(key)) is not None:
                    dst.append(float(ks[key]))
            fm, fs = _num(ks.get("flight_mean_ms")), _num(ks.get("flight_std_ms"))
            cv = _num(ks.get("flight_cv"))
            if cv is None and fm and fs is not None:
                cv = fs / fm
            if cv is not None:
                flight_cv.append(cv)
        for key, dst in (("temp_c", temp), ("humidity_pct", hum), ("lux", lux)):
            if _num(s.get(key)) is not None:
                dst.append(float(s[key]))
        if _num(s.get("co2_ppm")) is not None:
            co2.append((ts if ts is not None else float(len(co2)) * 10.0, float(s["co2_ppm"])))

    if postures:
        for g in _GROUPS:
            out[f"pos_{g}"] = postures.count(g) / len(postures)
        span_min = len(ticks) * 10.0 / 60.0
        out["pos_switch_per_min"] = sum(a != b for a, b in zip(postures, postures[1:])) / span_min
    out["tof_nod_rate"] = _mean(nod)
    out["tof_drowsy"] = _mean(drowsy)
    out["tof_motion"] = _mean(tmotion)
    out["mm_motion"] = _mean(mm_motion)
    out["mm_motion_sd"] = _sd(mm_motion)
    out["mm_resp_bpm"] = _median(resp)
    out["mm_heart_bpm"] = _median(heart)
    out["ks_events_per_min"] = _mean(ks_rate)
    out["ks_dwell_mean"] = _mean(dwell)
    out["ks_dwell_sd"] = _mean(dwell_sd)
    out["ks_flight_mean"] = _mean(flight)
    out["ks_flight_cv"] = _mean(flight_cv)
    out["ks_idle"] = _mean(idle)
    out["ks_correction"] = _mean(corr)
    out["env_co2"] = _mean([v for _, v in co2])
    out["env_co2_slope"] = _slope_per_min(co2)
    out["env_temp"] = _mean(temp)
    out["env_humidity"] = _mean(hum)
    out["env_lux"] = _mean(lux)
    out["present_ratio"] = _mean(present)
    out["fsm_c_fatigue"] = _mean(cfat)
    return out


def context_features(end_ts: float, session: dict[str, Any]) -> dict[str, float | None]:
    """세션 시작 답변(session_start 기록)과 시각으로 만드는 특징."""
    start = _num(session.get("ts"))
    profile = session.get("profile") or {}
    hour = time.localtime(end_ts)
    h = hour.tm_hour + hour.tm_min / 60.0
    sleep = _num(session.get("sleep_hours_last_night"))
    usual = _num(profile.get("usual_sleep_h"))
    return {
        "elapsed_min": (end_ts - start) / 60.0 if start is not None else None,
        "hour_sin": math.sin(2 * math.pi * h / 24.0),
        "hour_cos": math.cos(2 * math.pi * h / 24.0),
        "kss_start": _num(session.get("kss")),
        "sleep_last_night": sleep,
        "sleep_debt": usual - sleep if usual is not None and sleep is not None else None,
        "chronotype": CHRONOTYPE.get(str(profile.get("chronotype"))),
    }


def window_features(ticks: list[dict[str, Any]], end_ts: float, session: dict[str, Any], *,
                    window_sec: float = WINDOW_SEC, baseline_sec: float = BASELINE_SEC) -> dict[str, float | None]:
    """end_ts 로 끝나는 창의 FEATURES. ticks 는 세션 전체(시간순), session 은 session_start 기록."""
    start = _num(session.get("ts"))
    window = [t for t in ticks if _num(t.get("ts")) is not None and end_ts - window_sec < t["ts"] <= end_ts]
    out = sensor_features(window)
    base = None
    if start is not None:
        early = [t for t in ticks if _num(t.get("ts")) is not None and start <= t["ts"] < start + baseline_sec]
        # 기준 구간이 아직 다 안 찼으면(시작 10분 이내) 비교할 수 없다.
        if early and end_ts >= start + baseline_sec:
            base = sensor_features(early)
    for key in RELATIVE:
        value, ref = out.get(key), base.get(key) if base else None
        out[key] = value - ref if value is not None and ref is not None else None
    out.update(context_features(end_ts, session))
    return out
