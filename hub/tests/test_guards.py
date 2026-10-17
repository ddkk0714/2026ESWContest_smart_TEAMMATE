"""조합 조건(guard) 단위 테스트 — docs/guard-spec.md 사양 검증.

guard 는 가중합이 표현하지 못하는 다신호 AND 규칙을 담당한다. 핵심 검증은
"셋이 동시에 성립" 과 "하나만 크게 성립" 이 실제로 갈리는지다.
"""
from __future__ import annotations

import pytest

from deskmate_hub.inference import FSMEngine, SensorFrame, Signal, State
from deskmate_hub.inference.guards import GuardEvaluator


def frame(t: float, **sig) -> SensorFrame:
    """sig: 신호이름=dict(reasons=[...], delta=..., available=...)"""
    signals = {k: Signal(**v) for k, v in sig.items()}
    return SensorFrame(now=t, present=True, pc_ratio=0.9, signals=signals)


def ev(**guards) -> GuardEvaluator:
    return GuardEvaluator({"guards": guards})


DROWSY = {
    "all": [
        {"signal": "respiration", "reason": "heart_slow"},
        {"signal": "posture", "reason": "slouch"},
        {"signal": "posture", "reason": "head_down"},
    ]
}


# ── 조합 평가 ────────────────────────────────────────────────
def test_all_requires_every_condition():
    """셋이 동시에 성립해야 졸음이다 — 이게 가중합으로 안 되던 부분."""
    e = ev(drowsy=DROWSY)
    both = frame(
        0,
        respiration={"reasons": ["heart_slow"]},
        posture={"reasons": ["slouch", "head_down"]},
    )
    assert e.evaluate(both, 0)[0] == ["drowsy"]


def test_all_fails_when_one_signal_missing():
    """자세만 굽었으면 졸음이 아니다. delta 가 아무리 커도 마찬가지."""
    e = ev(drowsy=DROWSY)
    posture_only = frame(0, posture={"reasons": ["slouch", "head_down"], "delta": 0.99})
    assert e.evaluate(posture_only, 0)[0] == []


def test_any_and_none():
    e = ev(
        either={"any": [
            {"signal": "posture", "reason": "slouch"},
            {"signal": "posture", "reason": "lean_back"},
        ]},
        quiet={"none": [{"signal": "keystroke", "reason": "typing_idle"}]},
    )
    active, _ = e.evaluate(frame(0, posture={"reasons": ["lean_back"]}), 0)
    assert active == ["either", "quiet"]


def test_empty_lists_follow_spec():
    """all: [] 은 참, any: [] 는 거짓 (guard-spec §3.2)."""
    e = ev(always={"all": []}, never={"any": []})
    assert e.evaluate(frame(0), 0)[0] == ["always"]


def test_nested_combinators():
    e = ev(nested={"all": [
        {"signal": "posture", "reason": "slouch"},
        {"any": [
            {"signal": "keystroke", "reason": "typing_idle"},
            {"signal": "keystroke", "reason": "rhythm_slow"},
        ]},
    ]})
    f = frame(0, posture={"reasons": ["slouch"]}, keystroke={"reasons": ["rhythm_slow"]})
    assert e.evaluate(f, 0)[0] == ["nested"]


# ── 가용성 (data-spec DAT-T01: 0 과 결측은 다르다) ──────────────
def test_unavailable_signal_never_matches_reason():
    e = ev(g=DROWSY)
    f = frame(
        0,
        respiration={"reasons": ["heart_slow"], "available": False},
        posture={"reasons": ["slouch", "head_down"]},
    )
    assert e.evaluate(f, 0)[0] == []


def test_numeric_compare_requires_available_signal():
    """미가용 신호의 delta=0.0 을 '낮다' 로 읽으면 안 된다."""
    e = ev(low={"all": [{"signal": "keystroke", "delta_lt": 0.5}]})
    assert e.evaluate(frame(0, keystroke={"delta": 0.0, "available": False}), 0)[0] == []
    assert e.evaluate(frame(1, keystroke={"delta": 0.0, "available": True}), 1)[0] == ["low"]


def test_missing_signal_is_unavailable():
    e = ev(g={"all": [{"signal": "posture", "reason": "slouch"}]})
    assert e.evaluate(frame(0), 0)[0] == []


