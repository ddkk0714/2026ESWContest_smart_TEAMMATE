"""각본·반응·기기 설정 로딩. 모든 확률·임계값·효과 계수는 yaml 에 있고 코드에는 없다.

    scenario = load_scenario("hub/tools/life_sim/scenarios/a1_wake_first.yaml")

각본 파일은 config/reaction.yaml·config/devices.yaml 의 기본값 위에 `reaction:`·`devices:` 블록을 깊은 병합으로 덮는다.
`persona:` 가 있으면 reaction.personas[<persona>] 도 그 사이에 끼운다(기본 < 페르소나 < 각본).
"""
from __future__ import annotations

import copy
import datetime as _dt
import os
from typing import Any

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(HERE, "config")
SCENARIO_DIR = os.path.join(HERE, "scenarios")

SCENARIO_DEFAULTS: dict[str, Any] = {
    "name": "unnamed", "seed": 1, "persona": "default", "fps": 10, "backend": "mujoco", "placement": "desk",
    "clutter": 4, "scene": "rich", "l8_params": None, "require_ok_placement": True,
    "hub": {"fsm_config": None, "tick_sec": None},
    "date": "2026-10-22", "start_clock": "14:00",
    "env": {"co2_ppm": 850.0, "temp_c": 24.0, "humidity_pct": 45.0, "lux": 350.0, "pm25": 20.0, "window_open": False},
    "weather": {"temp_c": 18.0, "humidity": 55, "wind_ms": 2.0, "condition": "clear", "season": "autumn",
                "source": "sim"},
    "segments": [],
    "reaction": {}, "devices": {},
    # policy.engine: hub(= hub/deskmate_hub/personalize 컨텍스트 밴딧, 기본) | simple(life_sim 간이 정책, 대역) | none
    # policy.profile: none | synth(A: 합성 30 일 학습) | global(B: 전역 prior)
    "policy": {"engine": "hub", "profile": "none", "synth_persona": None, "days": 30},
    "notes": "",
}


def deep_merge(base: dict[str, Any], over: dict[str, Any] | None) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_defaults() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    reaction = load_yaml(os.path.join(CONFIG_DIR, "reaction.yaml"))
    devices = load_yaml(os.path.join(CONFIG_DIR, "devices.yaml"))
    policy = load_yaml(os.path.join(CONFIG_DIR, "policy_simple.yaml")) if os.path.exists(
        os.path.join(CONFIG_DIR, "policy_simple.yaml")) else {}
    return reaction, devices, policy


def resolve_reaction(reaction_cfg: dict[str, Any], persona: str, override: dict[str, Any] | None) -> dict[str, Any]:
    base = copy.deepcopy(reaction_cfg.get("defaults") or {})
    persona_cfg = (reaction_cfg.get("personas") or {}).get(persona) or {}
    return deep_merge(deep_merge(base, persona_cfg), override)


def load_scenario(path: str) -> dict[str, Any]:
    raw = load_yaml(path)
    sc = deep_merge(SCENARIO_DEFAULTS, raw)
    if "name" not in raw:
        sc["name"] = os.path.splitext(os.path.basename(path))[0]
    reaction_cfg, devices_cfg, policy_cfg = load_defaults()
    sc["reaction_resolved"] = resolve_reaction(reaction_cfg, sc["persona"], raw.get("reaction"))
    sc["devices_resolved"] = deep_merge(devices_cfg, raw.get("devices"))
    sc["policy_resolved"] = deep_merge(policy_cfg, raw.get("policy"))
    sc["path"] = os.path.abspath(path)
    sc["t0"] = start_epoch(sc["date"], sc["start_clock"])
    if not sc["segments"]:
        raise ValueError(f"{path}: segments 가 비어 있다")
    return sc


def start_epoch(date: str, clock: str) -> float:
    """각본의 가상 시계 시작(로컬 시간대 epoch). 시간대 버킷·취침 규칙이 하루 중 시각을 쓴다."""
    y, m, d = (int(x) for x in str(date).split("-"))
    hh, mm = (int(x) for x in str(clock).split(":")[:2])
    return _dt.datetime(y, m, d, hh, mm).timestamp()


def fsm_config_path(name: str | None) -> str | None:
    """`fsm.yaml`/`fsm.demo.yaml` 같은 이름은 패키지 config/ 에서, 경로면 그대로."""
    if not name:
        return None
    if os.path.exists(name):
        return name
    from deskmate_hub.inference.config import _CONFIG_DIR
    cand = os.path.join(_CONFIG_DIR, name)
    return cand if os.path.exists(cand) else name
