"""엎드림·노딩 시나리오 검증 — ToF posture 신호가 FSM 을 어떻게 움직이는지 고정한다.

ToF(VL53L9CX 54×42)는 자세·재실·노딩 특징을 만들어 posture Signal(phi/delta)로 넘긴다
(docs/data-spec.md). engine 은 그 두 float 만 보므로, 여기 테스트는
"특징 추출이 이런 값을 주면 FSM 이 이렇게 판정한다"는 계약을 고정하는 역할이다.

전제(config/fsm.yaml): fatigue_confirm 0.70, fatigue_focus_low 0.40,
PC 피로 가중치 keystroke 0.35 · posture 0.25 · environment 0.10 · elapsed 0.15
(respiration 은 비활성이라 0). 타이머는 elevated/confirm 각 180s.
"""
from __future__ import annotations

import pytest

from deskmate_hub.inference import FSMEngine, SensorFrame, Signal, State

# 엎드려 자는 구간의 신호 모양: 자세 붕괴 + 타이핑 정지(미가용) + 누적 경과.
SLUMP = {
    "posture": {"delta": 0.95},
    "elapsed": {"delta": 0.80},
    "environment": {"delta": 0.20},
    "keystroke": {"delta": 0.0, "available": False},
}


def frame(t: float, *, present=True, pc_ratio=0.9, sig=None, **flags) -> SensorFrame:
    signals = {k: Signal(**v) for k, v in (sig or {}).items()}
    return SensorFrame(now=t, present=present, pc_ratio=pc_ratio, signals=signals, **flags)


def drive_to_focus(engine: FSMEngine, *, pc_ratio=0.9, t0=0.0) -> float:
    """IDLE→FOCUS_* 까지 진행하고 마지막 시각을 반환."""
    engine.tick(frame(t0, touch=True, pc_ratio=pc_ratio))
    engine.tick(frame(t0 + 301, pc_ratio=pc_ratio))
    engine.tick(frame(t0 + 302, pc_ratio=pc_ratio))
    assert engine.state in {State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC}
    return t0 + 302


def run_until_fatigue(engine: FSMEngine, t: float, sig: dict, *, pc_ratio=0.9) -> float:
    """FOCUS→FATIGUE 까지 몰아간다(전이 hold 180s 두 번). 반환: 다음 시각."""
    for dt in (30, 220, 250, 440):
        engine.tick(frame(t + dt, pc_ratio=pc_ratio, sig=sig))
    return t + 470


# ── 엎드림 정상 경로 ───────────────────────────────────────
def test_slump_with_typing_stop_routes_to_posture():
    """엎드림 + 타이핑 정지 → 피로 확정 → 자세 개입.

    타이핑이 멈추면 keystroke(0.35)가 분모에서 빠져 cognitive 그룹이 elapsed(0.15)만
    남는다. 그래서 posture(0.25)가 dominant 가 되고 ACTION_POSTURE 로 라우팅된다.
    """
    e = FSMEngine()
    t = drive_to_focus(e)
    t = run_until_fatigue(e, t, SLUMP)
    assert e.state is State.FATIGUE

    assert e.tick(frame(t, sig=SLUMP)).state is State.CAUSE_ANALYSIS
    r = e.tick(frame(t + 30, sig=SLUMP))
    assert r.state is State.ACTION_POSTURE
    assert r.cause == "posture"
    assert "posture_alert" in e.tick(frame(t + 60, sig=SLUMP)).actions


def test_slump_score_crosses_confirm_threshold():
    """엎드림 신호 조합의 C_fatigue 는 확정선(0.70)을 넘는다."""
    e = FSMEngine()
    drive_to_focus(e)
    r = e.tick(frame(400, sig=SLUMP))
    assert r.scores.c_fatigue == pytest.approx(0.755, abs=1e-3)


def test_slump_recovers_to_focus_after_correction():
    """자세 교정 후 피로가 내려가면 몰입으로 복귀한다."""
    e = FSMEngine()
    t = drive_to_focus(e)
    t = run_until_fatigue(e, t, SLUMP)
    e.tick(frame(t, sig=SLUMP))                              # →CAUSE_ANALYSIS
    e.tick(frame(t + 30, sig=SLUMP))                         # →ACTION_POSTURE
    e.tick(frame(t + 60, sig=SLUMP, action_done=True))       # →MONITOR
    assert e.state is State.MONITOR

    fixed = {"posture": {"delta": 0.10}, "elapsed": {"delta": 0.20},
             "environment": {"delta": 0.10}, "keystroke": {"delta": 0.10}}
    assert e.tick(frame(t + 90, sig=fixed)).state is State.RECOVERY
    assert e.tick(frame(t + 120, sig=fixed)).state is State.FOCUS_PC


# ── 자세 단독 판정 금지 (데이터·FSM 계약) ───────────────────
def test_posture_alone_cannot_confirm_fatigue():
    """자세만 나쁘고 다른 근거가 없으면 피로를 확정하지 않는다.

    데이터·FSM 계약: "자세는 재실/자세종류 판정에만 제한 사용,
    판정 주축은 키스트로크 + 작업시간 + 환경". PC 가중치에서 posture 는 0.25 뿐이라
    delta=1.0 이어도 C_fatigue 는 0.55 로 확정선(0.70)에 못 미친다.
    """
    e = FSMEngine()
    t = drive_to_focus(e)
    posture_only = {
        "posture": {"delta": 1.00},
        "elapsed": {"delta": 0.10},
        "environment": {"delta": 0.10},
        "keystroke": {"delta": 0.0, "available": False},
    }
    r = e.tick(frame(t + 30, sig=posture_only))
    assert r.scores.c_fatigue == pytest.approx(0.550, abs=1e-3)

    t = run_until_fatigue(e, t, posture_only)
    assert e.state is State.FATIGUE_SUSPECT      # 의심까지만, 확정 아님


