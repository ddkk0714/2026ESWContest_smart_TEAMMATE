"""FSM 설정 로더. 임계값·가중치·타이머는 config/fsm.yaml 에서만 온다."""
from __future__ import annotations

import os
from typing import Any

import yaml

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
DEFAULT_PATH = os.path.join(_CONFIG_DIR, "fsm.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    """fsm.yaml 을 읽어 dict 로 반환. path 미지정 시 패키지 기본 설정."""
    with open(path or DEFAULT_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)
