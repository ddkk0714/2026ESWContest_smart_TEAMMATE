"""10 s 주기에서 임계 근방 진동 입력이 상태를 튀게 하지 않는지(채터링) 확인한다 — roadmap 4-B §5.

score_period 를 30→10 s 로 줄이면 임계 근방 표본이 3배 늘어난다. 유지 시간 타이머(fatigue_elevated_hold_sec 등)와
히스테리시스(진입 0.40 / 회복 0.30, 확정 0.70 / 실패 0.70)가 초 단위라 주기와 무관하게 동작해야 한다.
"""
from __future__ import annotations

from deskmate_hub.inference import FSMEngine, SensorFrame, Signal, State, load_config

PERIOD = 10.0


def frame(now, delta, phi=0.05, **flags):
    # PC 컨텍스트: 모든 신호를 같은 delta 로 주면 C_fatigue == delta (가중치 합 1 로 재정규화)
    sig = {k: Signal(phi=phi, delta=delta) for k in ("keystroke", "posture", "environment", "elapsed")}
    return SensorFrame(now=now, present=True, pc_ratio=0.9, signals=sig, **flags)


def run_to_focus(engine, cfg):
    t = 0.0
    engine.tick(frame(t, 0.1, touch=True))
    baseline = float(cfg["timers"]["baseline_sec"])
    while t < baseline + 3 * PERIOD:
        t += PERIOD
        engine.tick(frame(t, 0.1))
    assert engine.state is State.FOCUS_PC
    return t


def test_oscillation_around_suspect_threshold_does_not_flip_state():
    cfg = load_config()
    engine = FSMEngine(cfg)
    t = run_to_focus(engine, cfg)
    th = float(cfg["thresholds"]["fatigue_suspect_low"])          # 0.40
    visited = set()
    for i in range(int(1800 / PERIOD)):                            # 30분 동안 0.38 / 0.42 교대
        t += PERIOD
        r = engine.tick(frame(t, th + (0.02 if i % 2 else -0.02)))
        visited.add(r.state)
    # 유지 시간(180 s) 동안 연속으로 임계 위에 있지 않으므로 FATIGUE_SUSPECT 로 가지 않는다
    assert visited == {State.FOCUS_PC}, visited


def test_sustained_elevation_enters_suspect_and_hysteresis_holds_it():
    cfg = load_config()
    engine = FSMEngine(cfg)
    t = run_to_focus(engine, cfg)
    hold = float(cfg["timers"]["fatigue_elevated_hold_sec"])
    # 0.50 지속 → hold 뒤 FATIGUE_SUSPECT
    ticks_to_enter = None
    for i in range(int((hold + 5 * PERIOD) / PERIOD)):
        t += PERIOD
        if engine.tick(frame(t, 0.50)).state is State.FATIGUE_SUSPECT:
            ticks_to_enter = i + 1
            break
    assert ticks_to_enter is not None
    assert abs(ticks_to_enter * PERIOD - hold) <= 2 * PERIOD       # 대략 hold 시간에 진입
    # SUSPECT 안에서 0.35 / 0.45 진동: 회복(<0.30)도 확정(≥0.70 지속)도 아니므로 머문다
    visited = set()
    for i in range(int(900 / PERIOD)):
        t += PERIOD
        visited.add(engine.tick(frame(t, 0.35 if i % 2 else 0.45)).state)
    assert visited == {State.FATIGUE_SUSPECT}, visited
    # 0.29 로 내려가면 자연 회복
    t += PERIOD
    assert engine.tick(frame(t, 0.29)).state is State.FOCUS_PC


def test_confirm_requires_sustained_high_fatigue():
    cfg = load_config()
    engine = FSMEngine(cfg)
    t = run_to_focus(engine, cfg)
    for _ in range(int(float(cfg["timers"]["fatigue_elevated_hold_sec"]) / PERIOD) + 2):
        t += PERIOD
        engine.tick(frame(t, 0.55))
    assert engine.state is State.FATIGUE_SUSPECT
    # 0.72 / 0.68 교대 → 확정 유지시간(180 s) 을 연속으로 못 채워 FATIGUE 로 가지 않는다
    visited = set()
    for i in range(int(600 / PERIOD)):
        t += PERIOD
        visited.add(engine.tick(frame(t, 0.68 if i % 2 else 0.72)).state)
    assert State.FATIGUE not in visited, visited
    # 0.75 지속 → FATIGUE
    for _ in range(int(float(cfg["timers"]["fatigue_confirm_hold_sec"]) / PERIOD) + 2):
        t += PERIOD
        r = engine.tick(frame(t, 0.75))
    assert r.state in (State.FATIGUE, State.CAUSE_ANALYSIS, State.ACTION_ENV, State.ACTION_POSTURE, State.ACTION_BREAK)


def test_tick_latency_budget():
    """판정 사이클 ≤ 500 ms (PC 기준 참고치; Pi 4 실측은 별도)."""
    import time

    cfg = load_config()
    engine = FSMEngine(cfg)
    frames = [frame(i * PERIOD, 0.3 + 0.001 * (i % 100), touch=(i == 0)) for i in range(1000)]
    start = time.perf_counter()
    for f in frames:
        engine.tick(f)
    per_tick_ms = (time.perf_counter() - start) / len(frames) * 1000
    assert per_tick_ms < 500, per_tick_ms
