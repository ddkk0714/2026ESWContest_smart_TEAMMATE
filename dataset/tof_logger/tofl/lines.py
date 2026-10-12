"""ESP32 수집 펌웨어(env:tof_logger)가 USB 로 보내는 줄을 읽는다.

줄 형식은 firmware/esp32_sensor_node/src/tof_logger/TofLine.h. `{"t":` 로 시작하지 않는 줄
(부팅 로그·디버그 문구)과 깨진 줄은 버린다.
"""
from __future__ import annotations

import json

KINDS = ("tof", "tof_hello", "tof_err")


def parse_line(text: str | bytes) -> dict | None:
    """알아볼 수 있는 줄이면 dict, 아니면 None. tof 줄은 배열 길이까지 확인한다."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = text.strip()
    if not text.startswith('{"t":'):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("t") not in KINDS:
        return None
    if obj["t"] == "tof" and not _valid_frame(obj):
        return None
    return obj


def _valid_frame(obj: dict) -> bool:
    try:
        w, h = int(obj["w"]), int(obj["h"])
        d, st = obj["d"], obj["st"]
        int(obj["seq"]), int(obj["ms"])
    except (KeyError, TypeError, ValueError):
        return False
    return (w > 0 and h > 0 and isinstance(d, list) and isinstance(st, list)
            and len(d) == w * h and len(st) == w * h and isinstance(obj.get("rule"), dict))
