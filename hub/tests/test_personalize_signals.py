"""2026-10-25 사용자 설계 — 첫 설정으로 기준, 묻지 않고 얻는 반응으로 맞춰 가기 (personalize/onboarding.py · signals.py).

1. 첫 설정 프로필(deskmate/profile/user) → 전역 prior·후보 제외·취침 시각, 재시작 뒤에도 유지
2. 허브가 무드등을 밝힌 뒤 사용자가 직접 어둡게 함 → 음의 보상 (앱 보고 · 조도 휴리스틱)
3. 졸음·엎드림 깨우기 성공 → 효과 축 보상
"""
from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from deskmate_hub.features.baseline import BaselineStore  # noqa: F401 — live import 경로 확인
from deskmate_hub.inference import State
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.cache import Sample
from deskmate_hub.ingest.mqtt_lines import MqttLineSource, route_mqtt_message
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import (EpisodeRecorder, ImplicitDimTracker, InterventionPolicy, Situation, WakeTracker,
                                      load_personalize_config, parse_profile, read_records)
from deskmate_hub.personalize.features import FEATURE_INDEX
from deskmate_hub.personalize.onboarding import load_profile

from test_ingest_mapping import env, mm
from test_tof_service import svc

HUB = Path(__file__).resolve().parents[1]
BIAS = FEATURE_INDEX["bias"]

PROFILE = {"profile_id": "u-7f3a", "age_band": "20s", "sleep_hours": 5.5, "bedtime": "01:00", "wake_time": "08:30",
           "light_pref": "low", "sound_pref": "off", "wind_pref": "mid", "focus_help": ["break", "light", "카페"],
           "placement": "left", "name": "홍길동", "email": "x@y.z"}


def _envelope(data, ts=1000.0, seq=1):
    return json.dumps({"ts": ts, "node": "pi5-display", "seq": seq, "data": data}).encode("utf-8")


def _cfg(**over):
    cfg = copy.deepcopy(load_personalize_config())
    cfg.update(over)
    return cfg


def _mean(policy, arm, axis="accept", feat=BIAS):
    return policy.models[arm][axis].mean()[feat]


def _sit(**kw):
    base = dict(ts=1e6, hour_local=15.5, weekday=2, month=10, co2_ppm=900, temp_c=24.0, lux=200,
                c_fatigue=0.8, session_elapsed_min=60, posture_label="face_down")
    base.update(kw)
    return Situation(**base)


# ── 1. 첫 설정 프로필 ───────────────────────────────────────────────

def test_parse_profile_keeps_schema_fields_only():
    p = parse_profile(PROFILE)
    assert "name" not in p and "email" not in p                       # 모르는 키(식별 정보)는 버린다
    assert p["focus_help"] == ["light", "break"]                      # 모르는 항목 버림, 순서는 스키마 순
    assert p["light_pref"] == "low" and p["sound_pref"] == "off" and p["sleep_hours"] == 5.5
    bad = parse_profile({"profile_id": "a b/c", "light_pref": "bright", "bedtime": "25:00", "sleep_hours": "8"})
    assert bad is None                                                # 틀린 값만 있으면 아무것도 안 남는다
    assert parse_profile({"light_pref": "high", "bedtime": "7:5"}) == {"light_pref": "high"}
    assert parse_profile("nope") is None


def test_profile_shifts_prior_and_is_idempotent():
    cfg = _cfg()
    p = InterventionPolicy(cfg)
    base_bright, base_dim = _mean(p, "light_cool_bright"), _mean(p, "light_warm_dim")
    notes = p.apply_user_profile(parse_profile(PROFILE))
    m = cfg["onboarding"]["mapping"]
    d_bright = m["light_pref"]["low"]["prior"]["light_cool_bright"]["accept"]["bias"] \
        + m["focus_help"]["light"]["prior"]["light_cool_bright"]["accept"]["bias"]
    assert _mean(p, "light_cool_bright") == pytest.approx(base_bright + d_bright)
    assert _mean(p, "light_warm_dim") > base_dim
    assert _mean(p, "break_short") > cfg["prior"]["break_short"]["accept"]["bias"]   # 수면 부족 + focus_help break
    assert any("어두운 조명" in n for n in notes) and any("소리 끔" in n for n in notes)
    before = list(p.models["light_cool_bright"]["accept"].b)
    p.apply_user_profile(parse_profile(PROFILE))                      # 같은 프로필 다시 → 변화 없음
    assert p.models["light_cool_bright"]["accept"].b == pytest.approx(before)
    p.apply_user_profile({"light_pref": "mid"})                       # 바꾸면 전역 + 새 프로필만
    assert _mean(p, "light_cool_bright") == pytest.approx(base_bright)


