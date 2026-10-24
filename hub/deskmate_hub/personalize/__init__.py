"""사용자 맞춤 개입 선택 정책(3층) — docs/personalization-spec.md.

    1층 규칙 FSM(inference/)          : 상태·cause·gate                 ← 수정 없음
    2층 사용자 프로필(profile.py)      : 일 단위 생활습관 통계, JSONL
    3층 개입 선택 정책(policy.py)      : 컨텍스트 밴딧(Thompson), 순수 Python(linalg.py) — Pi 4 에서도 돈다
    기록(record.py·log.py)             : live 가 결정·피드백·10 분 뒤 결과를 한 줄로
    첫 설정(onboarding.py)             : deskmate/profile/user → 전역 prior·후보 제외·취침 시각 (2026-10-25)
    암묵 신호(signals.py)              : 밝힘 뒤 직접 어둡게 함(음) · 깨우기 성공(효과 축) (2026-10-25)

`personalization/`(2단계 관찰 모델·개인 head)과는 별개 패키지다.
"""
from .adapter import SituationExtras, extras_from_cache, fsm_record, is_decision_tick, situation_from_tick
from .config import load_personalize_config
from .weather import weather_from_sample
from .features import FEATURE_NAMES, POSTURE_LABELS, Situation, vectorize
from .log import PersonalizeLogger, read_records
from .record import EpisodeRecorder
from .onboarding import TOPIC_PROFILE, parse_profile
from .signals import ImplicitDimTracker, WakeTracker
from .rewards import REWARD_ACCEPT, reward_effect

from .policy import Decision, InterventionPolicy
from .profile import DailyStats, ProfileSummary, aggregate_daily, build_profile, summarize

__all__ = [
    "SituationExtras", "extras_from_cache", "fsm_record", "is_decision_tick", "situation_from_tick",
    "weather_from_sample",
    "load_personalize_config",
    "FEATURE_NAMES", "POSTURE_LABELS", "Situation", "vectorize",
    "PersonalizeLogger", "read_records",
    "Decision", "InterventionPolicy", "EpisodeRecorder", "REWARD_ACCEPT", "reward_effect",
    "TOPIC_PROFILE", "parse_profile", "ImplicitDimTracker", "WakeTracker",
    "DailyStats", "ProfileSummary", "aggregate_daily", "build_profile", "summarize",
]
