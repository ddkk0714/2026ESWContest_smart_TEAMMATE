"""시연 프로파일 오버레이 — 환경변수 `DESKMATE_PROFILE=demo-a|demo-b` 면 `config/profiles/<이름>.yaml` 의 `fsm:`·`ingest:` 절을
기본 fsm.yaml·ingest.yaml 위에 깊은 병합으로 얹는다(2026-10-24). 환경변수가 없으면 아무것도 바꾸지 않는다.

보드(ATLAS 제한 Python, PyYAML 없음)에서는 `hub/atlas/tools/build_payload.py` 가 profiles/*.yaml 을 JSON 으로 바꿔 payload 에
넣고 여기서 pkgutil 로 읽는다 — fsm.json·ingest.json 과 같은 방식. 켜는 법은 hub/atlas/README.md(hub.env 한 줄).
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import copy
import json
import os
import pkgutil
import re
from typing import Any, Callable

PROFILE_ENV = "DESKMATE_PROFILE"
PROFILES_DIR = os.path.join(os.path.dirname(__file__), "config", "profiles")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def profile_name(environ: dict[str, str] | None = None) -> str | None:
    """환경변수의 프로파일 이름. 비어 있으면 None(= 기본 설정 그대로)."""
    name = (os.environ if environ is None else environ).get(PROFILE_ENV, "").strip()
    return name or None


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """base 위에 overlay 를 얹은 새 dict. 둘 다 dict 인 키만 재귀하고, 그 외(값·리스트)는 overlay 가 통째로 덮는다. 원본은 안 바꾼다."""
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_profile(name: str) -> dict[str, Any]:
    """profiles/<name>.json(보드 payload) 또는 profiles/<name>.yaml(PC) → {"fsm": {...}, "ingest": {...}}."""
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"[profile] {PROFILE_ENV}={name!r}: 이름은 영문·숫자·'-' 만 (예: demo-a)")
    try:
        raw = pkgutil.get_data("deskmate_hub", f"config/profiles/{name}.json")
    except OSError:
        raw = None
    if raw is not None:
        data = json.loads(raw.decode("utf-8"))
    else:
        path = os.path.join(PROFILES_DIR, f"{name}.yaml")
        if not os.path.exists(path):
            raise FileNotFoundError(f"[profile] {PROFILE_ENV}={name}: {path} 없음 (있는 것: {', '.join(available()) or '-'})")
        import yaml

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"[profile] {name}: fsm:·ingest: 절을 가진 매핑이어야 한다")
    for section in ("fsm", "ingest"):
        if data.get(section) is not None and not isinstance(data[section], dict):
            raise ValueError(f"[profile] {name}: {section}: 절은 매핑이어야 한다")
    return data


def available() -> list[str]:
    """PC 에서 고를 수 있는 프로파일 이름(profiles/*.yaml). 보드 payload 안에서는 디렉터리가 없어 빈 목록."""
    try:
        return sorted(f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".yaml"))
    except OSError:
        return []


def apply_profile(fsm_cfg: dict[str, Any], ingest_cfg: dict[str, Any], name: str | None = None, *,
                  environ: dict[str, str] | None = None, log: Callable[[str], None] | None = None,
                  ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """프로파일이 지정돼 있으면 (fsm, ingest) 위에 얹은 새 설정과 이름을, 아니면 받은 설정 그대로와 None 을 돌려준다.

    name 이 None 이면 환경변수를 보고, "" 이면 환경변수를 무시하고 적용하지 않는다(이미 얹은 설정을 다시 넘길 때).
    """
    name = profile_name(environ) if name is None else (name.strip() or None)
    if not name:
        return fsm_cfg, ingest_cfg, None
    prof = load_profile(name)
    fsm = deep_merge(fsm_cfg, prof.get("fsm") or {})
    ingest = deep_merge(ingest_cfg, prof.get("ingest") or {})
    if log is not None:
        log(f"[profile] {PROFILE_ENV}={name} — fsm {_summary(prof.get('fsm') or {})}; ingest {_summary(prof.get('ingest') or {})} "
            "(시연 전용 오버레이, 실측 기록에 쓰지 말 것)")
    return fsm, ingest, name


def _summary(overlay: dict[str, Any], prefix: str = "") -> str:
    parts: list[str] = []
    for key, value in overlay.items():
        if isinstance(value, dict):
            parts.append(_summary(value, f"{prefix}{key}."))
        else:
            parts.append(f"{prefix}{key}={value}")
    return " ".join(p for p in parts if p) or "-"
