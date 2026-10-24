"""첫 설정(Pi 5 앱 온보딩) → 개입 선택의 기준(전역 prior). 순수 Python(Pi 4).

사용자 설계(2026-10-25): "응답은 많이 받지 않고, UI 처음 설정한 내용을 바탕으로 표준을 잡는다."
입력은 `deskmate/profile/user`(retain, QoS 1, 공통 envelope — docs/mqtt-topics.md). 받으면

  - personalize.yaml `onboarding.mapping` 표대로 arm 별 prior 가중치를 더하고(빼고), `disable` 에 적힌 arm 은 후보에서 뺀다
    (예: light_pref low → 밝은 조명 arm 수락 prior ↓·따뜻하고 어두운 조명 ↑, sound_pref off → 알림음·음성 안내 제외)
  - bedtime·wake_time 은 기존 취침 규칙(sleep·after_bedtime·quiet_hours)에 들어간다(default_bedtime 대신)
  - `keep_fields` 에 적힌 필드만 profiles/<profile_id>/user_profile.json 에 원자적으로 저장한다 — 재시작 뒤에도 유지

이름·연락처 같은 식별 정보는 스키마에 없고, 모르는 키는 버린다(CLAUDE.md §2-5). 로그에는 profile_id 만 남는다.
"""
from __future__ import annotations

import json
import os
from typing import Any

TOPIC_PROFILE = "deskmate/profile/user"
SCHEMA_NAME = "deskmate_user_profile"
ACTIVE_SCHEMA = "deskmate_active_profile"

ENUMS: dict[str, tuple[str, ...]] = {
    "age_band": ("10s", "20s", "30s", "40s", "50s+"),
    "light_pref": ("low", "mid", "high"),
    "sound_pref": ("off", "low", "mid", "high"),
    "wind_pref": ("off", "low", "mid", "high"),
    "placement": ("left", "center", "right"),
}
FOCUS_HELP = ("light", "sound", "wind", "break", "voice")
FOCUS_HELP_ALIASES = {"lamp": "light", "fan": "wind", "notify": "sound"}   # 제품 UI(V18) 별칭 → 표준 이름
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_ID_MAX = 64


def parse_hhmm(text: Any) -> float | None:
    """"HH:MM" → 시(소수). 형식이 틀리면 None."""
    if not isinstance(text, str) or ":" not in text:
        return None
    h, _, m = text.strip().partition(":")
    if not (h.isdigit() and m.isdigit()) or len(m) != 2:
        return None
    hour, minute = int(h), int(m)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour + minute / 60.0


def parse_profile(data: Any) -> dict[str, Any] | None:
    """앱이 보낸 data 를 검증해 아는 필드만 남긴다. 값이 틀린 필드는 버리고(나머지는 살린다), 하나도 없으면 None."""
    if not isinstance(data, dict):
        return None
    out: dict[str, Any] = {}
    pid = data.get("profile_id")
    if isinstance(pid, str) and 0 < len(pid.strip()) <= _ID_MAX and all(c in _ID_CHARS for c in pid.strip()):
        out["profile_id"] = pid.strip()
    for key, allowed in ENUMS.items():
        value = data.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
    sleep = data.get("sleep_hours")
    if isinstance(sleep, (int, float)) and not isinstance(sleep, bool) and 0.0 <= float(sleep) <= 24.0:
        out["sleep_hours"] = float(sleep)
    for key in ("bedtime", "wake_time"):
        if parse_hhmm(data.get(key)) is not None:
            out[key] = data[key].strip()
    helps = data.get("focus_help")
    if isinstance(helps, list):
        # 앱이 고른 순서(몰입 도움 순위)를 지키고, 별칭은 표준 이름으로, 모르는 값·중복은 버린다
        seen: list[str] = []
        for item in helps:
            name = FOCUS_HELP_ALIASES.get(item, item) if isinstance(item, str) else None
            if name in FOCUS_HELP and name not in seen:
                seen.append(name)
        out["focus_help"] = seen
    return out or None


def kept(profile: dict[str, Any], keep_fields: list[str] | tuple[str, ...] | None) -> dict[str, Any]:
    """저장·기록할 필드만(keep_fields). 없으면 전부."""
    if not keep_fields:
        return dict(profile)
    return {k: v for k, v in profile.items() if k in keep_fields}


