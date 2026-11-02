"""빠른 시연 모드(demo_mode.py, fsm.yaml demo_fast:, 2026-10-31).

화면 버튼 → deskmate/demo/mode {"speed":"fast"} → FSM 피로 유지 타이머를 줄여 엎드림 → 무드등 명령이 빨라지고,
"normal" 이면 되돌린다. 판정 경로(자세 서비스 → 점수 → FSM 상태 순서)는 그대로다 — 기대기만으로는 여전히 확정하지 않는다.
"""
from __future__ import annotations

import json

import pytest

from deskmate_hub.demo_mode import DemoSpeed
from deskmate_hub.inference import State
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.cache import Sample
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message

from test_demo_face_down import _hub
from test_ingest_mapping import env, mm
from test_tof_service import svc

CFG = {"enabled": True, "max_on_sec": 600,
       "timers": {"fatigue_elevated_hold_sec": 0, "fatigue_confirm_hold_sec": 0, "no_such_timer": 1}}


def _sample(t: float, speed: str) -> Sample:
    return Sample(received=t, ts=t, seq=None, data={"speed": speed})


def test_fast_overrides_known_timers_and_normal_restores():
    timers = {"fatigue_elevated_hold_sec": 20, "fatigue_confirm_hold_sec": 20, "rest_min_sec": 60}
    d = DemoSpeed(CFG, timers)
    d.observe(_sample(1.0, "fast"), 1.0)
    assert timers == {"fatigue_elevated_hold_sec": 0.0, "fatigue_confirm_hold_sec": 0.0, "rest_min_sec": 60}
    assert "no_such_timer" not in timers                       # 모르는 키는 만들지 않는다
    snap = d.snapshot(11.0)
    assert snap["speed"] == "fast" and snap["remaining_s"] == 590 and snap["timers"]["fatigue_confirm_hold_sec"] == 0
    d.observe(_sample(1.0, "fast"), 12.0)                      # 같은 표본을 다시 봐도 아무 일 없다
    d.observe(_sample(20.0, "normal"), 20.0)
    assert timers == {"fatigue_elevated_hold_sec": 20, "fatigue_confirm_hold_sec": 20, "rest_min_sec": 60}
    assert d.snapshot(21.0) == {"speed": "normal", "enabled": True}


def test_disabled_ignores_request_and_max_on_turns_it_off():
    timers = {"fatigue_elevated_hold_sec": 180, "fatigue_confirm_hold_sec": 180}
    off = DemoSpeed(dict(CFG, enabled=False), timers)
    off.observe(_sample(1.0, "fast"), 1.0)
    assert off.speed == "normal" and timers["fatigue_confirm_hold_sec"] == 180
    on = DemoSpeed(CFG, timers)
    on.observe(_sample(1.0, "fast"), 1.0)
    on.observe(None, 600.0)
    assert on.speed == "fast"
    on.observe(None, 601.0)
    assert on.speed == "normal" and on.last_reason == "max_on" and timers["fatigue_confirm_hold_sec"] == 180


def test_topic_routes_to_cache_envelope_or_bare():
    cache = SensorCache()
    body = {"schema_version": "1.0", "ts": 5.0, "node": "display", "data": {"speed": "fast"}}
    assert route_mqtt_message(cache, "deskmate/demo/mode", json.dumps(body).encode(), now=5.0) == "demo_mode"
    assert cache.snapshot().demo_mode.data == {"speed": "fast"}
    assert route_mqtt_message(cache, "deskmate/demo/mode", b'{"speed": "normal"}', now=6.0) == "demo_mode"
    assert cache.snapshot().demo_mode.data["speed"] == "normal"
    assert route_mqtt_message(cache, "deskmate/demo/mode", b"not json", now=7.0) is None


def test_default_config_is_off_and_demo_profiles_turn_it_on(tmp_path):
    hub, _, _ = _hub(tmp_path, "")
    assert hub.demo_speed.enabled is False
    for profile in ("demo-a", "demo-b", "demo-b-study"):
        hub, _, _ = _hub(tmp_path, profile)
        assert hub.demo_speed.enabled is True, profile


def _run(hub, cache, *, speed: str | None, posture: str = "face_down", horizon: float = 240):
    """바르게 앉아 기준선 → FOCUS → posture 시작. (posture 시작 시각, 첫 램프 시각, 본 상태들, 마지막 state/phase)."""
    period, t = hub.period, 0.0
    start = lamp_at = None
    seen, last = [], None
    if speed:
        cache.put("demo_mode", _sample(0.0, speed))
    baseline = float(hub.fsm_cfg["timers"]["baseline_sec"])
    while t <= baseline + horizon:
        focus = hub.engine.state in (State.FOCUS_PC, State.FOCUS_MIXED, State.FOCUS_NPC)
        if start is None and focus and t >= baseline + 30:
            start = t
        p = posture if start is not None else "upright"
        cache.put("mmwave", mm(t, level=5)); cache.put("env", env(t, co2=700))
        cache.put("tof_service", svc(t, posture=p, conf=1.0))
        hub.tick_once(t)
        seen.append(hub.engine.state)
        if lamp_at is None and hub.mock_plug is not None and "desk_lamp" in hub.mock_plug.state:
            lamp_at = t
        t += period
    return start, lamp_at, seen