# ── 지속 조건 ────────────────────────────────────────────────
def test_hold_sec_delays_activation():
    e = ev(g={"all": [{"signal": "posture", "reason": "slouch"}], "hold_sec": 60})
    slouch = lambda t: frame(t, posture={"reasons": ["slouch"]})  # noqa: E731
    assert e.evaluate(slouch(0), 0)[0] == []       # 방금 성립, 아직 대기
    assert e.evaluate(slouch(59), 59)[0] == []
    assert e.evaluate(slouch(60), 60)[0] == ["g"]  # 60초 경과


def test_broken_condition_resets_hold_timer():
    """깜빡이면 처음부터 다시 센다 — 채터링 방지."""
    e = ev(g={"all": [{"signal": "posture", "reason": "slouch"}], "hold_sec": 60})
    e.evaluate(frame(0, posture={"reasons": ["slouch"]}), 0)
    e.evaluate(frame(30, posture={"reasons": []}), 30)          # 끊김
    assert e.evaluate(frame(70, posture={"reasons": ["slouch"]}), 70)[0] == []
    assert e.evaluate(frame(130, posture={"reasons": ["slouch"]}), 130)[0] == ["g"]


# ── actions · route ─────────────────────────────────────────
def test_actions_emit_once_on_activation():
    """유지되는 동안 매 tick 방출하면 개입이 폭주한다."""
    e = ev(g={"all": [{"signal": "posture", "reason": "slouch"}], "actions": ["wake_alert"]})
    slouch = lambda t: frame(t, posture={"reasons": ["slouch"]})  # noqa: E731
    _, newly = e.evaluate(slouch(0), 0)
    assert e.actions_for(newly) == ["wake_alert"]
    _, newly = e.evaluate(slouch(30), 30)
    assert e.actions_for(newly) == []               # 유지 중 — 재방출 없음


def test_actions_reemit_after_break():
    e = ev(g={"all": [{"signal": "posture", "reason": "slouch"}], "actions": ["wake_alert"]})
    e.evaluate(frame(0, posture={"reasons": ["slouch"]}), 0)
    e.evaluate(frame(30, posture={"reasons": []}), 30)
    _, newly = e.evaluate(frame(60, posture={"reasons": ["slouch"]}), 60)
    assert e.actions_for(newly) == ["wake_alert"]


def test_route_follows_declaration_order():
    e = ev(
        first={"all": [{"signal": "posture", "reason": "slouch"}], "route_to": "posture"},
        second={"all": [{"signal": "posture", "reason": "slouch"}], "route_to": "environment"},
    )
    active, _ = e.evaluate(frame(0, posture={"reasons": ["slouch"]}), 0)
    assert e.route_for(active) == "posture"


def test_disabled_guard_never_activates():
    e = ev(g={"enabled": False, "all": [{"signal": "posture", "reason": "slouch"}]})
    assert e.evaluate(frame(0, posture={"reasons": ["slouch"]}), 0)[0] == []


# ── 설정 검증 (오타는 조용히 거짓이 되지 않고 즉시 실패한다) ──────
@pytest.mark.parametrize("bad", [
    {"all": [{"signal": "posture", "resaon": "slouch"}]},        # 키 오타
    {"all": [{"signal": "postures", "reason": "slouch"}]},       # 없는 신호
    {"all": [{"reason": "slouch"}]},                             # signal 누락
    {"all": [{"signal": "posture"}]},                            # 검사 조건 없음
    {"hold_sec": 60},                                            # 결합자 없음
    {"all": [{"signal": "posture", "reason": "slouch"}], "actoins": ["x"]},
    {"all": {"signal": "posture", "reason": "slouch"}},          # 목록이 아님
])
def test_invalid_config_raises(bad):
    with pytest.raises(ValueError):
        ev(g=bad)


# ── 엔진 통합 ────────────────────────────────────────────────
def test_engine_without_guards_is_unchanged():
    """guards 가 비면 guard 도입 전과 동일하게 동작해야 한다."""
    cfg = FSMEngine().cfg.copy()
    cfg["guards"] = {}
    e = FSMEngine(cfg)
    assert e.tick(SensorFrame(now=0, present=True, pc_ratio=0.9, touch=True)).state is State.START
    assert e.active_guards == []