def _mapping_entries(profile: dict[str, Any], ocfg: dict[str, Any]) -> list[dict[str, Any]]:
    """프로필 값에 해당하는 mapping 표의 항목들(적용 순서대로)."""
    mapping = ocfg.get("mapping") or {}
    entries: list[dict[str, Any]] = []
    for key in ("light_pref", "sound_pref", "wind_pref"):
        value = profile.get(key)
        if value is not None:
            entry = (mapping.get(key) or {}).get(value)
            if entry:
                entries.append(entry)
    for item in profile.get("focus_help") or ():
        entry = (mapping.get("focus_help") or {}).get(item)
        if entry:
            entries.append(entry)
    short = ocfg.get("sleep_short_hours")
    if profile.get("sleep_hours") is not None and short is not None and float(profile["sleep_hours"]) < float(short):
        entry = mapping.get("sleep_short")
        if entry:
            entries.append(entry)
    return entries


def profile_adjustments(profile: dict[str, Any], ocfg: dict[str, Any]
                        ) -> tuple[dict[str, dict[str, dict[str, float]]], set[str], list[str]]:
    """(prior 가산 {arm: {axis: {특징: Δ}}}, 후보에서 뺄 arm, 설명 문구 목록)."""
    deltas: dict[str, dict[str, dict[str, float]]] = {}
    disabled: set[str] = set()
    notes: list[str] = []
    for entry in _mapping_entries(profile, ocfg):
        for arm, axes in (entry.get("prior") or {}).items():
            for axis, feats in (axes or {}).items():
                acc = deltas.setdefault(arm, {}).setdefault(axis, {})
                for feat, delta in (feats or {}).items():
                    acc[feat] = acc.get(feat, 0.0) + float(delta)
        disabled.update(entry.get("disable") or ())
        if entry.get("note_ko"):
            notes.append(str(entry["note_ko"]))
    return deltas, disabled, notes


def merged_prior(base: dict[str, Any], deltas: dict[str, dict[str, dict[str, float]]]) -> dict[str, Any]:
    """전역 prior(personalize.yaml `prior`) + 프로필 가산. base 는 바꾸지 않는다."""
    out: dict[str, Any] = {}
    for arm in set(base) | set(deltas):
        axes: dict[str, dict[str, float]] = {}
        for axis in set((base.get(arm) or {})) | set(deltas.get(arm) or {}):
            feats = dict(((base.get(arm) or {}).get(axis)) or {})
            for feat, delta in ((deltas.get(arm) or {}).get(axis) or {}).items():
                feats[feat] = float(feats.get(feat, 0.0)) + float(delta)
            axes[axis] = feats
        out[arm] = axes
    return out


def quiet_hours_of(profile: dict[str, Any]) -> dict[str, float] | None:
    """bedtime~wake_time → quiet_hours {start, end}(시, 소수). 둘 중 하나라도 없으면 None."""
    bed, wake = parse_hhmm(profile.get("bedtime")), parse_hhmm(profile.get("wake_time"))
    if bed is None or wake is None:
        return None
    return {"start": bed, "end": wake}


def save_profile(path: str, profile: dict[str, Any], *, received_ts: float | None = None) -> None:
    """원자적 저장(tmp → os.replace). 저장 실패는 호출한 쪽이 로그만 남긴다."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    body = {"schema_name": SCHEMA_NAME, "schema_version": "1.0", "received_ts": received_ts, "profile": profile}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False)
    os.replace(tmp, path)


def save_active_profile(path: str, profile_id: str) -> None:
    """지금 사용자(profile_id 만). 재시작하면 이 사람의 정책으로 이어 간다."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"schema_name": ACTIVE_SCHEMA, "schema_version": "1.0", "profile_id": profile_id}, fh)
    os.replace(tmp, path)


def load_active_profile(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("schema_name") != ACTIVE_SCHEMA:
        return None
    parsed = parse_profile({"profile_id": body.get("profile_id")})
    return parsed.get("profile_id") if parsed else None


def load_profile(path: str) -> dict[str, Any] | None:
    """저장된 프로필. 없거나 손상이면 None."""
    try:
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("schema_name") != SCHEMA_NAME:
        return None
    return parse_profile(body.get("profile"))
