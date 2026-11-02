"""시연 프로파일에서 엎드림(face_down)만으로 무드등이 켜진다 (2026-10-25, PC 폐루프 시뮬 결함).

face_down δ 1.0 만(끄덕임·mmWave 졸음 플래그·키 입력 없음, 환경 중립)으로 FATIGUE 를 확정하고 ACTION_POSTURE → desk_lamp
adjust_brightness 가 FOCUS 진입 뒤 120 s 안에 나가야 한다. 기대기(recline)·턱 괴기(chin_rest)만으로는 확정하지 않는다.
보드 기본(프로파일 없음)은 바뀌지 않아 face_down 만으로는 확정하지 않는다(기본 가중치 0.53).
"""
from __future__ import annotations

import io

import pytest

from deskmate_hub.inference import GateMode, State, load_config
from deskmate_hub.ingest import SensorCache
from deskmate_hub.inference.scoring import compute_scores
from deskmate_hub.inference.context import Context
from deskmate_hub.inference.types import SensorFrame, Signal
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import load_personalize_config
from deskmate_hub.profile import apply_profile

from test_ingest_mapping import env, mm
from test_posture_actions import CFG as CONTROL_CFG
from test_tof_service import svc

PROFILES = ("demo-a", "demo-b", "demo-b-study")


