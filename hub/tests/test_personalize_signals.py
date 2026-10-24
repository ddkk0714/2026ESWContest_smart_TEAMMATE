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
    assert p["focus_help"] == ["break", "light"]                      # 모르는 항목 버림, 보낸 순서 유지
    assert p["light_pref"] == "low" and p["sound_pref"] == "off" and p["sleep_hours"] == 5.5
    bad = parse_profile({"profile_id": "a b/c", "light_pref": "bright", "bedtime": "25:00", "sleep_hours": "8"})
    assert bad is None                                                # 틀린 값만 있으면 아무것도 안 남는다
    assert parse_profile({"light_pref": "high", "bedtime": "7:5"}) == {"light_pref": "high"}
    assert parse_profile("nope") is None


def test_focus_help_aliases_keep_order_and_any_node():
    p = parse_profile({"focus_help": ["notify", "lamp", "break", "fan", "light", "music", 3, "voice"]})
    assert p["focus_help"] == ["sound", "light", "break", "wind", "voice"]      # 별칭 → 표준, 중복·모르는 값 버림, 순서 유지
    cache = SensorCache()                                                       # 제품 UI node "pi5-ui" 도 거르지 않는다
    body = json.dumps({"ts": 1.0, "node": "pi5-ui", "seq": 1, "data": {"profile_id": "v18", "focus_help": ["lamp"]}})
    assert route_mqtt_message(cache, "deskmate/profile/user", body.encode(), 1.0) == "user_profile"
    fb = json.dumps({"ts": 1.0, "node": "pi5-ui", "data": {"kind": "manual_adjust", "target_id": "desk_lamp", "from": 9, "to": 3}})
    assert route_mqtt_message(cache, "deskmate/feedback/user", fb.encode(), 1.0) == "feedback"
    assert parse_profile(cache.user_profile.data)["focus_help"] == ["light"]


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
    d = p.decide(_sit(co2_ppm=1500), cause="cognitive", gate="suggest", session_id="s1")
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
    assert d.candidates == ["posture_card", "wake_light"] and d.arm in d.candidates
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

    # 다른 profile_id → 그 사람 폴더로 전환(policy.json·user_profile.json), 지금 사용자는 active_profile.json
    assert hub.active_profile_id == "u-7f3a" and states[-1]["data"]["sensor_summary"]["personalize"]["active_profile_id"] == "u-7f3a"
    assert rec.policy_path == str(tmp_path / "u-7f3a" / "policy.json") and (tmp_path / "u-7f3a" / "policy.json").exists()
    assert json.loads((tmp_path / "active_profile.json").read_text(encoding="utf-8"))["profile_id"] == "u-7f3a"
    path = tmp_path / "u-7f3a" / "user_profile.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["profile"]["profile_id"] == "u-7f3a"
    assert "age_band" not in stored["profile"] and "홍길동" not in json.dumps(stored, ensure_ascii=False)
    assert not (tmp_path / "u-7f3a" / "user_profile.json.tmp").exists()
    b_saved = list(hub.personalize_policy.models["light_cool_bright"]["accept"].b)

    # 재시작: 브로커에 retain 이 없어도 파일에서 다시 반영 — prior 를 두 번 더하지 않는다
    hub2, cache2, states2 = _live(tmp_path)
    p2 = hub2.personalize_policy
    assert hub2.active_profile_id == "u-7f3a" and hub2.personalize_recorder.logger.profile_id == "u-7f3a"
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
    assert not (tmp_path / "prof" / "user_profile.json").exists() and not (tmp_path / "u-7f3a").exists()


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
    assert pol.stats["wake_light"]["implicit"] == 1                       # 자세(깨우기) 밝힘 → wake_light 가 배운다
    assert pol.stats["light_cool_bright"]["implicit"] == 0
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


# ── 깨우기 무드등 arm (wake_light) ─────────────────────────────────

def test_arm_commands_pass_value_dict_and_undo_through():
    from deskmate_hub.personalize.adapter import arm_commands

    cfg = load_personalize_config()
    wake = next(a for a in cfg["arms"] if a["id"] == "wake_light")
    (spec,) = arm_commands(wake["device"])
    assert spec == {"target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 60, "min_raw": 153},
                    "undo_value": None, "undo": "previous"}
    assert spec["value"] is not wake["device"]["value"]                   # 복사본(설정 dict 를 바꾸지 않게)
    assert "wake_light" in cfg["implicit"]["brighten_arms"] and wake["causes"] == ["posture"]


