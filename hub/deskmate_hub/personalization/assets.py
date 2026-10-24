"""모델·메타데이터 파일 읽기 — 파일 경로가 있으면 그것, 없으면 패키지 안(보드 payload zip)에서 pkgutil 로.

personalization.yaml 의 model_path/metadata_path 가 `config/stage2/common.portable.json` 같은 패키지 상대 경로면 PC 에서는
deskmate_hub 패키지 디렉터리의 파일, 보드(ATLAS, 실행 파일 뒤 zip payload)에서는 zip 안 항목을 읽는다(fsm.json 과 같은 방식, 2026-10-25).
표준 라이브러리만.
"""
from __future__ import annotations

import os
import pkgutil


def read_asset(path) -> bytes:
    path = str(path)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()
    raw = None
    try:
        raw = pkgutil.get_data("deskmate_hub", path.replace("\\", "/"))
    except (OSError, ValueError):
        raw = None
    if raw is None:
        raise FileNotFoundError(path)
    return raw
