"""실센서 수신 → SensorFrame (ingest). MVP 2026-09-18 — MQTT 센서 토픽 경로.

    python -m deskmate_hub run --broker <ip>

UART 라인(Pi 4 native service 브리지) 입력은 통합 MVP(10-05)에서 같은 SensorCache 에 붙인다.
"""
import json
import os
import pkgutil
from typing import Any

from .cache import CacheView, Sample, SensorCache
from .mapping import SessionTracker, build_frame, pc_ratio, sensor_summary

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
DEFAULT_INGEST_PATH = os.path.join(_CONFIG_DIR, "ingest.yaml")


def load_ingest_config(path: str | None = None) -> dict[str, Any]:
    """config/ingest.yaml (Atlas 빌드에서는 ingest.json) 을 읽는다."""
    if path is None:
        try:
            raw = pkgutil.get_data("deskmate_hub", "config/ingest.json")
        except OSError:
            raw = None
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        path = DEFAULT_INGEST_PATH
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


__all__ = [
    "CacheView", "Sample", "SensorCache", "SessionTracker",
    "build_frame", "pc_ratio", "sensor_summary", "load_ingest_config", "DEFAULT_INGEST_PATH",
]
