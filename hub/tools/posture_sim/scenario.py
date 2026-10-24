"""자세판정 시뮬 각본 로딩. 센서 설정 기본값은 config/sensors.yaml, 각본의 `sensors:` 블록이 깊은 병합으로 덮는다.

    sc = load_scenario("hub/tools/posture_sim/scenarios/posture_tour.yaml")

각본 필드: name · seed · fps · backend(mujoco|numpy) · placement · clutter · scene · l8_params · require_ok_placement ·
start_clock · date · segments[] (person.py 구간 문법) · sensors{} · notes. 자리비움 구간은 `where: room | out` 을 가질 수 있다.
전체 시뮬(life_sim)은 이 로더의 deep_merge·start_epoch·load_sensors 를 재사용한다.
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
    "date": "2026-10-22", "start_clock": "14:00", "away_where": "out",
    "segments": [], "sensors": {}, "notes": "",
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


def load_sensors(override: dict[str, Any] | None = None) -> dict[str, Any]:
    return deep_merge(load_yaml(os.path.join(CONFIG_DIR, "sensors.yaml")), override)


def start_epoch(date: str, clock: str) -> float:
    """각본의 가상 시계 시작(로컬 시간대 epoch)."""
    y, m, d = (int(x) for x in str(date).split("-"))
    hh, mm = (int(x) for x in str(clock).split(":")[:2])
    return _dt.datetime(y, m, d, hh, mm).timestamp()


def load_scenario(path: str) -> dict[str, Any]:
    raw = load_yaml(path)
    sc = deep_merge(SCENARIO_DEFAULTS, raw)
    if "name" not in raw:
        sc["name"] = os.path.splitext(os.path.basename(path))[0]
    sc["sensors_resolved"] = load_sensors(raw.get("sensors"))
    sc["path"] = os.path.abspath(path)
    sc["t0"] = start_epoch(sc["date"], sc["start_clock"])
    if not sc["segments"]:
        raise ValueError(f"{path}: segments 가 비어 있다")
    return sc