def test_engine_exposes_active_guards():
    cfg = FSMEngine().cfg.copy()
    cfg["guards"] = {"hot": {"all": [{"signal": "environment", "reason": "temp_high"}]}}
    e = FSMEngine(cfg)
    result = e.tick(frame(0, environment={"reasons": ["temp_high"]}))
    assert result.guards == ["hot"]


# ── 라우팅 통합 — guard 가 실제로 개입을 바꾸는가 ─────────────────
def gframe(t: float, sig: dict, pc_ratio: float = 0.1, **flags) -> SensorFrame:
    signals = {k: Signal(**v) for k, v in sig.items()}
    return SensorFrame(now=t, present=True, pc_ratio=pc_ratio, signals=signals, **flags)


def drive_to_fatigue(e: FSMEngine, sig: dict, pc_ratio: float = 0.1) -> float:
    """IDLE→FATIGUE. 타이머는 config 기본값(baseline 300s, hold 180s)."""
    e.tick(gframe(0, {}, pc_ratio, touch=True))
    e.tick(gframe(301, {}, pc_ratio))
    e.tick(gframe(302, {}, pc_ratio))
    for dt in (30, 220, 250, 440):
        e.tick(gframe(302 + dt, sig, pc_ratio))
    assert e.state is State.FATIGUE
    return 772.0


def _cfg_with(guards: dict) -> dict:
    cfg = dict(FSMEngine().cfg)
    cfg["guards"] = guards
    return cfg


# 비PC 컨텍스트에서 dominant 는 posture 다 (test_fsm_transitions 의 기준 시나리오).
_SIG = {
    "posture": {"delta": 0.9, "reasons": ["slouch", "head_down"]},
    "environment": {"delta": 0.8},
    "elapsed": {"delta": 0.8},
    "respiration": {"delta": 0.5, "reasons": ["heart_slow"]},
}
_DROWSY_GUARD = {"drowsy": dict(DROWSY, route_to="environment", actions=["wake_alert"])}


def test_guard_route_overrides_dominant_cause():
    """졸음이면 자세 교정이 아니라 환기로 간다 — 점수만으로는 못 가르던 분기."""
    e = FSMEngine(_cfg_with(_DROWSY_GUARD))
    t = drive_to_fatigue(e, _SIG)
    assert e.tick(gframe(t, _SIG)).state is State.CAUSE_ANALYSIS
    r = e.tick(gframe(t + 30, _SIG))
    assert r.state is State.ACTION_ENV          # guard 없으면 ACTION_POSTURE 였다
    assert r.cause == "environment"
    assert "guard_route:environment" in r.actions


def test_same_signals_route_to_posture_without_guard():
    """대조군 — guard 만 빼면 기존 dominant 라우팅 그대로다."""
    e = FSMEngine(_cfg_with({}))
    t = drive_to_fatigue(e, _SIG)
    e.tick(gframe(t, _SIG))
    r = e.tick(gframe(t + 30, _SIG))
    assert r.state is State.ACTION_POSTURE
    assert r.cause == "posture"


def test_guard_route_not_reused_after_failed_intervention():
    """개입 실패로 되돌아오면 guard 가 같은 원인을 다시 강제하면 안 된다."""
    e = FSMEngine(_cfg_with(_DROWSY_GUARD))
    t = drive_to_fatigue(e, _SIG)
    e.tick(gframe(t, _SIG))                              # →CAUSE_ANALYSIS
    e.tick(gframe(t + 30, _SIG))                         # →ACTION_ENV
    e.tick(gframe(t + 60, _SIG, action_done=True))       # →MONITOR
    assert e.state is State.MONITOR
    e.tick(gframe(t + 90, _SIG))                         # 개선 없음 →ESCALATE
    assert e.state is State.ESCALATE
    e.tick(gframe(t + 120, _SIG))                        # →CAUSE_ANALYSIS 재시도
    assert e.state is State.CAUSE_ANALYSIS
    r = e.tick(gframe(t + 150, _SIG))
    assert r.cause != "environment"                      # 소진된 원인은 재강제 금지
    assert r.state is not State.ACTION_ENV