def test_profile_mapping_moves_wake_light():
    p = InterventionPolicy(_cfg())
    base = _mean(p, "wake_light")
    p.apply_user_profile({"light_pref": "low"})
    assert _mean(p, "wake_light") == pytest.approx(base - 0.3)
    p.apply_user_profile({"light_pref": "high", "focus_help": ["light"]})
    assert _mean(p, "wake_light") == pytest.approx(base + 0.4)


def _drive_drowsy(hub, cache, *, until):
    period, baseline = hub.period, float(hub.fsm_cfg["timers"]["baseline_sec"])
    t = 0.0
    while t <= baseline + 2 * period:
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += period
    for _ in range(int(3600 / period)):
        cache.put("mmwave", mm(t, level=5)); cache.put("env", env(t, co2=2000))
        cache.put("tof_service", svc(t, posture="drowsy", conf=0.92))
        hub.tick_once(t)
        if hub.engine.state is State.ACTION_POSTURE and hub.control.episode and hub.control.episode.awaiting_user:
            cache.put_feedback({"verdict": "accept"})
        t += period
        if until(hub):
            break
    return t


def test_live_hub_drowsy_picks_wake_light_and_manual_dim_lowers_it(tmp_path):
    from deskmate_hub.control import load_control_config
    from deskmate_hub.inference import load_config
    from deskmate_hub.ingest import load_ingest_config

    cfg = _cfg(log_dir=str(tmp_path))
    cfg["bandit"] = dict(cfg["bandit"], algorithm="greedy")                # 평균만 — 결정적
    cfg["prior"] = dict(cfg["prior"], wake_light={"accept": {"bias": 2.0}, "effect": {"bias": 2.0}})
    cache, states = SensorCache(), []
    hub = LiveHub(cache, fsm_cfg=copy.deepcopy(load_config()), ingest_cfg=dict(load_ingest_config(), normalization="linear"),
                  control_cfg=dict(load_control_config(), adapter="mock"), publish=states.append, out=io.StringIO(),
                  profile="", personalize_cfg=cfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "default" / "policy.json"))
    t = _drive_drowsy(hub, cache, until=lambda h: bool(h.mock_plug.state.get("desk_lamp")))
    ep = hub.control.episode or hub.control.history[-1]
    cmd = ep.commands[0]
    assert ep.state == "ACTION_POSTURE" and (cmd.target_id, cmd.operation) == ("desk_lamp", "adjust_brightness")
    assert cmd.value == {"percent": 60, "min_raw": 153} and cmd.undo_mode == "previous"
    view = next(s["data"]["sensor_summary"]["personalize"] for s in reversed(states)
                if (s["data"]["sensor_summary"].get("personalize") or {}).get("arm"))
    assert view["arm"] == "wake_light" and view["arm_label"] == "무드등 밝혀 깨우기"
    hub.tick_once(t)                                                        # 모의 램프 결과(previous·actual) 반영
    t += hub.period
    ev = hub.personalize_recorder.implicit.last
    assert ev.brighten and ev.previous is not None and ev.actual >= 153
    pol = hub.personalize_policy
    before = _mean(pol, "wake_light")
    route_mqtt_message(cache, "deskmate/feedback/user", _envelope(
        {"kind": "manual_adjust", "target_id": "desk_lamp", "from": int(ev.actual), "to": 40}, ts=t), t)
    hub.tick_once(t)
    assert pol.stats["wake_light"]["implicit"] == 1 and _mean(pol, "wake_light") < before


# ── 사람별 정책 ────────────────────────────────────────────────────