def test_profile_shift_keeps_learned_evidence():
    p = InterventionPolicy(_cfg())
    d = p.decide(_sit(co2_ppm=1500), cause="environment", gate="suggest", session_id="s1")
    p.feedback(d, "accept")
    learned = [x - p.models[d.arm]["accept"].lam * t for x, t in zip(p.models[d.arm]["accept"].b,
                                                                     p.models[d.arm]["accept"].theta0)]
    p.apply_user_profile({"light_pref": "low", "wind_pref": "off"})
    after = [x - p.models[d.arm]["accept"].lam * t for x, t in zip(p.models[d.arm]["accept"].b,
                                                                   p.models[d.arm]["accept"].theta0)]
    assert after == pytest.approx(learned)                            # Σrx 는 그대로, prior 만 바뀜


def test_sound_pref_off_removes_sound_and_voice_arms():
    p = InterventionPolicy(_cfg())
    d = p.decide(_sit(), cause="posture", gate="suggest", session_id="s1")
    assert "sound_cue" in d.candidates and "posture_voice" in d.candidates
    p.apply_user_profile({"sound_pref": "off"})
    p.reset_session()
    d = p.decide(_sit(), cause="posture", gate="suggest", session_id="s2")
    assert d.excluded.get("sound_cue") == "user_profile" and d.excluded.get("posture_voice") == "user_profile"
    assert d.arm == "posture_card" and d.candidates == ["posture_card"]
    p.apply_user_profile({"sound_pref": "high"})                       # 다시 켜면 돌아온다
    p.reset_session()
    assert "sound_cue" in p.decide(_sit(), cause="posture", gate="suggest", session_id="s3").candidates


def test_profile_sleep_times_feed_quiet_hours():
    p = InterventionPolicy(_cfg())
    assert p._in_quiet_hours(23.5) and not p._in_quiet_hours(7.5)     # 기본 23~7
    p.apply_user_profile({"bedtime": "01:00", "wake_time": "08:30"})
    assert not p._in_quiet_hours(23.5) and p._in_quiet_hours(7.5) and p._in_quiet_hours(2.0)


def test_mqtt_route_profile_topic():
    cache = SensorCache()
    assert route_mqtt_message(cache, "deskmate/profile/user", _envelope(PROFILE, seq=4), 1000.0) == "user_profile"
    assert cache.snapshot().user_profile.data["light_pref"] == "low" and cache.user_profile.seq == 4
    assert route_mqtt_message(cache, "deskmate/profile/user", b"{not json", 1001.0) is None
    assert route_mqtt_message(cache, "deskmate/profile/user", json.dumps({"ts": 1}).encode(), 1001.0) is None
    src = MqttLineSource(cache)
    assert src.feed_line("MQTT\tdeskmate/profile/user\t" + _envelope({"sound_pref": "low"}).decode()) == "user_profile"


def _live(tmp_path, cache=None, **kw):
    cache = cache or SensorCache()
    states = []
    hub = LiveHub(cache, publish=states.append, out=io.StringIO(), profile="",
                  control_cfg={"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")},
                  personalize_cfg=kw.pop("personalize_cfg", _cfg()),
                  personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "prof" / "policy.json"), **kw)
    return hub, cache, states


