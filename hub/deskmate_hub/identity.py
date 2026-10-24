"""지금 이 자리의 사람(프로필 id) — 개인화 정책 상태(personalize policy.json)·반응 학습 상태(learning reaction.json)·
피로 보정이 같은 id 로 파일을 나눈다. 2026-10-25: 설정값(personalize.yaml·learning.yaml 의 profile_id, 기본 "default") 그대로.
개인화 신호 브랜치(feat/board-personal-signals)의 active_profile_id 가 들어오면 이 함수 몸통만 바꾸면 된다.
표준 라이브러리만.
"""
from __future__ import annotations

from typing import Any

DEFAULT_PROFILE_ID = "default"


def active_profile_id(*cfgs: dict[str, Any] | None) -> str:
    """설정들 중 처음 나오는 비어 있지 않은 profile_id, 없으면 "default". 파일 이름에 쓰므로 공백·경로 구분자는 뺀다."""
    for cfg in cfgs:
        if not cfg:
            continue
        value = cfg.get("profile_id")
        if value is None or str(value).strip() == "":
            continue
        return "".join(ch for ch in str(value).strip() if ch not in "/\\ \t\r\n") or DEFAULT_PROFILE_ID
    return DEFAULT_PROFILE_ID