def test_typing_contradicts_slump():
    """타이핑이 살아 있으면 자세가 나빠도 확정되지 않는다.

    keystroke 가 가용하면 분모에 0.35 가 돌아와 C_fatigue 가 희석된다.
    엎드린 채로 타이핑하는 상태는 모순 신호이므로 확정을 보류하는 쪽이 맞다.
    """
    e = FSMEngine()
    t = drive_to_focus(e)
    typing_while_slumped = dict(SLUMP, keystroke={"delta": 0.20}, elapsed={"delta": 0.50})
    r = e.tick(frame(t + 30, sig=typing_while_slumped))
    assert r.scores.c_fatigue == pytest.approx(0.474, abs=1e-3)

    t = run_until_fatigue(e, t, typing_while_slumped)
    assert e.state is State.FATIGUE_SUSPECT


@pytest.mark.xfail(
    reason="특징 추출이 elapsed/environment 를 available=False 로 보내면 "
           "재정규화로 posture 단독 C_fatigue=1.0 이 되어 리스크 3 완화가 무너진다. "
           "ingest/features 계약으로 막아야 한다.",
    strict=True,
)
def test_missing_signals_must_not_let_posture_alone_confirm():
    """자세 외 신호가 미가용이면 자세 단독으로 피로가 확정된다 — 계약 위험.

    scoring 은 available=False 인 항을 분모에서 빼므로, posture 만 들어오면
    분모가 0.25 하나가 되어 delta 0.95 가 그대로 C_fatigue 0.95 가 된다.
    test_posture_alone_cannot_confirm_fatigue 가 지키려는 성질이 입력 가용성에
    따라 뒤집히는 것이라, 특징 추출 쪽에서 elapsed/environment 를 항상 채워야 한다.
    """
    e = FSMEngine()
    t = drive_to_focus(e)
    only_posture = {"posture": {"delta": 0.95}}
    r = e.tick(frame(t + 30, sig=only_posture))
    assert r.scores.c_fatigue < 0.70


# ── 노딩(꾸벅임)과 30초 tick ───────────────────────────────
def test_single_nod_spike_does_not_trigger():
    """단발 꾸벅임은 전이를 일으키지 않는다.

    노딩은 1~2초 사건인데 SensorFrame 은 30초 주기다. engine 은 개별 꾸벅임을
    볼 수 없으므로, 특징 추출이 30초 창에서 집계해 delta 를 지속적으로 올려줘야
    FATIGUE_SUSPECT 까지 간다(진입 hold 180s).
    """
    e = FSMEngine()
    t = drive_to_focus(e)
    calm = {"posture": {"delta": 0.10}, "elapsed": {"delta": 0.20},
            "environment": {"delta": 0.10}, "keystroke": {"delta": 0.10}}

    e.tick(frame(t + 30, sig=SLUMP))                 # 스파이크 1회 → hold 시작
    assert e.state is State.FOCUS_PC
    e.tick(frame(t + 60, sig=calm))                  # 정상 복귀 → hold 리셋
    assert e.state is State.FOCUS_PC
    e.tick(frame(t + 230, sig=SLUMP))                # 200s 지났지만 hold 는 새로 시작
    assert e.state is State.FOCUS_PC


def test_sustained_nodding_reaches_suspect():
    """30초 창 집계로 delta 가 계속 높으면 3분 뒤 의심으로 올라간다."""
    e = FSMEngine()
    t = drive_to_focus(e)
    e.tick(frame(t + 30, sig=SLUMP))
    e.tick(frame(t + 100, sig=SLUMP))
    assert e.state is State.FOCUS_PC                 # 아직 180s 미만
    assert e.tick(frame(t + 220, sig=SLUMP)).state is State.FATIGUE_SUSPECT


# ── 엎드림 vs 자리비움 ─────────────────────────────────────
def test_slump_must_keep_present_true():
    """엎드린 사람을 재실 False 로 보내면 피로가 아니라 자리비움으로 오판된다.

    ToF 재실 판정을 "상단 zone 에 머리 있음" 으로 구현하면 엎드린 순간 present 가
    꺼져 10분 뒤 IDLE 로 떨어진다. 즉 재실은 머리 위치가 아니라 zone 점유로
    판정해야 한다는 요구사항이 FSM 쪽에서 생긴다.
    """
    e = FSMEngine()
    t = drive_to_focus(e)

    # 잘못된 구현: 엎드리자마자 present=False
    e.tick(frame(t + 30, present=False, sig=SLUMP))
    assert e.tick(frame(t + 700, present=False, sig=SLUMP)).state is State.IDLE

    # 올바른 구현: 엎드려도 재실 유지 → 피로 경로로 간다
    e2 = FSMEngine()
    t2 = drive_to_focus(e2)
    run_until_fatigue(e2, t2, SLUMP)
    assert e2.state is State.FATIGUE
