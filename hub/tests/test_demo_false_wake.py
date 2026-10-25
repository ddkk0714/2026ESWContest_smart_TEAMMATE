"""시연 프로파일에서 0x03 규칙 졸음 플래그·끄덕임만으로는 무드등이 헛켜지지 않는다 (2026-10-25, PC 시뮬 재실행 결함).

자세 몫을 0.80 으로 올리자(becd35f) 규칙 플래그(δ 1.0)·끄덕임이 깨어 있는 사람(턱 괴기·타이핑)에게 램프를 켰다. 시연 프로파일은
`ingest.tof.rule_drowsy_delta`·`nod_delta_max` 0.3 으로 눌러 플래그·끄덕임만의 점수를 0.24 (< 의심 0.40) 로 둔다. 자세 모델 라벨
(face_down·drowsy)은 그대로 1.0. 깬 뒤 플래그가 40 s 남아도 still_asleep(0.9)에 못 미치고, 점수가 회복 수준이면 MONITOR 는
진입 대비 부호 잡음으로 격상하지 않는다(thresholds.monitor_recovered_below). 보드 기본(상한 1.0·null)은 그대로다.
"""
from __future__ import annotations

import pytest

from deskmate_hub.inference import FSMEngine, State, load_config
from deskmate_hub.inference.context import Context
from deskmate_hub.inference.scoring import compute_scores
from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config
from deskmate_hub.profile import apply_profile

from test_demo_face_down import PROFILES, _frame, _hub
from test_fsm_transitions import drive_to_focus, frame as fsm_frame
from test_ingest_mapping import env, ks, mm, tof
from test_tof_service import svc


def _run(hub, cache, timeline, *, horizon_s: float):
    """timeline(t) → (모델 라벨, 0x03 플래그, 끄덕임 Hz, 타이핑). (FOCUS 시각, 램프 시각, FOCUS 뒤 최대 C, 상태들, 상태별 시각)."""
    period, t = hub.period, 0.0
    focus_at = lamp_at = None
    c_max, seen, at = 0.0, [], {}
    while t <= horizon_s:
        label, flag, nod, typing = timeline(t)
        cache.put("mmwave", mm(t, level=40 if typing else 5)); cache.put("env", env(t, co2=700))
        cache.put("tof", tof(t, posture="upright", drowsy=flag, nod=nod))           # ESP32 0x03 규칙 특징(플래그·끄덕임)
        cache.put("tof_service", svc(t, posture=label, conf=0.92))                  # 자세 모델 라벨
        if typing:
            cache.put("keystroke", ks(t, idle=0.2, cv=0.3))
        hub.tick_once(t)
        st = hub.engine.state
        seen.append(st); at.setdefault(st, t)
        if focus_at is None and st in (State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC):
            focus_at = t
        if focus_at is not None and hub._last_result is not None:
            c_max = max(c_max, float(hub._last_result.scores.c_fatigue))
        if lamp_at is None and hub.mock_plug is not None and "desk_lamp" in hub.mock_plug.state:
            lamp_at = t
        t += period
    return focus_at, lamp_at, c_max, seen, at


@pytest.mark.parametrize("profile", PROFILES)
def test_chin_rest_with_rule_flag_and_nods_does_not_light_the_lamp(tmp_path, profile):
    """턱 괴기 600 s, 안 졸음: 시뮬 0x03 플래그 49/60 tick·끄덕임 2~6 → 8a43ef5 에서는 C 0.81 로 102 s 에 램프."""
    hub, cache, _ = _hub(tmp_path, profile)
    base = float(hub.fsm_cfg["timers"]["baseline_sec"])
    focus_at, lamp_at, c_max, seen, _ = _run(
        hub, cache, lambda t: ("chin_rest", (int(t / 10) % 60) < 49, 0.05 if (int(t / 10) % 60) < 49 else 0.0, False),
        horizon_s=base + 600)
    assert focus_at is not None and lamp_at is None and State.FATIGUE not in seen, (lamp_at, seen[-8:])
    assert c_max < hub.fsm_cfg["thresholds"]["fatigue_confirm"], c_max


@pytest.mark.parametrize("profile", PROFILES)
def test_typing_upright_with_rule_flag_does_not_light_the_lamp(tmp_path, profile):
    """a1: 타이핑 중 200~260 s 에 플래그 true·끄덕임 9~14 → 8a43ef5 에서는 pc C 0.71 로 260 s 에 램프."""
    hub, cache, _ = _hub(tmp_path, profile)
    base = float(hub.fsm_cfg["timers"]["baseline_sec"])

    def timeline(t):
        rel = t - base
        flag = 200 <= rel <= 260
        return "upright", flag, (0.2 if flag else 0.0), True
    focus_at, lamp_at, c_max, seen, _ = _run(hub, cache, timeline, horizon_s=base + 420)
    assert focus_at is not None and lamp_at is None and State.FATIGUE not in seen
    assert c_max < hub.fsm_cfg["thresholds"]["fatigue_suspect_low"], c_max          # 의심(0.40)에도 안 든다