def test_profile_message_changes_prior_and_survives_restart(tmp_path):
    hub, cache, states = _live(tmp_path)
    base = _mean(hub.personalize_policy, "light_cool_bright")
    route_mqtt_message(cache, "deskmate/profile/user", _envelope(PROFILE), 1000.0)
    hub.tick_once(1000.0)
    rec = hub.personalize_recorder
    assert _mean(hub.personalize_policy, "light_cool_bright") < base
    assert hub.personalize_policy.disabled == {"sound_cue", "posture_voice"}
    assert rec.logger.profile_id == "u-7f3a" and rec.profile_summary.usual_bedtime == "01:00"
    assert rec._sit_cfg["profile"]["night_end_hour"] == pytest.approx(8.5)
    text = states[-1]["data"]["sensor_summary"]["personalize"]["signal"]["text_ko"]
    assert text.startswith("첫 설정 반영") and "취침 01:00" in text

    path = tmp_path / "prof" / "user_profile.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["profile"]["profile_id"] == "u-7f3a"
    assert "age_band" not in stored["profile"] and "홍길동" not in json.dumps(stored, ensure_ascii=False)
    assert not (tmp_path / "prof" / "user_profile.json.tmp").exists()
    b_saved = list(hub.personalize_policy.models["light_cool_bright"]["accept"].b)

    # 재시작: 브로커에 retain 이 없어도 파일에서 다시 반영 — prior 를 두 번 더하지 않는다
    hub2, cache2, states2 = _live(tmp_path)
    p2 = hub2.personalize_policy
    assert hub2.personalize_recorder.user_profile == load_profile(str(path))
    assert p2.disabled == {"sound_cue", "posture_voice"}
    assert p2.models["light_cool_bright"]["accept"].b == pytest.approx(b_saved)
    route_mqtt_message(cache2, "deskmate/profile/user", _envelope(PROFILE, ts=2000.0), 2000.0)   # retain 재수신
    hub2.tick_once(2000.0)
    assert p2.models["light_cool_bright"]["accept"].b == pytest.approx(b_saved)


def test_onboarding_disabled_ignores_profile(tmp_path):
    cfg = _cfg()
    cfg["onboarding"] = dict(cfg["onboarding"], enabled=False)
    hub, cache, _ = _live(tmp_path, personalize_cfg=cfg)
    base = _mean(hub.personalize_policy, "light_cool_bright")
    route_mqtt_message(cache, "deskmate/profile/user", _envelope(PROFILE), 1000.0)
    hub.tick_once(1000.0)
    assert _mean(hub.personalize_policy, "light_cool_bright") == base
    assert not (tmp_path / "prof" / "user_profile.json").exists()


# ── 2. 밝힘 뒤 직접 어둡게 ──────────────────────────────────────────

def _tracker(**over):
    cfg = dict(load_personalize_config()["implicit"], **over)
    return ImplicitDimTracker(cfg)


def _bright(tr, t, *, cid="c1", pct=60, prev=100, actual=160, eid="e1", decision=None):
    tr.on_command({"command_id": cid, "target_id": "desk_lamp", "operation": "adjust_brightness",
                   "value": {"percent": pct}, "gate": "auto"}, t, episode_id=eid, decision=decision)
    tr.on_result({"command_id": cid, "status": "succeeded", "previous": prev, "actual": actual, "actual_value": actual}, t + 1)


def test_manual_adjust_after_brighten_is_negative():
    tr = _tracker()
    _bright(tr, 1000.0)
    sig = tr.on_manual_adjust({"kind": "manual_adjust", "target_id": "desk_lamp", "from": 160, "to": 60}, 1100.0)
    assert sig["kind"] == "manual_dim_app" and sig["reward"] == load_personalize_config()["implicit"]["app_reward"]
    assert sig["episode_id"] == "e1" and sig["detail"]["after_sec"] == 100.0 and tr.level == 60
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 60, "to": 30}, 1110.0) is None   # 한 번만 센다