def _hub(tmp_path, profile: str):
    cache, states = SensorCache(), []
    # 정책은 끈다 — 이 파일은 FSM 가중치·램프 기본 명령 경로를 본다. 정책이 켜지면 밴딧이 고른 arm 이 실행된다(test_arm_delivery.py)
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub = LiveHub(cache, control_cfg=CONTROL_CFG, publish=states.append, out=io.StringIO(), profile=profile,
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"))
    return hub, cache, states


def _run(hub, cache, posture: str, *, horizon_s: float):
    """착석 → 세션 시작 → FOCUS 뒤 posture 만 계속. (FOCUS 진입 시각, 첫 램프 명령 시각, 최대 c_fatigue, 본 상태들)."""
    period, t = hub.period, 0.0
    focus_at = lamp_at = None
    c_max = 0.0
    seen = []
    while t <= horizon_s:
        cache.put("mmwave", mm(t, level=5)); cache.put("env", env(t, co2=700))
        cache.put("tof_service", svc(t, posture=posture, conf=1.0))
        hub.tick_once(t)
        st = hub.engine.state
        seen.append(st)
        if focus_at is None and st in (State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC):
            focus_at = t
        if focus_at is not None and hub._last_result is not None:
            c_max = max(c_max, float(hub._last_result.scores.c_fatigue))
        if lamp_at is None and hub.mock_plug is not None and "desk_lamp" in hub.mock_plug.state:
            lamp_at = t
        if st is State.ACTION_POSTURE and hub.control.episode and hub.control.episode.awaiting_user:
            pytest.fail(f"{posture}: 자동이어야 하는데 제안 카드가 떴다 (gate {hub._last_result.gate})")
        t += period
    return focus_at, lamp_at, c_max, seen


@pytest.mark.parametrize("profile", PROFILES)
def test_face_down_alone_turns_the_lamp_on_quickly(tmp_path, profile):
    hub, cache, states = _hub(tmp_path, profile)
    baseline = float(hub.fsm_cfg["timers"]["baseline_sec"])
    focus_at, lamp_at, c_max, seen = _run(hub, cache, "face_down", horizon_s=baseline + 240)
    assert focus_at is not None and State.FATIGUE in seen and State.ACTION_POSTURE in seen, seen[-12:]
    assert lamp_at is not None and lamp_at - focus_at <= 120, (focus_at, lamp_at)
    cmd = hub.mock_plug.state["desk_lamp"]
    assert cmd["adjust_brightness"] == {"percent": 60, "min_raw": 153} and cmd["brightness"] == 176
    ep = [e for e in hub.control.history + ([hub.control.episode] if hub.control.episode else []) if e.state == "ACTION_POSTURE"][0]
    assert ep.gate is GateMode.AUTO and ep.cause == "posture"
    assert c_max >= hub.fsm_cfg["thresholds"]["fatigue_confirm"] + 0.1, c_max   # 여유 있게 넘는다


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("posture", ["recline", "chin_rest"])
def test_recline_or_chin_rest_alone_does_not_confirm(tmp_path, profile, posture):
    hub, cache, _ = _hub(tmp_path, profile)
    baseline = float(hub.fsm_cfg["timers"]["baseline_sec"])
    focus_at, lamp_at, c_max, seen = _run(hub, cache, posture, horizon_s=baseline + 900)
    assert focus_at is not None and State.FATIGUE not in seen and lamp_at is None
    assert c_max < hub.fsm_cfg["thresholds"]["fatigue_confirm"], c_max


def test_default_profile_face_down_alone_stays_below_confirm(tmp_path):
    hub, cache, _ = _hub(tmp_path, "")
    assert hub.fsm_cfg["weights"]["fatigue"]["npc"]["posture"] == 0.40
    focus_at, lamp_at, c_max, seen = _run(hub, cache, "face_down", horizon_s=300 + 900)
    assert focus_at is not None and State.FATIGUE not in seen and lamp_at is None
    assert 0.50 <= c_max < hub.fsm_cfg["thresholds"]["fatigue_confirm"], c_max


def _frame(posture_delta: float, *, elapsed: float, keystroke: float | None, pc_ratio: float) -> SensorFrame:
    signals = {"posture": Signal(phi=0.0, delta=posture_delta, available=True),
               "environment": Signal(phi=0.0, delta=0.0, available=True),
               "elapsed": Signal(phi=0.0, delta=elapsed, available=True),
               "respiration": Signal(available=False),
               "keystroke": Signal(phi=0.0, delta=keystroke, available=True) if keystroke is not None else Signal(available=False)}
    return SensorFrame(now=0.0, present=True, pc_ratio=pc_ratio, signals=signals)


@pytest.mark.parametrize("profile", PROFILES)
def test_score_table_matches_the_profile_comment(profile):
    """프로파일 주석의 점수표: face_down 0.80 · recline 0.48 · (타이핑 멈춤) pc·npc·MIXED 모두 같다."""
    fsm, _, _ = apply_profile(load_config(), {}, profile)
    for ctx, r in ((Context.NPC, 0.1), (Context.PC, 0.9), (Context.MIXED, 0.5)):
        fd = compute_scores(_frame(1.0, elapsed=0.02, keystroke=None, pc_ratio=r), ctx, fsm).c_fatigue
        rc = compute_scores(_frame(0.6, elapsed=0.02, keystroke=None, pc_ratio=r), ctx, fsm).c_fatigue
        assert fd == pytest.approx(0.802, abs=0.01) and rc == pytest.approx(0.482, abs=0.01), (ctx, fd, rc)
    typing = compute_scores(_frame(1.0, elapsed=0.1, keystroke=0.1, pc_ratio=0.9), Context.PC, fsm).c_fatigue
    assert typing == pytest.approx(0.63, abs=0.02)
    late_recline = compute_scores(_frame(0.6, elapsed=1.0, keystroke=None, pc_ratio=0.1), Context.NPC, fsm).c_fatigue
    assert late_recline < 0.60                                             # 90 분 기대기도 demo-b 확정(0.60) 아래


@pytest.mark.parametrize("profile", PROFILES)
def test_after_waking_upright_score_falls_so_monitor_recovers_not_escalates(tmp_path, profile):
    """PC 시뮬 a1(끄덕임+키 입력 저하)에서는 깬 뒤에도 점수가 0.61 에 머물러 MONITOR → ESCALATE → 강제 휴식이 됐다.
    시연 가중치는 자세가 지배(0.80)하므로 엎드림 → 램프 → 바로 앉으면(upright δ 0) 점수가 한 tick 에 ~0.1 로 떨어지고
    MONITOR(창 30 s)가 RECOVERY 로 간다. 학습기(−0.05)는 keep_floor 아래에서만 깎으므로 여기 영향 없다."""
    hub, cache, _ = _hub(tmp_path, profile)
    period, t = hub.period, 0.0
    lamp_at = None
    posture = "face_down"
    seen_after = []
    scores_after = []
    while t <= float(hub.fsm_cfg["timers"]["baseline_sec"]) + 600:
        cache.put("mmwave", mm(t, level=5 if posture == "face_down" else 40)); cache.put("env", env(t, co2=700))
        cache.put("tof_service", svc(t, posture=posture, conf=1.0))
        hub.tick_once(t)
        if lamp_at is None and hub.mock_plug is not None and "desk_lamp" in hub.mock_plug.state:
            lamp_at = t
            posture = "upright"                                   # 램프가 켜지자 9 s 뒤 깨어 앉았다(PC a1 과 같이)
        elif lamp_at is not None:
            seen_after.append(hub.engine.state)
            scores_after.append(float(hub._last_result.scores.c_fatigue))
            if hub.engine.state is State.RECOVERY:
                break
        t += period
    assert lamp_at is not None and State.RECOVERY in seen_after, seen_after[-10:]
    assert State.ESCALATE not in seen_after and State.REST not in seen_after
    assert min(scores_after) < 0.2, scores_after                  # 앉은 뒤 점수가 바로 떨어진다(엎드림 0.80 → ~0.1)
    assert t - lamp_at <= 90, (lamp_at, t)                        # ACTION 완료 + MONITOR 창 30 s 안에 회복 판정
