"""규칙 FSM 상태 전이 단위 테스트 — 합성 입력으로 실기기 없이 검증한다.

타이머 기본값(config/fsm.yaml): baseline 300s, elevated/confirm/break hold 180s,
rest 300s, absent 600s.
"""
from __future__ import annotations

import pytest

from deskmate_hub.inference import Context, FSMEngine, GateMode, SensorFrame, Signal, State


def frame(t: float, *, present=True, pc_ratio=0.9, sig=None, **flags) -> SensorFrame:
    signals = {k: Signal(**v) for k, v in (sig or {}).items()}
    return SensorFrame(now=t, present=present, pc_ratio=pc_ratio, signals=signals, **flags)


def drive_to_focus(engine: FSMEngine, *, pc_ratio=0.9, t0=0.0) -> float:
    """IDLE→FOCUS_* 까지 진행하고 마지막 시각을 반환."""
    engine.tick(frame(t0, touch=True, pc_ratio=pc_ratio))          # IDLE→START
    engine.tick(frame(t0 + 301, pc_ratio=pc_ratio))                # baseline→CONTEXT_DETECT
    engine.tick(frame(t0 + 302, pc_ratio=pc_ratio))                # →FOCUS_*
    assert engine.state in {State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC}
    return t0 + 302


# ── 시작 경로 ──────────────────────────────────────────────
def test_idle_requires_touch():
    e = FSMEngine()
    assert e.tick(frame(0)).state is State.IDLE
    assert e.tick(frame(1, touch=True)).state is State.START


def test_baseline_holds_until_timer():
    e = FSMEngine()
    e.tick(frame(0, touch=True))
    assert e.tick(frame(100)).state is State.START          # 아직 baseline 중
    assert e.tick(frame(301)).state is State.CONTEXT_DETECT  # 300s 경과


@pytest.mark.parametrize("ratio,expected", [
    (0.9, State.FOCUS_PC),
    (0.5, State.FOCUS_MIXED),
    (0.1, State.FOCUS_NPC),
])
def test_context_routes_to_focus(ratio, expected):
    e = FSMEngine()
    drive_to_focus(e, pc_ratio=ratio)
    assert e.state is expected


# ── 판단 경로 ──────────────────────────────────────────────
def test_focus_to_fatigue_suspect_needs_hold():
    e = FSMEngine()
    t = drive_to_focus(e)
    hot = {"posture": {"delta": 0.9}, "elapsed": {"delta": 0.9}, "keystroke": {"delta": 0.8}}
    assert e.tick(frame(t + 30, sig=hot)).state is State.FOCUS_PC     # 진입 hold 시작
    assert e.tick(frame(t + 100, sig=hot)).state is State.FOCUS_PC    # 아직 3분 미만
    assert e.tick(frame(t + 220, sig=hot)).state is State.FATIGUE_SUSPECT


def test_focus_break_and_reengage():
    e = FSMEngine()
    t = drive_to_focus(e)
    # 집중 저하: C_focus 높고 C_fatigue 낮음
    lo_fat = {"posture": {"phi": 0.9, "delta": 0.1}, "elapsed": {"phi": 0.9, "delta": 0.1}}
    r = e.tick(frame(t + 30, sig=lo_fat))
    assert r.state is State.FOCUS_BREAK
    # 재유도 성공: C_focus < 0.25
    calm = {"posture": {"phi": 0.1, "delta": 0.1}, "elapsed": {"phi": 0.1, "delta": 0.1}}
    assert e.tick(frame(t + 60, sig=calm)).state is State.FOCUS_PC


def test_suspect_natural_recovery():
    e = FSMEngine()
    t = drive_to_focus(e)
    hot = {"posture": {"delta": 0.9}, "elapsed": {"delta": 0.9}, "keystroke": {"delta": 0.8}}
    for dt in (30, 100, 220):
        e.tick(frame(t + dt, sig=hot))
    assert e.state is State.FATIGUE_SUSPECT
    calm = {"posture": {"delta": 0.05}, "elapsed": {"delta": 0.05}, "keystroke": {"delta": 0.05}}
    assert e.tick(frame(t + 260, sig=calm)).state is State.FOCUS_PC


# ── 후속 조치 경로 ─────────────────────────────────────────
def confirm_fatigue(e: FSMEngine, t: float, sig: dict, pc_ratio=0.9) -> float:
    """FOCUS→...→FATIGUE 까지 몰아간다. 반환: 다음 시각."""
    e.tick(frame(t + 30, pc_ratio=pc_ratio, sig=sig))    # elevated 시작
    e.tick(frame(t + 220, pc_ratio=pc_ratio, sig=sig))   # →FATIGUE_SUSPECT
    e.tick(frame(t + 250, pc_ratio=pc_ratio, sig=sig))   # confirm hold 시작 (C≥0.70)
    e.tick(frame(t + 440, pc_ratio=pc_ratio, sig=sig))   # →FATIGUE
    assert e.state is State.FATIGUE
    return t + 470