def test_manual_adjust_ignored_after_unrelated_or_outside_window_or_brightening():
    tr = _tracker()
    _bright(tr, 1000.0, pct=-50, prev=160, actual=80)                  # 직전 허브 명령이 어둡게
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 80, "to": 40}, 1050.0)["reason"] == "unrelated_command"
    tr = _tracker()
    _bright(tr, 1000.0)
    tr.on_command({"command_id": "u1", "target_id": "desk_lamp", "operation": "set_brightness", "value": 100,
                   "gate": "undo"}, 1030.0)                             # 되돌리기(명시 undo 로 이미 처리)
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 100, "to": 50}, 1060.0)["kind"] == "ignored"
    tr = _tracker()
    _bright(tr, 1000.0)
    late = 1000.0 + load_personalize_config()["implicit"]["dim_window_min"] * 60 + 1
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 160, "to": 60}, late)["reason"] == "outside_window"
    tr = _tracker()
    _bright(tr, 1000.0)
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 160, "to": 220}, 1050.0) is None   # 더 밝게는 신호 아님
    assert tr.on_manual_adjust({"target_id": "vent_fan", "from": 1, "to": 0}, 1060.0) is None
    tr = _tracker()
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 160, "to": 60}, 1000.0)["reason"] == "no_hub_command"


def test_brighten_detection_set_and_adjust():
    tr = _tracker()
    tr.on_command({"command_id": "s1", "target_id": "desk_lamp", "operation": "set_brightness", "value": 200}, 1000.0)
    tr.on_result({"command_id": "s1", "status": "succeeded", "previous": 120, "actual_value": 200}, 1001.0)
    assert tr._brightened(tr.last)
    tr.on_command({"command_id": "s2", "target_id": "desk_lamp", "operation": "set_brightness", "value": 100}, 1100.0)
    tr.on_result({"command_id": "s2", "status": "succeeded", "previous": 200, "actual_value": 100}, 1101.0)
    assert not tr._brightened(tr.last)
    _bright(tr, 1200.0, cid="a1", pct=60, prev=255, actual=255)         # 이미 최대 → 안 밝아짐
    assert not tr._brightened(tr.last)
    _bright(tr, 1300.0, cid="a2", pct=30, prev=100, actual=130)
    assert tr._brightened(tr.last)
    tr.on_command({"command_id": "f", "target_id": "desk_lamp", "operation": "adjust_brightness",
                   "value": {"percent": 30}}, 1400.0)
    tr.on_result({"command_id": "f", "status": "failed"}, 1401.0)       # 실패한 명령은 밝힘 아님
    assert not tr._brightened(tr.last)
    # previous 를 끝내 모르는 옛 결과: brighten_unknown_min 이상이면 밝힘, null 이면 아님
    fresh = _tracker()
    fresh.on_command({"command_id": "o", "target_id": "desk_lamp", "operation": "set_brightness", "value": 230}, 1.0)
    assert fresh._brightened(fresh.last)
    off = _tracker(brighten_unknown_min=None)
    off.on_command({"command_id": "o", "target_id": "desk_lamp", "operation": "set_brightness", "value": 230}, 1.0)
    assert not off._brightened(off.last)


def _lux_run(tr, series):
    """[(t, lux)] 를 차례로 → 마지막으로 나온 신호(없으면 None)."""
    out = None
    for t, lux in series:
        sig = tr.on_lux(lux, t)
        out = sig or out
    return out


def test_lux_heuristic_positive():
    tr = _tracker()
    tr.on_lux(200, 995.0)                                               # 명령 직전 조도
    _bright(tr, 1000.0)
    sig = _lux_run(tr, [(1010.0, 320), (1020.0, 330), (1100.0, 330), (1110.0, 150), (1120.0, 150), (1130.0, 140),
                        (1140.0, 140)])
    assert sig["kind"] == "manual_dim_lux" and sig["reward"] == load_personalize_config()["implicit"]["lux_reward"]
    assert sig["detail"]["lux_before"] == 200 and sig["detail"]["lux_peak"] == 330 and sig["detail"]["hold_sec"] >= 30
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 160, "to": 60}, 1150.0) is None   # 이미 셌다


