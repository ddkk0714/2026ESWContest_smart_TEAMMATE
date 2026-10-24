"""자세판정 시뮬(tools/posture_sim) — 합성 격자 + 오라클 자세로 tofsim·torch 없이 돈다.

1) 책상 재실 규칙: 방 안(where: room) 자리비움은 mmWave 가 먼 거리로 잡아도 부재, OR(이전 hub) 은 헛재실.
2) 격자가 UART 0x05 인코드·디코드·조립을 지나 모델에 닿는다(표본 수 = 프레임 수).
"""
from __future__ import annotations

from tools.posture_sim.mmwave import presence_decision
from tools.posture_sim.scenario import SCENARIO_DEFAULTS, deep_merge, load_sensors, start_epoch

SEGMENTS = [
    {"kind": "away", "seconds": 6},
    {"kind": "upright", "seconds": 120, "typing": True},
    {"kind": "away", "seconds": 180, "where": "room"},
    {"kind": "face_down", "seconds": 120, "typing": False},
    {"kind": "away", "seconds": 120, "where": "out"},
    {"kind": "upright", "seconds": 60, "typing": True},
]


def _scenario(priority="mmwave"):
    sc = deep_merge(SCENARIO_DEFAULTS, {"name": f"t_{priority}", "seed": 3, "segments": SEGMENTS})
    sc["sensors_resolved"] = load_sensors({"presence": {"priority": priority}})
    sc["t0"] = start_epoch("2026-10-23", "14:00")
    return sc


def _run(tmp_path, priority):
    from tools.posture_sim.runner import run_posture
    from tools.posture_sim.synthetic import SyntheticPerson
    sc = _scenario(priority)
    src = SyntheticPerson(sc["segments"], seed=sc["seed"], fps=10.0)
    return run_posture(sc, out_dir=str(tmp_path), posture="oracle", source=src, progress=False)


def test_room_absence_is_absent_with_mmwave_gate(tmp_path):
    res = _run(tmp_path, "mmwave")
    eps = {e["where"]: e for e in res["presence"]["away_episodes"]}
    assert eps["room"]["false_present_s"] <= 3                  # 레이더는 방 안 사람을 보지만 거리로 책상 부재
    assert eps["out"]["absent_after_s"] <= 20                   # 무인 판정 지연(leave_hold 15 s) 안
    assert res["presence"]["still_person_miss_s"] <= 10         # 엎드려도 재실(가까운 거리)
    assert res["presence"]["decision"]["acc"] > 0.95
    assert res["posture"]["acc_sec"] > 0.9


def test_or_rule_keeps_room_absence_present(tmp_path):
    res = _run(tmp_path, "any")
    eps = {e["where"]: e for e in res["presence"]["away_episodes"]}
    assert eps["room"]["false_present_s"] > 150                 # 이전 hub(OR): 방 안에 있으면 계속 재실


def test_presence_decision_rule():
    assert presence_decision(None, True) == (True, "tof")
    assert presence_decision({"present": False, "valid": True}, True)[0] is False
    assert presence_decision({"present": True, "distance_cm": 300, "valid": True}, True)[0] is False
    assert presence_decision({"present": True, "distance_cm": 80, "valid": True}, False)[0] is True
    assert presence_decision({"present": True, "distance_cm": None, "valid": True}, False)[0] is False
    assert presence_decision({"present": False}, True, priority="any")[0] is True
