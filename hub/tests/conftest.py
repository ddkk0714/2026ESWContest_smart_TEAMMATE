"""hub 시험 공통 설정.

반응 학습(learning.yaml)은 Pi 4 배포판에서 켜져 있고 상태를 logs/profiles/<profile_id>/reaction.json 에 쓴다. 시험이 작업
디렉터리의 logs/ 를 읽고 써서 실행마다 결과가 달라지지 않게, LiveHub 가 읽는 learning 설정의 state_path·common_path 를
시험마다 tmp 로 돌린다. 켜짐·울타리 값은 그대로라 기본 LiveHub 시험은 보드 기본(학습 켬) 경로로 돈다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _learning_state_in_tmp(tmp_path, monkeypatch):
    try:
        import deskmate_hub.live as live
    except ImportError:  # pragma: no cover
        return
    if live.load_learning_config is None:  # pragma: no cover
        return
    real = live.load_learning_config

    def isolated(path=None):
        cfg = dict(real(path) or {})
        if cfg.get("state_path"):
            cfg["state_path"] = str(tmp_path / "learning-state" / "{profile_id}" / "reaction.json")
        if cfg.get("common_path"):
            cfg["common_path"] = str(tmp_path / "learning-common" / "learner_common_v1.json")
        return cfg

    monkeypatch.setattr(live, "load_learning_config", isolated)
