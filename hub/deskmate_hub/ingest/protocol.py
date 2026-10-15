"""MQTT 토픽·payload 파싱 (paho 의존 없음 — 테스트와 UART 브리지가 공유)."""
from __future__ import annotations

import json
import math

from .cache import Sample

TOPIC_SENSOR = "deskmate/sensor/#"
TOPIC_FEEDBACK = "deskmate/feedback/user"
TOPIC_STATE = "deskmate/state/phase"
TOPIC_REQUEST = "deskmate/interaction/request"
TOPIC_HEALTH = "deskmate/health/hub"
TOPIC_CONTROL_CMD = "deskmate/control/cmd"
TOPIC_CONTROL_RESULT = "deskmate/control/result"

_KIND_BY_TOPIC = {"mmwave": "mmwave", "env": "env", "keystroke": "keystroke", "tof": "tof"}

_TOF_POSTURES = {"upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away", "unknown"}
_TOF_NUMBERS = {"motion_score", "baseline_deviation", "posture_confidence", "head_delta_mm",
                "nod_rate_hz", "coverage_ratio", "grid_width", "grid_height", "valid_zones"}


def _finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _tof_features(data: dict) -> dict | None:
    # Keep only scalar features; unknown fields, including depth arrays, never reach the cache.
    if type(data.get("present")) is not bool or type(data.get("valid")) is not bool:
        return None
    posture = data.get("posture")
    if not isinstance(posture, str) or posture not in _TOF_POSTURES:
        return None
    out = {"present": data["present"], "valid": data["valid"], "posture": posture}
    for key in ("baseline_ready",):
        if key in data:
            if type(data[key]) is not bool:
                return None
            out[key] = data[key]
    for key in _TOF_NUMBERS & data.keys():
        value = data[key]
        if value is None:
            out[key] = None
            continue
        if not _finite_number(value):
            return None
        if key in ("motion_score", "posture_confidence", "coverage_ratio") and not 0 <= value <= 1:
            return None
        if key in ("baseline_deviation", "nod_rate_hz") and value < 0:
            return None
        if key in ("grid_width", "grid_height", "valid_zones") and (type(value) is not int or value < 0):
            return None
        out[key] = value
    return out


def parse_sensor_message(topic: str, payload: bytes, received: float) -> tuple[str, Sample] | None:
    """토픽·payload → (kind, Sample). 계약 밖이면 None."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] != "deskmate" or parts[1] != "sensor":
        return None
    kind = _KIND_BY_TOPIC.get(parts[2])
    if kind is None:
        return None
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else body  # 평면 payload 허용
    ts = body.get("ts")
    seq = body.get("seq")
    if kind == "tof":
        if len(parts) != 4 or not parts[3] or body.get("schema_version", "1.0") != "1.0":
            return None
        data = _tof_features(data)
        if data is None or not _finite_number(ts):
            return None
    return kind, Sample(
        received=received,
        ts=float(ts) if isinstance(ts, (int, float)) else received,
        seq=int(seq) if isinstance(seq, int) else None,
        data=data,
    )