def test_lux_heuristic_negative_cases():
    # 밝힘 뒤 조도 상승이 없다 → 조도 신호 무시(로그만), 이후 하락도 무시
    tr = _tracker()
    tr.on_lux(200, 995.0)
    _bright(tr, 1000.0)
    first = _lux_run(tr, [(1010.0, 205), (1020.0, 210)])
    assert first["kind"] == "ignored" and first["reason"] == "no_lux_rise"
    assert _lux_run(tr, [(1100.0, 80), (1110.0, 80), (1150.0, 80)]) is None
    # 그래도 앱 보고는 받는다
    assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 160, "to": 60}, 1160.0)["kind"] == "manual_dim_app"
    # 명령 전 조도를 모른다 → 무시
    tr = _tracker()
    _bright(tr, 1000.0)
    assert _lux_run(tr, [(1010.0, 320), (1020.0, 320)])["reason"] == "no_lux_rise"
    # 방 조명이 꺼짐(우리 상승폭보다 훨씬 크게 떨어짐) → 무시
    tr = _tracker()
    tr.on_lux(200, 995.0)
    _bright(tr, 1000.0)
    assert _lux_run(tr, [(1010.0, 320), (1020.0, 320), (1100.0, 15)])["reason"] == "room_light"
    # 잠깐 가렸다 돌아옴(30 s 못 버팀) → 신호 없음
    tr = _tracker()
    tr.on_lux(200, 995.0)
    _bright(tr, 1000.0)
    assert _lux_run(tr, [(1010.0, 320), (1020.0, 320), (1100.0, 150), (1110.0, 150), (1120.0, 310), (1150.0, 315)]) is None
    # 그 사이 허브가 무드등 명령을 다시 냈다 → 앞 밝힘의 하락으로 보지 않는다
    tr = _tracker()
    tr.on_lux(200, 995.0)
    _bright(tr, 1000.0)
    _lux_run(tr, [(1010.0, 320), (1020.0, 320)])
    tr.on_command({"command_id": "d", "target_id": "desk_lamp", "operation": "adjust_brightness",
                   "value": {"percent": -50}, "gate": "auto"}, 1030.0)
    assert _lux_run(tr, [(1040.0, 150), (1060.0, 150), (1080.0, 150)]) is None
    # 창 밖의 하락 → 무시
    tr = _tracker()
    tr.on_lux(200, 995.0)
    _bright(tr, 1000.0)
    _lux_run(tr, [(1010.0, 320), (1020.0, 320)])
    assert _lux_run(tr, [(1700.0, 150), (1720.0, 150), (1740.0, 150)]) is None


def _recorder(tmp_path, cfg=None):
    from deskmate_hub.ingest import load_ingest_config

    cfg = cfg or _cfg()
    policy = InterventionPolicy(cfg)
    rec = EpisodeRecorder(cfg, str(tmp_path / "p.jsonl"), boot_id="b1", ingest_cfg=load_ingest_config(),
                          on_log=lambda m: None, policy=policy, policy_path=str(tmp_path / "policy.json"))
    rec.session_id = "b1-s1"
    return rec, policy


def _open(rec, policy, sit, cause, gate="auto", ts=1000.0):
    from deskmate_hub.personalize.adapter import fsm_record  # noqa: F401
    d = policy.decide(sit, cause=cause, gate=gate, session_id="b1-s1", now=ts)
    eid = rec.logger.open_episode(session_id="b1-s1", situation=sit, fsm={"state": "ACTION_ENV", "cause": cause,
                                  "gate": gate, "retry": False, "context": "pc", "guards": []}, decision=d, ts=ts)
    rec._episodes[eid] = {"cause": cause, "gate": d.delivered_gate, "upright_seen": False}
    return eid, d