def test_profile_switch_keeps_separate_policies_and_resumes_after_restart(tmp_path):
    hub, cache, states = _live(tmp_path)
    assert hub.active_profile_id == "prof"                                # 메시지 전 = 설정(정책 경로) 기본 사용자
    first = hub.personalize_policy
    route_mqtt_message(cache, "deskmate/profile/user", _envelope({"profile_id": "alice", "light_pref": "low"}), 1000.0)
    hub.tick_once(1000.0)
    alice = hub.personalize_policy
    assert hub.active_profile_id == "alice" and alice is not first
    d = alice.decide(_sit(), cause="cognitive", gate="suggest", session_id="x")
    alice.feedback(d, "accept")
    hub.personalize_recorder._save_policy()
    assert alice.n_feedback == 1

    route_mqtt_message(cache, "deskmate/profile/user", _envelope({"profile_id": "bob", "light_pref": "high"}, ts=1100.0),
                       1100.0)
    hub.tick_once(1100.0)
    bob = hub.personalize_policy
    assert hub.active_profile_id == "bob" and bob.n_feedback == 0         # 새 사람 = 전역 prior + 그 프로필
    assert _mean(bob, "light_cool_bright") > _mean(alice, "light_cool_bright")
    assert (tmp_path / "alice" / "policy.json").exists() and (tmp_path / "bob" / "user_profile.json").exists()
    assert states[-1]["data"]["sensor_summary"]["personalize"]["active_profile_id"] == "bob"

    route_mqtt_message(cache, "deskmate/profile/user", _envelope({"profile_id": "alice", "light_pref": "low"}, ts=1200.0),
                       1200.0)
    hub.tick_once(1200.0)
    back = hub.personalize_policy
    assert hub.active_profile_id == "alice" and back.n_feedback == 1      # alice 의 학습을 다시 불러온다
    assert _mean(back, "light_cool_bright") == pytest.approx(_mean(alice, "light_cool_bright"))

    hub2, _, _ = _live(tmp_path)                                          # 재시작 → 마지막 사용자(alice)로
    assert hub2.active_profile_id == "alice" and hub2.personalize_policy.n_feedback == 1
    assert hub2.personalize_recorder.user_profile == {"profile_id": "alice", "light_pref": "low"}
    assert hub2.personalize_recorder.logger.profile_id == "alice"


def test_profile_switch_closes_open_episodes_with_previous_policy(tmp_path):
    rec, policy = _recorder(tmp_path / "default")
    eid, d = _open(rec, policy, _sit(), "cognitive", gate="suggest")
    rec._apply_profile({"profile_id": "carol"}, now=2000.0, save=True)
    assert rec.active_profile_id == "carol" and rec.policy is not policy and not rec._episodes
    ep = next(r for r in read_records(rec.path) if r["schema_name"] == "personalize_episode")
    assert ep["episode_id"] == eid and ep["outcome"]["status"] == "profile_switched" and ep["profile_id"] == "default"
    assert rec.policy_path == str(tmp_path / "carol" / "policy.json")


def test_profile_without_id_keeps_active_profile(tmp_path):
    hub, cache, _ = _live(tmp_path)
    route_mqtt_message(cache, "deskmate/profile/user", _envelope({"light_pref": "low"}), 1000.0)
    hub.tick_once(1000.0)
    assert hub.active_profile_id == "prof" and (tmp_path / "prof" / "user_profile.json").exists()
    assert not (tmp_path / "active_profile.json").exists()


def test_reaction_learner_follows_active_profile(tmp_path):
    from deskmate_hub.learning import load_learning_config

    lcfg = dict(load_learning_config(), enabled=True, state_path=str(tmp_path / "{profile_id}" / "reaction.json"))
    hub, cache, _ = _live(tmp_path, learning_cfg=lcfg)
    assert hub.active_profile_id == "prof" and hub.profile_id == "default"   # 메시지 전: 각자 설정 기본값(경로 폴더·learning.yaml)
    hub.tick_once(990.0)
    assert hub.profile_id == "default"                                    # 사람이 안 바뀌었으면 학습기도 그대로
    route_mqtt_message(cache, "deskmate/profile/user", _envelope({"profile_id": "dana", "sound_pref": "low"}), 1000.0)
    hub.tick_once(1000.0)
    assert hub.active_profile_id == hub.profile_id == "dana"
    assert hub.learner.state_path == str(tmp_path / "dana" / "reaction.json")
    assert hub.engine.score_adjust == hub.learner.adjust
    hub2, _, _ = _live(tmp_path, learning_cfg=lcfg)                       # 재시작: 정책·반응 학습 모두 dana 로
    assert hub2.active_profile_id == hub2.profile_id == "dana"
    assert hub2.learner.state_path == str(tmp_path / "dana" / "reaction.json")
