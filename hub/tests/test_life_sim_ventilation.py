"""CO₂ 환기 타이밍 규칙(tools/life_sim/ventilation.py) — FSM 밖에서 깨어 있을 때 창문 환기 카드를 띄우는 시점을 고정한다."""
from __future__ import annotations

from tools.life_sim.ventilation import VentRule

CFG = {"enabled": True, "avg_s": 60, "on_ppm": 1000, "hold_s": 300, "high_ppm": 1400, "awake_s": 60,
       "focus_defer_s": 600, "retry_s": 1200, "reject_retry_s": 2400}
WINDOW = {"choice": "window", "minutes": 15.0, "why": ["windy"]}
RAIN = {"choice": "vent_fan", "why": ["rain", "co2_stays_fan_only_comfort"]}


def _run(rule, t0, t1, co2, *, posture="upright", state="FOCUS_BREAK", present=True, vent=WINDOW, window_open=False):
    """t0..t1 초를 1 초씩 돌리고 카드가 처음 나온 (시각, 요청) 을 돌려준다."""
    for t in range(t0, t1):
        req = rule.observe(float(t), co2, present=present, posture=posture, state=state, window_open=window_open, vent=vent)
        if req is not None:
            return t, req
    return None, None


def test_disabled_by_default():
    rule = VentRule({}, t0=0.0)
    assert _run(rule, 0, 2000, 1500.0) == (None, None)


def test_card_after_hold_when_awake_and_window_allowed():
    rule = VentRule(CFG, t0=0.0)
    t, req = _run(rule, 0, 1000, 1100.0)
    assert t == 300                                  # 평균 ≥ 1000 이 hold_s 이어진 뒤
    assert req["minutes"] == 15.0 and "co2_rule" in req["why"] and "windy" in req["why"]
    assert _run(rule, 301, 1400, 1100.0) == (None, None)       # 무응답이면 retry_s 동안 다시 묻지 않는다
    assert _run(rule, 1400, 1700, 1100.0)[0] == 1500


def test_no_card_while_drowsy_or_away_or_window_open():
    for kw in ({"posture": "drowsy"}, {"posture": "face_down"}, {"present": False}, {"window_open": True}):
        rule = VentRule(CFG, t0=0.0)
        assert _run(rule, 0, 2000, 1300.0, **kw) == (None, None), kw


def test_weather_exclusion_logs_once_and_shows_no_card():
    rule = VentRule(CFG, t0=0.0)
    assert _run(rule, 0, 2000, 1300.0, vent=RAIN) == (None, None)
    ex = [e for e in rule.log if e["kind"] == "excluded"]
    assert len(ex) == 1 and "rain" in ex[0]["why"]


def test_focus_defers_unless_urgent():
    rule = VentRule(CFG, t0=0.0)
    assert _run(rule, 0, 2000, 1200.0, state="FOCUS_PC")[0] == 900           # 몰입이면 최대 600 s 더 기다린다
    rule = VentRule(CFG, t0=0.0)
    assert _run(rule, 0, 2000, 1500.0, state="FOCUS_PC")[0] == 300           # ≥ high_ppm 이면 바로


def test_reject_waits_longer():
    rule = VentRule(CFG, t0=0.0)
    t, _ = _run(rule, 0, 1000, 1100.0)
    rule.on_feedback("reject", float(t + 30))
    assert _run(rule, t + 1, t + 2400, 1100.0) == (None, None)
    assert _run(rule, t + 2400, t + 3000, 1100.0)[0] is not None