@pytest.mark.parametrize("profile", ["demo-a", "demo-b", "demo-b-study"])
def test_fast_mode_lamp_within_30s_and_normal_is_slower(tmp_path, profile):
    fast, cache, states = _hub(tmp_path / "f", profile)
    start, lamp_at, _ = _run(fast, cache, speed="fast")
    assert start is not None and lamp_at is not None and lamp_at - start <= 30, (start, lamp_at)
    assert states[-1]["data"]["sensor_summary"]["demo_mode"]["speed"] == "fast"

    normal, cache, states = _hub(tmp_path / "n", profile)
    n_start, n_lamp, _ = _run(normal, cache, speed=None)
    assert n_lamp is not None and n_lamp - n_start > lamp_at - start, (n_start, n_lamp, start, lamp_at)
    assert states[-1]["data"]["sensor_summary"]["demo_mode"] == {"speed": "normal", "enabled": True}


def test_fast_mode_still_needs_a_sleepy_posture(tmp_path):
    """유지 시간만 줄인다 — 기대기(점수 0.48 < 확정 0.60)는 빠른 시연에서도 피로로 확정하지 않는다."""
    hub, cache, _ = _hub(tmp_path, "demo-b")
    _, lamp_at, seen = _run(hub, cache, speed="fast", posture="recline", horizon=600)
    assert lamp_at is None and State.FATIGUE not in seen


# ── 시연 개인화 장면(2026-10-31 오프라인 확인): 되돌리기·'조명 25%' 한 번이 다음 엎드림의 기기를 바꾸나 ──────────────

STUDENT = {"light_pref": "high", "sound_pref": "low", "wind_pref": "off"}


def _personal_hub(tmp_path):
    import io
    from deskmate_hub.live import LiveHub
    from deskmate_hub.personalize import load_personalize_config
    from test_posture_actions import CFG as CONTROL_CFG
    cache, reqs = SensorCache(), []
    pcfg = dict(load_personalize_config(), log_dir=str(tmp_path))
    hub = LiveHub(cache, control_cfg=CONTROL_CFG, publish=lambda e: None, publish_request=reqs.append, out=io.StringIO(),
                  profile="demo-b", personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"))
    cache.put("user_profile", Sample(received=0.0, ts=0.0, seq=1, data=dict(STUDENT)))
    cache.put("demo_mode", _sample(0.0, "fast"))
    return hub, cache


def _two_naps(hub, cache, reaction: str | None, gap_s: float):
    """60 s 바르게 → 엎드림 → 무드등 → 20 s 뒤 reaction → 깨어 앉음 → 무드등 gap_s 뒤 다시 엎드림. (첫 arm, 두 번째 arm, 첫 밝기 뒤 밝기들)."""
    t, period = 0.0, hub.period
    lamp_at = second_at = None
    arms, brightness = [], []
    while t < 1500:
        down = (t >= 60 and lamp_at is None) or (second_at is not None and t >= second_at)
        if lamp_at is not None and second_at is None and t >= lamp_at + gap_s:
            second_at, down = t, True
        p = "face_down" if down else "upright"
        cache.put("mmwave", mm(t, level=5 if down else 40)); cache.put("env", env(t, co2=700))
        cache.put("tof_service", svc(t, posture=p, conf=1.0))
        if lamp_at is not None and t == lamp_at + 20 and reaction == "undo":
            cache.put_feedback({"request_id": "atlas-display", "verdict": "reject", "response_ms": 0}, received=t)
        if lamp_at is not None and t == lamp_at + 20 and reaction == "dim25":     # V27 '조명 25%'
            cache.put_feedback({"kind": "manual_adjust", "target_id": "desk_lamp", "from": 176, "to": 64}, received=t)
        hub.tick_once(t)
        view = hub.personalize_recorder.last_view or {}
        if hub.engine.state is State.ACTION_POSTURE and view.get("arm") and (not arms or second_at is not None and len(arms) == 1):
            arms.append(view["arm"])
        if lamp_at is None and "desk_lamp" in hub.mock_plug.state:
            lamp_at = t
        if lamp_at is not None and second_at is None:
            brightness.append(hub.mock_plug.brightness.get("desk_lamp"))
        if len(arms) == 2:
            break
        t += period
    return arms, brightness


@pytest.mark.parametrize("reaction,second", [(None, "wake_light"), ("undo", "sound_cue"), ("dim25", "sound_cue")])
def test_one_reaction_changes_the_next_device_after_cooldown(tmp_path, reaction, second):
    """수험생 온보딩·demo-b(greedy): 반응이 없으면 다음에도 무드등, 되돌리기(reject → undo −2)나 '조명 25%'(manual_adjust → 암묵 −1)
    한 번이면 다음 엎드림에 알림음. 단 무드등 arm 쿨다운(personalize cooldown_sec 300) 뒤에 다시 엎드려야 이 차이가 보인다."""
    hub, cache = _personal_hub(tmp_path)
    arms, brightness = _two_naps(hub, cache, reaction, gap_s=400)
    assert arms == ["wake_light", second], arms
    if reaction == "undo":
        assert brightness[0] == 176 and brightness[-1] < 176, brightness      # 되돌리기는 무드등을 이전 밝기로 내린다


def test_within_cooldown_the_next_device_changes_even_without_reaction(tmp_path):
    """시연 멘트 주의: 무드등 뒤 5분 안에 다시 엎드리면 반응이 없어도 알림음이 나간다(같은 arm 쿨다운) — 개인화의 증거로 쓰면 안 된다."""
    hub, cache = _personal_hub(tmp_path)
    arms, _ = _two_naps(hub, cache, None, gap_s=120)
    assert arms == ["wake_light", "sound_cue"], arms