def test_recorder_manual_dim_updates_brighten_arm_and_logs(tmp_path):
    rec, policy = _recorder(tmp_path)
    eid, d = _open(rec, policy, _sit(lux=80, co2_ppm=None, posture_label="upright"), "cognitive")
    n_before = policy.models["light_cool_bright"]["accept"].n
    rec.implicit.on_command({"command_id": "c1", "target_id": "desk_lamp", "operation": "adjust_brightness",
                             "value": {"percent": 50}, "gate": "auto"}, 1000.0, episode_id=eid,
                            decision=rec.logger.decision_of(eid))
    sig = rec.on_manual_adjust({"kind": "manual_adjust", "target_id": "desk_lamp", "from": 165, "to": 70}, ts=1120.0)
    assert sig["kind"] == "manual_dim_app"
    assert policy.models["light_cool_bright"]["accept"].n == n_before + 1
    assert policy.stats["light_cool_bright"]["implicit"] == 1
    assert rec.logger._open[eid]["signals"][0]["kind"] == "manual_dim_app"
    assert rec.last_view["signal"]["text_ko"] == "밝힘 뒤 직접 어둡게 함 → 밝은 조명 덜 고름"
    rec._close(eid, status="measured", c_after=0.7, next_state="MONITOR")
    lines = read_records(rec.path)
    sig_line = next(r for r in lines if r["schema_name"] == "personalize_signal")
    assert sig_line["episode_id"] == eid and sig_line["policy_arms"] == ["light_cool_bright"] and sig_line["reward"] == -1.0
    ep = next(r for r in lines if r["schema_name"] == "personalize_episode")
    assert ep["signals"][0]["kind"] == "manual_dim_app"
    assert ep["feedback"] is None                                       # 자동이어도 auto_kept 로 적지 않는다
    assert json.loads((tmp_path / "policy.json").read_text(encoding="utf-8"))["stats"]["light_cool_bright"]["implicit"] == 1


def test_recorder_lux_signal_via_tick_path(tmp_path):
    rec, policy = _recorder(tmp_path)
    eid, d = _open(rec, policy, _sit(lux=200, posture_label="upright"), "cognitive")
    cache = SensorCache()

    def lux_at(t, lux):
        cache.put("env", Sample(received=t, ts=t, seq=None, data=dict(env(t).data, lux=lux)))
        rec._observe_signals(cache.snapshot(), t, "upright", 0.7)

    lux_at(995.0, 200)
    rec.implicit.on_command({"command_id": "c1", "target_id": "desk_lamp", "operation": "adjust_brightness",
                             "value": {"percent": 50}, "gate": "auto"}, 1000.0, episode_id=eid, decision=d)
    for t, lux in [(1010.0, 320), (1020.0, 320), (1100.0, 150), (1110.0, 150), (1120.0, 150), (1130.0, 150)]:
        lux_at(t, lux)
    assert policy.stats["light_cool_bright"]["implicit"] == 1
    assert rec.logger._open[eid]["signals"][0]["kind"] == "manual_dim_lux"
    assert "조도" in rec.last_signal["text_ko"]


# ── 3. 잘 깸 ────────────────────────────────────────────────────────

def _wake(**over):
    return WakeTracker(dict(load_personalize_config()["wake"], **over))


def test_wake_wants_only_posture_drowsy_routes():
    w = _wake()
    assert w.wants("posture", "face_down", False, 0.5) == "face_down"
    assert w.wants("posture", "upright", True, 0.5) == "drowsy"
    assert w.wants("posture", "upright", False, 0.8) == "c_fatigue"
    assert w.wants("posture", "upright", False, 0.5) is None
    assert w.wants("environment", "face_down", True, 0.9) is None


def test_wake_success_with_activity_and_with_fatigue_drop():
    w = _wake()
    w.start("e1", 1000.0, "face_down", 0.85)
    done = []
    for t, label, active in [(1010.0, "face_down", False), (1020.0, "upright", False), (1030.0, "upright", True),
                             (1040.0, "upright", False)]:
        done += w.observe(t, label, 0.84, active)
    (watch, reward), = done
    assert watch.result == "success" and reward == 1.0
    assert watch.detail["to_awake_sec"] == 20.0 and watch.detail["hold_sec"] == 20.0
    assert WakeTracker.text_ko(watch) == "깨우기 성공 (엎드림→바른 자세 20 s)"
    w.start("e2", 2000.0, "drowsy", 0.85)                               # 활동 없이 C_fatigue 하락
    done = []
    for t in (2010.0, 2020.0, 2030.0):
        done += w.observe(t, "lean_forward", 0.70, False)
    assert done and done[0][0].result == "success" and done[0][0].detail["fatigue_drop"] == pytest.approx(0.15)


