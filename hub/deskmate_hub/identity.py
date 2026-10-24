"""지금 이 자리의 사람(프로필 id) — 개인화 정책 상태(personalize policy.json)·반응 학습 상태(learning reaction.json)·
피로 보정이 같은 id 로 파일을 나눈다. 2026-10-25: 설정값(personalize.yaml·learning.yaml 의 profile_id, 기본 "default") 그대로.
이 함수는 시작 기본값이다. 첫 설정 프로필(deskmate/profile/user)로 사람이 정해지면 personalize/record.py 가 사람별 정책으로 바꾸고
(profiles/active_profile.json 에 남겨 재시작 때 이어 감) LiveHub.active_profile_id·_follow_active_profile 이 반응 학습도 따라가게 한다.
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
