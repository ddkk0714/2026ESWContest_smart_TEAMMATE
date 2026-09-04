"""FSM 설정 로더. 임계값·가중치·타이머는 config/fsm.yaml 에서만 온다."""
from __future__ import annotations

import json
import os
import pkgutil
from typing import Any

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
DEFAULT_PATH = os.path.join(_CONFIG_DIR, "fsm.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    """fsm.yaml 을 읽어 dict 로 반환. path 미지정 시 패키지 기본 설정."""
    if path is not None:
        import yaml

        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    # Atlas builds derive this JSON from fsm.yaml and place it in the embedded
    # zip payload.  The restricted runtime omits modules required by PyYAML, so
    # keep YAML parsing in the build/development environment only.
    try:
        config = pkgutil.get_data("deskmate_hub", "config/fsm.json")
    except OSError:
        config = None
    if config is not None:
        return json.loads(config.decode("utf-8"))

    import yaml

    with open(DEFAULT_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)