def test_wake_failure_cases():
    w = _wake()
    w.start("e1", 1000.0, "face_down", 0.85)
    done = []
    t = 1000.0
    while not done:
        t += 10.0
        done = w.observe(t, "face_down", 0.85, False)
    assert done[0][0].result == "fail" and done[0][1] == -0.5 and t == 1120.0
    assert "실패" in WakeTracker.text_ko(done[0][0])
    # 바로 앉았지만 활동도 피로 하락도 없다 → 실패
    w.start("e2", 2000.0, "drowsy", 0.85)
    done = []
    for t in range(2010, 2131, 10):
        done += w.observe(float(t), "upright", 0.84, False)
    assert done[0][0].result == "fail"
    # 깼다가 다시 엎드림(20 s 못 버팀) → 실패
    w.start("e3", 3000.0, "face_down", 0.85)
    done = []
    for t, label in [(3010, "upright"), (3020, "face_down")] + [(3030 + 10 * i, "face_down") for i in range(10)]:
        done += w.observe(float(t), label, 0.84, True)
    assert done[0][0].result == "fail"


def test_recorder_wake_success_feeds_effect_axis_once(tmp_path):
    rec, policy = _recorder(tmp_path)
    eid, d = _open(rec, policy, _sit(posture_label="face_down", c_fatigue=0.85), "posture")
    rec.wake.start(eid, 1000.0, "face_down", 0.85)
    rec._episodes[eid]["wake"] = True
    cache = SensorCache()
    for t, label in [(1010.0, "face_down"), (1020.0, "upright"), (1030.0, "upright"), (1040.0, "upright")]:
        cache.put("keystroke", Sample(received=t, ts=t, seq=None, data={"typing_active": True, "input_active": True}))
        rec._observe_signals(cache.snapshot(), t, label, 0.84)
    st = policy.stats[d.arm]
    assert st["effect_n"] == 1 and st["effect_sum"] == 1.0
    assert rec.last_signal["text_ko"].startswith("깨우기 성공")
    rec._close(eid, status="measured", c_after=0.84, next_state="MONITOR")
    assert policy.stats[d.arm]["effect_n"] == 1                           # 닫을 때 다시 갱신하지 않는다
    ep = next(r for r in read_records(rec.path) if r["schema_name"] == "personalize_episode")
    assert ep["outcome"]["wake"]["result"] == "success" and ep["outcome"]["reward_effect"] == 1.0
    assert ep["outcome"]["wake"]["trigger"] == "face_down"


def test_recorder_wake_blend_mode_mixes_at_close(tmp_path):
    cfg = _cfg()
    cfg["wake"] = dict(cfg["wake"], effect_mode="blend", blend=0.5)
    rec, policy = _recorder(tmp_path, cfg)
    eid, d = _open(rec, policy, _sit(posture_label="face_down", c_fatigue=0.85), "posture")
    rec.wake.start(eid, 1000.0, "face_down", 0.85)
    rec._episodes[eid]["wake"] = True
    t = 1000.0
    while rec.wake.watches:
        t += 10.0
        rec._observe_signals(SensorCache().snapshot(), t, "face_down", 0.85)
    assert policy.stats[d.arm]["effect_n"] == 0                           # blend 는 닫을 때
    rec._close(eid, status="measured", c_after=0.85, next_state="MONITOR")
    ep = next(r for r in read_records(rec.path) if r["schema_name"] == "personalize_episode")
    assert ep["outcome"]["reward_effect"] == pytest.approx(0.5 * 0.0 + 0.5 * -0.5)
    assert policy.stats[d.arm]["effect_n"] == 1


# ── LiveHub 전체 경로: 졸음 → 무드등 밝힘(FSM 기본) → 앱에서 직접 어둡게 → 음의 보상 ─────────────

LAMP_CFG = {"enabled": True, "adapter": "mock", "result_timeout_sec": 15, "suggest_timeout_sec": 180,
            "undo_window_sec": 60, "cooldown_sec": 300, "irreversible_operations": ["set_power_off"],
            "actions": {"environment": [{"target_id": "vent_fan", "operation": "set_power", "value": "on",
                                         "undo_value": "off"}],
                        "posture": [{"target_id": "desk_lamp", "operation": "set_brightness", "value": 230,
                                     "undo_value": 110}]}}


