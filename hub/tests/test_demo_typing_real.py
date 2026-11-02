"""시연 프로파일에서 바르게 앉아 타이핑하는 사람은 FOCUS 에 남는다 (2026-10-26, 보드 실기 결함).

보드(demo-b, 18:22~19:22) 실기: 자세 모델 upright 0.78 인데 FATIGUE_SUSPECT 가 세션 57 분 중 760 s(22 %), 마지막 15 분 tick 의 23 %.
state/phase 19:19 프레임의 점수 c_fsm 0.535 =
  키 입력 0.25 × 1.0   — 실측 flight_cv 1.10(기본 램프 0.25~0.70 → 1.0)·idle 0.65(0.10~0.60 → φ 1.0). 선형 정규화(demo)라 절대 문턱
  자세   0.60 × 0.3   — 0x03 규칙 플래그 true·끄덕임 0.117 Hz(≥ 0.05 → 1.0, 상한 0.3). 모델 라벨은 upright → 0
  환경   0.075 × 1.0  — lux 11 (too_dark)
  경과   0.075 × 0.29
→ 학습기(common, α 0.3) 뒤 0.485 ≥ 의심 0.40. 같은 키 입력 φ 1.0 으로 c_focus 0.45 ≥ 0.30 → FOCUS_BREAK 도 잦다.
오늘 collector 로그(typing_active 창 14 753개): idle p50 0.83·p90 0.99, flight_cv p50 0.83·p90 1.21 — 기본 램프는 baseline 정규화의 폴백이라
선형 시연에선 깨어 일하는 창의 70~81 % 가 δ·φ 1.0 이다. 그래서 시연 프로파일은
  (1) ingest.keystroke 램프를 오늘 p90 위로(idle 0.99~1.0 · cv 1.2~2.0 · 정정 0.6) — 보드 창 δ 0.03·φ 0
  (2) ingest.tof.rule_drowsy_delta·nod_delta_max 0 (10-25 0.3) — 0x03 규칙은 타이핑하는 사람의 고개 움직임을 끄덕임으로 센다(recall 0.16)
둘 중 하나만으로는 안 된다: (2)만이면 0.347 이지만 c_focus 0.45 → FOCUS_BREAK → 180 s 뒤 점수가 경과만큼 오르면 FATIGUE_SUSPECT, 자연 회복 0.30
위라 안 나온다. (1)만이면 0.18+0.075+e 로 0.28~0.33, FOCUS 엔 남지만 여유 0.07 이고 0.30 위라 한 번 의심에 들면 안 나온다.
엎드림(모델 δ 1.0)은 그대로 확정한다. 보드 기본(램프·상한 1.0·baseline 정규화)은 그대로.
"""
from __future__ import annotations

import pytest

from deskmate_hub.inference import State, load_config
from deskmate_hub.inference.context import Context
from deskmate_hub.inference.scoring import compute_scores
from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config
from deskmate_hub.profile import apply_profile

from test_demo_face_down import PROFILES, _hub
from test_ingest_mapping import env, ks, mm, tof
from test_tof_service import svc

BOARD_KS = dict(idle=0.65, cv=1.096, corr=0.0167)     # 보드 19:19 키 입력 창(60 s): 타이핑 중, mouse 243/min
P90_KS = dict(idle=0.99, cv=1.21, corr=0.27)          # 오늘 typing_active 창의 p90 — 보통 날의 윗끝
BOARD_ENV = dict(co2=852, lux=11, temp=28.1)           # too_hot·too_dark
OLD_CAP = {"rule_drowsy_delta": 0.3, "nod_delta_max": 0.3}
LINEAR = [p for p in PROFILES if p != "demo-a"]       # demo-a 는 baseline 정규화 — 일정한 키 입력 통계는 δ 0 이라 보드 결함이 안 난다


def _old_ramps():
    return dict(load_ingest_config()["keystroke"])


def _fill(cache: SensorCache, now: float, *, label="upright", conf=0.78, flag=True, nod=0.117, typing=True, keys=BOARD_KS) -> None:
    cache.put("mmwave", mm(now, level=40, dist=59))
    cache.put("env", env(now, **BOARD_ENV))
    cache.put("tof", tof(now, posture="upright", drowsy=flag, nod=nod, motion=0.06))   # ESP32 0x03 규칙
    cache.put("tof_service", svc(now, posture=label, conf=conf))                        # 자세 모델
    if typing:
        cache.put("keystroke", ks(now, **keys))


def _frame(ingest, *, elapsed_s: float, **kw):
    now = 10_000.0
    cache = SensorCache()
    _fill(cache, now, **kw)
    return build_frame(cache.snapshot(), now, ingest, SessionTracker(session_started=now - elapsed_s), fsm_state=State.FOCUS_PC)


