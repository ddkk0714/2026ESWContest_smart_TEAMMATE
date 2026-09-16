"""제어 모듈 — ACTION_ENV → 기기 명령(가역 동작) → 결과 수신·타임아웃·되돌리기.

계약: data-spec §10, mqtt-topics `deskmate/control/cmd`·`deskmate/control/result`. 설정: config/control.yaml.
실기 플러그 모델은 미선정이라 어댑터는 MQTT(외부 응답기) 또는 MockPlugAdapter(프로세스 내 즉시 성공)다.
"""
import json
import os
import pkgutil
from typing import Any

from .dispatcher import ControlCommand, ControlDispatcher, Episode, MockPlugAdapter

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
DEFAULT_CONTROL_PATH = os.path.join(_CONFIG_DIR, "control.yaml")


def load_control_config(path: str | None = None) -> dict[str, Any]:
    if path is None:
        try:
            raw = pkgutil.get_data("deskmate_hub", "config/control.json")
        except OSError:
            raw = None
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        path = DEFAULT_CONTROL_PATH
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


__all__ = ["ControlCommand", "ControlDispatcher", "Episode", "MockPlugAdapter", "load_control_config", "DEFAULT_CONTROL_PATH"]