def test_fatigue_routes_by_dominant_cause():
    e = FSMEngine()
    t = drive_to_focus(e, pc_ratio=0.1)          # 비PC: 자세 가중치 0.40 지배 가능
    sig = {"posture": {"delta": 0.9}, "environment": {"delta": 0.8},
           "elapsed": {"delta": 0.8}}
    t = confirm_fatigue(e, t, sig, pc_ratio=0.1)
    assert e.tick(frame(t, pc_ratio=0.1, sig=sig)).state is State.CAUSE_ANALYSIS
    r = e.tick(frame(t + 30, pc_ratio=0.1, sig=sig))
    assert r.state is State.ACTION_POSTURE
    assert r.cause == "posture"


def test_intervention_success_recovers():
    e = FSMEngine()
    t = drive_to_focus(e)
    sig = {"posture": {"delta": 0.9}, "elapsed": {"delta": 0.9}, "keystroke": {"delta": 0.9}}
    t = confirm_fatigue(e, t, sig)
    e.tick(frame(t, sig=sig))                              # →CAUSE_ANALYSIS
    e.tick(frame(t + 30, sig=sig))                         # →ACTION_*
    e.tick(frame(t + 60, sig=sig, action_done=True))      # →MONITOR
    assert e.state is State.MONITOR
    calm = {"posture": {"delta": 0.1}, "elapsed": {"delta": 0.1}, "keystroke": {"delta": 0.1}}
    assert e.tick(frame(t + 90, sig=calm)).state is State.RECOVERY   # 개선
    assert e.tick(frame(t + 120, sig=calm)).state is State.FOCUS_PC  # 회복 승인


def test_intervention_failure_escalates_and_forces_rest():
    e = FSMEngine()
    t = drive_to_focus(e)
    sig = {"posture": {"delta": 0.9}, "elapsed": {"delta": 0.9}, "keystroke": {"delta": 0.9}}
    t = confirm_fatigue(e, t, sig)
    # 3개 원인 모두 소진할 때까지 실패 반복 (원인당 CAUSE→ACTION→MONITOR→ESCALATE ≈ 4 tick)
    seen_escalate = False
    for i in range(20):
        r = e.tick(frame(t + i * 30, sig=sig, action_done=True))
        if r.state is State.ESCALATE:
            seen_escalate = True
        if r.state is State.REST:
            break
    assert seen_escalate
    assert e.state is State.REST
    # 휴식 최소 시간 후 회복 판정
    assert e.tick(frame(t + 1000, sig=sig)).state is State.RECOVERY


# ── 전역/게이트/점수 ───────────────────────────────────────
def test_absent_forces_idle():
    e = FSMEngine()
    t = drive_to_focus(e)
    e.tick(frame(t + 30, present=False))                  # 이탈 시작
    assert e.tick(frame(t + 700, present=False)).state is State.IDLE


def test_end_touch_terminates():
    e = FSMEngine()
    drive_to_focus(e)
    assert e.tick(frame(1000, end_touch=True)).state is State.END


def test_scoring_renormalizes_when_keystroke_absent():
    e = FSMEngine()
    drive_to_focus(e, pc_ratio=0.9)
    # keystroke 미가용 → 분모에서 제외, 나머지로 정규화
    r = e.tick(frame(400, sig={
        "posture": {"delta": 1.0},
        "elapsed": {"delta": 1.0},
        "environment": {"delta": 1.0},
        "keystroke": {"delta": 1.0, "available": False},
    }))
    assert r.scores.c_fatigue == pytest.approx(1.0)       # 가용 신호가 모두 1.0


def test_respiration_disabled_by_default():
    e = FSMEngine()
    # 기본 config: respiration_enabled=false → 호흡 가중치 0
    w = e.tick(frame(0)).scores.fatigue_weights
    assert w["respiration"] == 0.0


def test_gate_modes():
    e = FSMEngine()
    assert e._gate(0.80) is GateMode.AUTO
    assert e._gate(0.50) is GateMode.SUGGEST
    assert e._gate(0.20) is GateMode.NONE


def test_dominant_cause_routing_direct():
    """개입 라우팅 함수 단위 검증 (weight×delta argmax)."""
    from deskmate_hub.inference.cause import dominant_cause
    from deskmate_hub.inference.config import load_config

    cfg = load_config()
    # 비PC 가중치: posture 0.40, environment 0.20, elapsed 0.15
    w = {"posture": 0.40, "environment": 0.20, "elapsed": 0.15, "keystroke": 0.0, "respiration": 0.0}

    env_heavy = frame(0, sig={"environment": {"delta": 1.0}, "posture": {"delta": 0.2},
                              "elapsed": {"delta": 0.2}})
    assert dominant_cause(env_heavy, w, cfg) == "environment"   # 0.20 > 0.08 > 0.03

    cog_heavy = frame(0, sig={"elapsed": {"delta": 1.0}, "posture": {"delta": 0.1},
                              "environment": {"delta": 0.1}})
    assert dominant_cause(cog_heavy, w, cfg) == "cognitive"     # elapsed 0.15 최대

    assert dominant_cause(frame(0), w, cfg) == "cognitive"      # 근거 부족 시 안전 기본