@pytest.mark.parametrize("profile", PROFILES)
def test_board_1026_upright_typing_frame_scores(profile):
    """보드 19:19 프레임: 10-25 설정이면 0.527(보드 c_fsm 0.535), 지금은 0.104. (2)만 0.347 · (1)만 0.282."""
    fsm, ingest, _ = apply_profile(load_config(), load_ingest_config(), profile)
    assert ingest["tof"]["rule_drowsy_delta"] == 0.0 and ingest["tof"]["nod_delta_max"] == 0.0
    assert ingest["keystroke"] == {"idle_ratio_low": 0.99, "idle_ratio_high": 1.0, "flight_cv_low": 1.2, "flight_cv_high": 2.0,
                                   "correction_rate_high": 0.6}
    low, recover = fsm["thresholds"]["fatigue_focus_low"], fsm["thresholds"]["natural_recovery"]
    for conf in (0.78, 0.5):                                                  # 수락 기준: 서비스 upright 는 conf ≥ 0.5 면 어떤 값이든
        g = _frame(ingest, elapsed_s=1560, conf=conf)
        assert g.get("posture").delta == 0.0 and compute_scores(g, Context.PC, fsm).c_fatigue < recover, conf
    f = _frame(ingest, elapsed_s=1560)
    assert f.get("keystroke").delta == pytest.approx(0.028, abs=0.005) and f.get("keystroke").phi == 0.0
    assert f.get("environment").delta == 1.0 and f.get("posture").delta == 0.0
    s = compute_scores(f, Context.PC, fsm)
    assert s.c_fatigue == pytest.approx(0.104, abs=0.01) and s.c_fatigue < recover < low, s
    assert s.c_focus < fsm["thresholds"]["focus_break"], s.c_focus                    # 집중 저하도 아니다
    # 10-25 설정(기본 램프 + 상한 0.3): 보드와 같은 0.527 · c_focus 0.45
    old = dict(ingest, tof=dict(ingest["tof"], **OLD_CAP), keystroke=_old_ramps())
    f_old = _frame(old, elapsed_s=1560)
    assert f_old.get("keystroke").delta == 1.0 and f_old.get("keystroke").phi == 1.0 and f_old.get("posture").delta == pytest.approx(0.3)
    s_old = compute_scores(f_old, Context.PC, fsm)
    assert s_old.c_fatigue == pytest.approx(0.527, abs=0.01) and s_old.c_fatigue >= low
    assert s_old.c_focus == pytest.approx(0.447, abs=0.01) and s_old.c_focus >= fsm["thresholds"]["focus_break"]
    # 하나만: (2)만 0.347(0.30 위) · (1)만 0.282
    only_cap = compute_scores(_frame(dict(ingest, keystroke=_old_ramps()), elapsed_s=1560), Context.PC, fsm).c_fatigue
    only_ramp = compute_scores(_frame(dict(ingest, tof=dict(ingest["tof"], **OLD_CAP)), elapsed_s=1560), Context.PC, fsm).c_fatigue
    assert only_cap == pytest.approx(0.347, abs=0.01) and only_cap > recover
    assert only_ramp == pytest.approx(0.282, abs=0.01)
    # 오늘 p90 창(idle 0.99·cv 1.21·정정 0.27)·어두운 방·휴식 없이 90 분: 0.25·0.45 + 0.075 + 0.075 = 0.26 < 0.30
    worst = compute_scores(_frame(ingest, elapsed_s=5400, keys=P90_KS), Context.PC, fsm)
    assert worst.c_fatigue < recover and worst.c_focus < fsm["thresholds"]["focus_break"], worst


def test_board_default_ramps_and_rule_flag_unchanged():
    ingest = load_ingest_config()
    assert ingest["keystroke"] == {"idle_ratio_low": 0.10, "idle_ratio_high": 0.60, "flight_cv_low": 0.25, "flight_cv_high": 0.70,
                                   "correction_rate_high": 0.15}
    f = _frame(ingest, elapsed_s=1560)
    assert f.get("posture").delta == 1.0 and f.get("keystroke").delta == 1.0


def _run(hub, cache, timeline, *, horizon_s: float):
    """timeline(t) → (모델 라벨, 0x03 플래그, 끄덕임 Hz, 타이핑). 보드 실측 키 입력·환경 값으로 돌린다."""
    period, t = hub.period, 0.0
    focus_at = lamp_at = None
    c_max, seen = 0.0, []
    while t <= horizon_s:
        label, flag, nod, typing = timeline(t)
        _fill(cache, t, label=label, conf=1.0 if label == "face_down" else 0.5, flag=flag, nod=nod, typing=typing)   # upright 는 conf 0.5(수락 기준 하한)
        hub.tick_once(t)
        st = hub.engine.state
        seen.append(st)
        if focus_at is None and st in (State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC):
            focus_at = t
        if focus_at is not None and hub._last_result is not None:
            c_max = max(c_max, float(hub._last_result.scores.c_fatigue))
        if lamp_at is None and hub.mock_plug is not None and "desk_lamp" in hub.mock_plug.state:
            lamp_at = t
        t += period
    return focus_at, lamp_at, c_max, seen