def test_live_hub_manual_adjust_after_posture_lamp(tmp_path):
    from deskmate_hub.inference import load_config
    from deskmate_hub.ingest import load_ingest_config

    cache, states = SensorCache(), []
    hub = LiveHub(cache, fsm_cfg=copy.deepcopy(load_config()), ingest_cfg=dict(load_ingest_config(), normalization="linear"),
                  control_cfg=LAMP_CFG, publish=states.append, out=io.StringIO(), profile="",
                  personalize_cfg=_cfg(log_dir=str(tmp_path)), personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"))
    period, baseline = hub.period, float(hub.fsm_cfg["timers"]["baseline_sec"])
    t = 0.0
    while t <= baseline + 2 * period:
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += period
    lamp_ts = None
    for _ in range(int(3600 / period)):
        cache.put("mmwave", mm(t, level=5)); cache.put("env", env(t, co2=2000))
        cache.put("tof_service", svc(t, posture="drowsy", conf=0.92))
        hub.tick_once(t)
        if hub.engine.state is State.ACTION_POSTURE and hub.control.episode and hub.control.episode.awaiting_user:
            cache.put_feedback({"verdict": "accept"})
        if lamp_ts is None and hub.mock_plug.state.get("desk_lamp"):
            lamp_ts = t
        t += period
        if lamp_ts is not None and hub.engine.state is State.MONITOR:
            break
    assert lamp_ts is not None and hub.personalize_recorder.implicit.last is not None
    assert hub.personalize_recorder.implicit.last.episode_id is not None
    route_mqtt_message(cache, "deskmate/feedback/user", _envelope(
        {"kind": "manual_adjust", "target_id": "desk_lamp", "from": 230, "to": 90, "input": "app"}, ts=t), t)
    hub.tick_once(t)
    pol = hub.personalize_policy
    assert pol.stats["light_cool_bright"]["implicit"] == 1
    assert "[esm]" not in hub.out.getvalue()                             # ESM 라벨·verdict 경로로 가지 않는다
    sig = states[-1]["data"]["sensor_summary"]["personalize"]["signal"]
    assert sig["kind"] == "manual_dim_app" and "직접 어둡게" in sig["text_ko"]
    lines = read_records(str(tmp_path / "p.jsonl"))
    assert any(r["schema_name"] == "personalize_signal" and r["kind"] == "manual_dim_app" for r in lines)


# ── 보드 조건 ───────────────────────────────────────────────────────

def test_board_runtime_profile_and_signals_without_numpy_or_random():
    code = textwrap.dedent("""
        import sys
        for name in ("_socket", "_random", "_ssl", "numpy", "statistics"):
            sys.modules[name] = None
        sys.modules.pop("random", None)
        from deskmate_hub.personalize import (ImplicitDimTracker, InterventionPolicy, WakeTracker,
                                              load_personalize_config, parse_profile)
        cfg = load_personalize_config()
        p = InterventionPolicy(cfg)
        notes = p.apply_user_profile(parse_profile({"light_pref": "low", "sound_pref": "off", "bedtime": "00:30",
                                                    "wake_time": "07:30"}))
        assert p.disabled == {"sound_cue", "posture_voice"} and notes
        tr = ImplicitDimTracker(cfg["implicit"])
        tr.on_command({"command_id": "c", "target_id": "desk_lamp", "operation": "adjust_brightness",
                       "value": {"percent": 50}}, 1.0)
        assert tr.on_manual_adjust({"target_id": "desk_lamp", "from": 150, "to": 50}, 30.0)["kind"] == "manual_dim_app"
        w = WakeTracker(cfg["wake"]); w.start("e", 0.0, "face_down", 0.9)
        out = [w.observe(float(t), "upright", 0.9, True) for t in (10, 20, 30)]
        assert any(out)
        print("ok")
    """)
    result = subprocess.run([sys.executable, "-c", code], cwd=HUB, capture_output=True, text=True)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr[-2000:]