@pytest.mark.parametrize("profile", PROFILES)
def test_nod_then_face_down_then_sit_up_recovers_without_escalate(tmp_path, profile):
    """끄덕임 150 s → 엎드림 → 램프 → 앉음(플래그는 40 s 더 true) → RECOVERY, ESCALATE·강제 휴식 없음."""
    hub, cache, _ = _hub(tmp_path, profile)
    base = float(hub.fsm_cfg["timers"]["baseline_sec"])
    state = {"lamp_at": None}

    def timeline(t):
        rel = t - base
        if state["lamp_at"] is None and hub.mock_plug is not None and "desk_lamp" in hub.mock_plug.state:
            state["lamp_at"] = t
        if state["lamp_at"] is not None:
            flag = t - state["lamp_at"] <= 40                                         # 깬 뒤 규칙 끄덕임 창이 40 s 남는다
            return "upright", flag, (0.1 if flag else 0.0), False
        if rel < 150:
            return "upright", True, 0.1, False                                        # 끄덕임(규칙 플래그)만 — 모델은 upright
        return "face_down", True, 0.1, False
    focus_at, lamp_at, c_max, seen, at = _run(hub, cache, timeline, horizon_s=base + 150 + 600)
    assert lamp_at is not None, seen[-10:]
    assert at.get(State.FATIGUE, 0) >= base + 150                                     # 끄덕임만으로는 확정하지 않았다
    after = seen[seen.index(State.ACTION_POSTURE):]
    assert State.RECOVERY in after and State.ESCALATE not in after and State.REST not in after, after
    assert any(s in (State.FOCUS_NPC, State.FOCUS_MIXED, State.FOCUS_PC) for s in after[after.index(State.RECOVERY):])


@pytest.mark.parametrize("profile", PROFILES)
def test_rule_flag_only_scores_stay_under_suspect(profile):
    """점수표: 플래그·끄덕임만(δ 0.3) → 0.24 (npc·pc 무타이핑), 타이핑 중 pc 0.21. 모델 face_down 은 그대로 0.80."""
    fsm, ingest, _ = apply_profile(load_config(), load_ingest_config(), profile)
    assert ingest["tof"]["rule_drowsy_delta"] == 0.3 and ingest["tof"]["nod_delta_max"] == 0.3
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, level=5)); cache.put("env", env(100.0, co2=700))
    cache.put("tof", tof(100.0, posture="upright", drowsy=True, nod=0.2))                  # 규칙: 플래그 + 끄덕임 과다
    cache.put("tof_service", svc(100.0, posture="upright", conf=0.92))                     # 모델: 바른 자세
    f = build_frame(cache.snapshot(), 100.0, ingest, SessionTracker(), fsm_state=State.FOCUS_NPC)
    assert f.get("posture").delta == pytest.approx(0.3)
    cache.put("tof_service", svc(100.0, posture="drowsy", conf=0.92))                      # 모델이 drowsy 면 1.0 그대로
    assert build_frame(cache.snapshot(), 100.0, ingest, SessionTracker(), fsm_state=State.FOCUS_NPC).get("posture").delta == 1.0
    for ctx, r in ((Context.NPC, 0.1), (Context.PC, 0.9), (Context.MIXED, 0.5)):
        c = compute_scores(_frame(0.3, elapsed=0.02, keystroke=None, pc_ratio=r), ctx, fsm).c_fatigue
        assert c == pytest.approx(0.242, abs=0.01) and c < fsm["thresholds"]["fatigue_suspect_low"], (ctx, c)
    typing = compute_scores(_frame(0.3, elapsed=0.02, keystroke=0.1, pc_ratio=0.9), Context.PC, fsm).c_fatigue
    assert typing == pytest.approx(0.21, abs=0.02)


def test_board_default_rule_flag_still_counts_fully():
    ingest = load_ingest_config()
    assert ingest["tof"]["rule_drowsy_delta"] == 1.0 and ingest["tof"]["nod_delta_max"] == 1.0
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, level=5))
    cache.put("tof", tof(100.0, posture="upright", drowsy=True))
    cache.put("tof_service", svc(100.0, posture="upright", conf=0.92))
    assert build_frame(cache.snapshot(), 100.0, ingest, SessionTracker(), fsm_state=State.FOCUS_NPC).get("posture").delta == 1.0
    assert load_config()["thresholds"]["monitor_recovered_below"] is None


@pytest.mark.parametrize("key,expected", [(None, State.ESCALATE), (0.30, State.RECOVERY)])
def test_monitor_recovered_below_stops_sign_noise_escalation(key, expected):
    """MONITOR 진입 때 C 0.05, 판정 때 0.06(잡음): 기본은 부호로 ESCALATE, 키가 있으면 회복 수준이라 RECOVERY."""
    cfg = load_config()
    cfg["thresholds"]["monitor_recovered_below"] = key
    e = FSMEngine(cfg)
    t = drive_to_focus(e)
    hot = {"posture": {"delta": 0.95}, "elapsed": {"delta": 0.95}, "keystroke": {"delta": 0.95}}
    for i in range(1, 60):
        r = e.tick(fsm_frame(t + i * 10, sig=hot))
        if r.state is State.ACTION_POSTURE or r.state is State.ACTION_BREAK or r.state is State.ACTION_ENV:
            break
    assert e.state in (State.ACTION_POSTURE, State.ACTION_BREAK, State.ACTION_ENV)
    t2 = t + i * 10
    low = {"posture": {"delta": 0.05}, "elapsed": {"delta": 0.05}, "keystroke": {"delta": 0.05}}
    e.tick(fsm_frame(t2 + 10, sig=low, action_done=True))                         # 개입 끝 → MONITOR (진입 C 0.05)
    assert e.state is State.MONITOR
    noisy = {"posture": {"delta": 0.06}, "elapsed": {"delta": 0.06}, "keystroke": {"delta": 0.06}}
    e.tick(fsm_frame(t2 + 20, sig=noisy))
    assert e.state is expected