ALWAYS_TYPING = lambda t: ("upright", True, 0.2, True)   # noqa: E731 — 플래그 true·끄덕임 0.2 Hz 내내, 보드 키 입력 창


@pytest.mark.parametrize("profile", PROFILES)
def test_upright_typing_with_rule_flag_and_nods_stays_in_focus_pc(tmp_path, profile):
    """600 s 내내 타이핑(보드 창)·플래그·끄덕임·어두운 방, 학습기 없이(순수 FSM): FOCUS_PC 만, 의심·집중 저하 없음."""
    hub, cache, _ = _hub(tmp_path, profile)
    hub.engine.score_adjust = None
    base = float(hub.fsm_cfg["timers"]["baseline_sec"])
    focus_at, lamp_at, c_max, seen = _run(hub, cache, ALWAYS_TYPING, horizon_s=base + 600)
    assert focus_at is not None and lamp_at is None, seen[-8:]
    after = seen[seen.index(State.FOCUS_PC):]
    assert set(after) == {State.FOCUS_PC}, sorted({s.value for s in after})
    assert c_max < hub.fsm_cfg["thresholds"]["natural_recovery"], c_max


@pytest.mark.parametrize("profile", LINEAR)
def test_one_change_alone_is_not_enough(tmp_path, profile):
    """10-25 설정은 보드처럼 의심(직접 경로). 상한 0 만: 집중 저하 → 180 s 뒤 경과만큼 오른 점수로 의심, 0.30 위라 못 나온다."""
    base = float(load_config()["timers"]["baseline_sec"])
    hub, cache, _ = _hub(tmp_path, profile); hub.engine.score_adjust = None
    hub.ingest_cfg["tof"].update(OLD_CAP); hub.ingest_cfg["keystroke"] = _old_ramps()
    _, _, c_max, seen = _run(hub, cache, ALWAYS_TYPING, horizon_s=10 + 600)
    assert State.FATIGUE_SUSPECT in seen and c_max == pytest.approx(0.53, abs=0.02), (c_max, seen[-6:])

    hub2, cache2, _ = _hub(tmp_path, profile); hub2.engine.score_adjust = None
    hub2.ingest_cfg["keystroke"] = _old_ramps()                       # (2)만
    _, _, c_max2, seen2 = _run(hub2, cache2, ALWAYS_TYPING, horizon_s=10 + 600)
    assert State.FOCUS_BREAK in seen2 and State.FATIGUE_SUSPECT in seen2, seen2[-6:]
    assert seen2[-1] is State.FATIGUE_SUSPECT and 0.30 < c_max2 < 0.40, (c_max2, seen2[-6:])   # 들어가서 안 나온다

    hub3, cache3, _ = _hub(tmp_path, profile); hub3.engine.score_adjust = None
    hub3.ingest_cfg["tof"].update(OLD_CAP)                            # (1)만 — FOCUS 엔 남지만 0.27(10 분)~0.33(90 분), 0.30 을 넘나든다
    _, _, c_max3, seen3 = _run(hub3, cache3, ALWAYS_TYPING, horizon_s=10 + 600)
    assert State.FATIGUE_SUSPECT not in seen3 and c_max3 == pytest.approx(0.27, abs=0.02), (c_max3, seen3[-6:])


@pytest.mark.parametrize("profile,keep_typing", [(p, False) for p in PROFILES] + [(p, True) for p in LINEAR])
def test_face_down_after_typing_still_lights_the_lamp_fast(tmp_path, profile, keep_typing):
    """엎드림(모델 δ 1.0)은 램프·상한과 무관. 타이핑하다 엎드리면(키 입력 창이 멈춰 빠짐) 90 s 안에 램프; demo-b 는 창이 살아 있어도
    0.60 + 0.25·0.03 + 0.075 ≥ 확정 0.60. (demo-a 는 확정 0.70 이라 타이핑이 이어지면 0.68 — 10-25 설계대로 타이핑 멈춤이 전제)"""
    hub, cache, _ = _hub(tmp_path, profile)
    hub.engine.score_adjust = None
    base = float(hub.fsm_cfg["timers"]["baseline_sec"])
    down_at = base + 60
    focus_at, lamp_at, c_max, seen = _run(
        hub, cache, lambda t: ("face_down", True, 0.1, keep_typing) if t >= down_at else ("upright", True, 0.1, True),
        horizon_s=base + 300)
    assert focus_at is not None and lamp_at is not None, seen[-10:]
    assert lamp_at - down_at <= 90, (down_at, lamp_at, seen[-12:])
    assert c_max >= hub.fsm_cfg["thresholds"]["fatigue_confirm"], c_max
