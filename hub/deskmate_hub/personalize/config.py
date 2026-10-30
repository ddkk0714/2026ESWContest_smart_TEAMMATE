"""personalize.yaml 로더. 임계값·prior·기기 값은 이 파일에서만 온다."""
from __future__ import annotations

import json
import os
import pkgutil
from typing import Any

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
DEFAULT_PATH = os.path.join(_CONFIG_DIR, "personalize.yaml")


def load_personalize_config(path: str | None = None) -> dict[str, Any]:
    """personalize.yaml 을 dict 로. path 미지정 시 패키지 기본값(YAML, 없으면 빌드가 만든 JSON)."""
    if path is not None:
        import yaml

        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    try:
        config = pkgutil.get_data("deskmate_hub", "config/personalize.json")
    except OSError:
        config = None
    if config is not None:
        return json.loads(config.decode("utf-8"))

    import yaml

    with open(DEFAULT_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)
