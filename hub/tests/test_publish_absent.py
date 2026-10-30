"""자리 비움(present false)이면 발행 점수는 0.0 + scores_valid false (2026-10-26 보드 실기).

보드 19:50(IDLE, mmWave 거리 143 cm > desk_max 130): 부재면 자세는 mmWave 게이트로 빠지고, 키 입력은 typing false, IDLE 은 경과도
없어 가용 신호가 환경 하나뿐 → Σw·δ/Σw 재정규화가 lux_dim 1.0(lux 12) 을 그대로 c_fatigue 1.0 으로, 온도 편차가 c_focus 0.45 로 발행돼
앱이 "피로 100 %" 를 보였다. FSM·리포트는 점수를 안 쓰므로 판정엔 영향 없고 표시만의 문제라, **엔진은 그대로 두고 발행 단계**(state/phase
envelope·카드 payload)에서만 present false 면 c_fatigue·c_focus 0.0 과 `present: false`·`scores_valid: false` 를 싣는다 — 앱·devview 는
숫자 대신 "자리 비움" 을 보인다. 엔진에서 0 으로 만들면 부재 중 SUSPECT 자연 회복·MONITOR 회복 승인이 잘못 성립한다.
"""
from __future__ import annotations

from deskmate_hub.inference import FSMEngine, State, load_config
from deskmate_hub.inference.types import SensorFrame, Signal
from deskmate_hub.presentation import state_envelope

from test_demo_face_down import _hub
from test_ingest_mapping import env, mm
from test_tof_service import svc


def _absent_env_only_result():
    """부재·환경만 가용(lux 어두움 δ 1.0, 온도 φ 0.45): 엔진 점수는 재정규화로 1.0 / 0.45."""
    engine = FSMEngine(load_config())
    frame = SensorFrame(now=0.0, present=False, pc_ratio=0.1,
                        signals={"environment": Signal(phi=0.45, delta=1.0, available=True)})
    return engine.tick(frame)


def test_state_envelope_zeroes_scores_when_absent_but_engine_scores_untouched():
    result = _absent_env_only_result()
    assert result.state is State.IDLE and result.scores.c_fatigue == 1.0 and result.scores.c_focus == 0.45   # 엔진은 그대로
    absent = state_envelope(result, boot_id="t", seq=1, ts=0.0, present=False)["data"]
    assert absent["c_fatigue"] == 0.0 and absent["c_focus"] == 0.0 and absent["confidence"] == 0.0
    assert absent["present"] is False and absent["scores_valid"] is False
    here = state_envelope(result, boot_id="t", seq=1, ts=0.0, present=True)["data"]
    assert here["c_fatigue"] == 1.0 and here["c_focus"] == 0.45 and here["present"] is True and here["scores_valid"] is True
    assert state_envelope(result, boot_id="t", seq=1, ts=0.0)["data"]["scores_valid"] is True          # 기본은 그대로


def test_live_hub_publishes_zero_while_away_from_desk(tmp_path):
    """보드 19:50 재현: 방 안(mmWave 143 cm)·서비스 recline 0.46·타이핑 없음·lux 12·27.8 °C → IDLE, 엔진 1.0, 발행 0.0."""
    hub, cache, states = _hub(tmp_path, "demo-b")
    for t in (0.0, 10.0):
        cache.put("mmwave", mm(t, present=False, level=3, dist=143))
        cache.put("env", env(t, co2=802, lux=12, temp=27.8))
        cache.put("tof_service", svc(t, posture="recline", conf=0.46))
        hub.tick_once(t)
    phases = [s["data"] for s in states if isinstance(s, dict) and "fsm_state" in (s.get("data") or {})]
    assert phases, states[-1]
    last = phases[-1]
    assert last["fsm_state"] == "IDLE" and last["sensor_summary"]["present"] is False
    assert hub._last_result.scores.c_fatigue == 1.0                                     # 엔진 점수(환경만 재정규화)는 그대로
    assert last["c_fatigue"] == 0.0 and last["c_focus"] == 0.0 and last["confidence"] == 0.0
    assert last["present"] is False and last["scores_valid"] is False
    # 돌아와 앉으면(책상 55 cm) 다시 점수가 실린다
    cache.put("mmwave", mm(20.0, present=True, level=3, dist=55))
    cache.put("env", env(20.0, co2=802, lux=12, temp=27.8))
    cache.put("tof_service", svc(20.0, posture="upright", conf=0.9))
    hub.tick_once(20.0)
    back = [s["data"] for s in states if isinstance(s, dict) and "fsm_state" in (s.get("data") or {})][-1]
    assert back["present"] is True and back["scores_valid"] is True


def test_card_payload_carries_present_and_scores_valid(tmp_path):
    """카드 payload(제안·CO₂)도 같은 필드를 싣는다 — 자리 비움이면 0.0."""
    hub, _, _ = _hub(tmp_path, "demo-b")
    result = _absent_env_only_result()
    hub._last_present = False
    assert hub._pub_scores(result) == (0.0, False)
    hub._last_present = True
    assert hub._pub_scores(result) == (1.0, True)
