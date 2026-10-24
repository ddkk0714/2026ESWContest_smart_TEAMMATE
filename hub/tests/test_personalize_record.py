"""개인화 로그 기록 전용 모드 — live 가 결정·피드백·10 분 뒤 결과·세션 요약을 JSONL 로 남긴다 (정책은 꺼진 채)."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from deskmate_hub.inference import State, load_config
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.inference.types import Context, GateMode, Scores, SensorFrame, TickResult
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import EpisodeRecorder, load_personalize_config, read_records
from deskmate_hub.personalize.record import SessionStats, fallback_decision

HUB_DIR = Path(__file__).resolve().parents[1]


def _hub(tmp_path, **kw):
    # 이 파일은 기록 전용 경로를 본다 — Pi 4 배포판은 정책을 켜 두므로(enabled: true) 여기서 끈다
    kw.setdefault("personalize_cfg", dict(load_personalize_config(), enabled=False))
    cache = SensorCache()
    states, requests = [], []
    hub = LiveHub(cache, publish=states.append, publish_request=requests.append,
                  control_cfg={"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")},
                  personalize_log_path=str(tmp_path / "personalize.jsonl"),
                  out=io.StringIO(), **kw)
    return hub, cache, states, requests


def _tick(hub, cache, now, *, fatigued=False, rising=False, present=True):
    messages = {
        "mmwave/node": {"present": present, "motion_level": 5 if fatigued else 40,
                        "drowsy_state": "DROWSY" if fatigued else "AWAKE",
                        "resp_bpm": 15, "resp_valid": True, "valid": True},
        "env/node": {"co2_ppm": 850 if rising else 800, "co2_valid": True, "temp_c": 24,
                     "temp_valid": True, "humidity_pct": 45, "humidity_valid": True,
                     "lux": 300, "lux_valid": True},
        "keystroke": {"typing_active": present, "input_active": present,
                      "idle_ratio": .1, "flight_cv": .35 if fatigued else .25,
                      "correction_rate": .01, "dwell_mean_ms": 90,
                      "flight_mean_ms": 140, "window_s": 60},
    }
    for topic, data in messages.items():
        payload = json.dumps({"schema_version": "1.0", "ts": now, "data": data}).encode()
        assert route_mqtt_message(cache, "deskmate/sensor/" + topic, payload, now)
    return hub.tick_once(now)


def _drive_to_suggest(hub, cache, period):
    now = 1000.0
    while hub.engine.state is not State.FOCUS_PC:
        _tick(hub, cache, now)
        now += period
        assert now < 1400
    for _ in range(100):
        _tick(hub, cache, now, fatigued=True)
        now += period
        if hub.pending_request is not None:
            break
    assert hub.engine.state is State.ACTION_BREAK and hub.pending_request is not None
    return now


def test_record_flag_default_on_and_recorder_present(tmp_path):
    hub, *_ = _hub(tmp_path)
    assert load_personalize_config()["record"] is True
    assert load_personalize_config()["enabled"] is True            # Pi 4 배포판(feat/pi4-hub-1023)은 정책을 켠다
    assert isinstance(hub.personalize_recorder, EpisodeRecorder)
    assert hub.personalize_recorder.path == str(tmp_path / "personalize.jsonl")


def test_record_off_means_no_recorder(tmp_path):
    cfg = dict(load_personalize_config(), record=False, enabled=False)
    hub, *_ = _hub(tmp_path, personalize_cfg=cfg)
    assert hub.personalize_recorder is None


@pytest.mark.parametrize("verdict", ["accept", "reject"])
def test_episode_line_after_feedback_and_ten_minutes(tmp_path, verdict):
    hub, cache, states, requests = _hub(tmp_path)
    cfg = load_config()
    period = cfg["timers"]["score_period_sec"]
    now = _drive_to_suggest(hub, cache, period)
    request_id = requests[-1]["data"]["request_id"]
    decided_at = hub.personalize_recorder.logger._open[hub.personalize_recorder.last_episode_id or next(iter(hub.personalize_recorder.logger._open))]["ts_ms"] / 1000.0
    assert not read_records(hub.personalize_recorder.path), "피드백·10 분 전에는 아직 한 줄도 쓰지 않는다"

    route_mqtt_message(cache, "deskmate/feedback/user", json.dumps({
        "schema_version": "1.0", "data": {"request_id": request_id, "verdict": verdict, "response_ms": 30000},
    }).encode(), now)
    _tick(hub, cache, now, fatigued=True)
    now += period
    assert not read_records(hub.personalize_recorder.path)

    window = float(load_personalize_config()["bandit"]["effect_window_min"]) * 60.0
    while now < decided_at + window + period:
        _tick(hub, cache, now)
        now += period
    records = read_records(hub.personalize_recorder.path)
    assert len(records) == 1
    rec = records[0]
    assert rec["schema_name"] == "personalize_episode" and rec["simulated"] is False
    assert rec["session_id"].endswith("-s1") and rec["profile_id"] == "default"
    assert rec["fsm"] == {"state": "ACTION_BREAK", "cause": "cognitive", "gate": "suggest", "retry": False,
                          "context": "pc", "guards": []}
    assert rec["decision"]["source"] == "fallback" and rec["decision"]["arm"] is None
    assert rec["decision"]["delivered_gate"] == "suggest" and "take_a_break" in rec["decision"]["explanation"]
    assert rec["feedback"]["verdict"] == verdict and rec["feedback"]["request_id"] == request_id
    assert rec["feedback"]["response_ms"] == 30000
    assert rec["outcome"]["status"] == "measured" and rec["outcome"]["c_fatigue_after"] is not None
    assert rec["outcome"]["reward_accept"] == (1.0 if verdict == "accept" else -1.0)
    assert rec["outcome"]["posture_recovered"] is None  # 인지성 원인
    f = rec["features"]
    assert f["co2_ppm"] == 800 and f["lux"] == 300 and f["typing_active"] is True
    assert f["drowsy"] is True and f["drowsy_count_30min"] >= 1
    assert f["session_elapsed_min"] > 0 and f["since_last_intervention_min"] is None
    assert "posture_dist" in f["missing"] and "weather" in f["missing"]  # ToF·날씨 없는 시험
    assert 0 <= f["hour_local"] < 24 and f["season"] in ("spring", "summer", "autumn", "winter")


def test_timeout_from_hub_expiry_is_recorded(tmp_path):
    hub, cache, states, requests = _hub(tmp_path)
    period = load_config()["timers"]["score_period_sec"]
    now = _drive_to_suggest(hub, cache, period)
    expiry = requests[-1]["ts"] + requests[-1]["data"]["expires_in_s"]
    _tick(hub, cache, expiry + 1, fatigued=True)
    assert hub.pending_request is None
    eid = next(iter(hub.personalize_recorder.logger._open))
    assert hub.personalize_recorder.logger._open[eid]["feedback"]["verdict"] == "timeout"


def test_session_end_flushes_episode_and_writes_summary(tmp_path):
    hub, cache, states, requests = _hub(tmp_path)
    period = load_config()["timers"]["score_period_sec"]
    now = _drive_to_suggest(hub, cache, period)
    # 사람이 자리를 비우면 세션이 끝난다 — 열린 에피소드는 session_ended 로, 세션 요약 한 줄 추가
    for _ in range(400):
        _tick(hub, cache, now, present=False)
        now += period
        if hub.engine.state in (State.END, State.IDLE):
            break
    assert hub.engine.state in (State.END, State.IDLE)
    records = read_records(hub.personalize_recorder.path)
    assert [r["schema_name"] for r in records] == ["personalize_episode", "personalize_session"]
    ep, ses = records
    # 자리비움 → IDLE 까지 10 분 = 효과 창 10 분이라 먼저 measured 로 닫힐 수 있다. 어느 쪽이든 세션 요약 전에 닫혀 있어야 한다
    assert ep["outcome"]["status"] in ("measured", "session_ended")
    if ep["outcome"]["status"] == "session_ended":
        assert ep["outcome"]["c_fatigue_after"] is None and ep["outcome"]["next_state"] in ("END", "IDLE")
    assert ses["session_id"] == ep["session_id"] and ses["length_min"] > 0
    assert ses["fatigue_by_hour"] and all(n > 0 for _, n in ses["fatigue_by_hour"].values())
    assert ses["slump_count"] == 0 and ses["first_slump_min"] is None  # ToF 없는 시험
    assert hub.personalize_recorder.session_id is None and hub.personalize_recorder.stats is None


def test_session_stats_slump_rest_recovery_and_co2():
    st = SessionStats(session_id="s", started_ts=0.0, start_hour_local=14.0,
                      slump_labels=("recline", "face_down", "chin_rest", "drowsy"))
    t = 0.0
    for state, label, co2, flags in [
        ("FOCUS_PC", "upright", 800, ()), ("FOCUS_PC", "recline", 800, ()),   # 슬럼프 시작 t=10
        ("FOCUS_PC", "recline", 900, ()), ("FATIGUE", "recline", 1300, ("co2_high",)),
    ]:
        st.observe(t, 14.0 + t / 3600, state, 0.5, label, co2, flags)
        t += 10.0
    assert st.slump_count == 0  # 아직 2 분 안 됨
    for _ in range(12):
        st.observe(t, 14.0, "FATIGUE", 0.8, "recline", 1300, ("co2_high",))
        t += 10.0
    assert st.slump_count == 1 and st.first_slump_min == pytest.approx(10 / 60, abs=0.01)
    assert st.co2_at_fatigue == [1300.0]
    st.observe(t, 14.0, "ACTION_POSTURE", 0.8, "recline", 1300, ()); t += 10
    st.observe(t, 14.0, "ACTION_POSTURE", 0.8, "recline", 1300, ()); t += 10
    st.observe(t, 14.0, "RECOVERY", 0.6, "upright", 1300, ()); t += 10
    assert st.recovery_after_alert_min == [pytest.approx(20 / 60, abs=0.01)]
    assert st.slump_count == 1  # 같은 구간은 두 번 세지 않는다
    st.observe(t, 15.0, "REST", 0.4, "upright", 900, ()); t += 600
    st.observe(t, 15.0, "FOCUS_PC", 0.4, "upright", 900, ()); t += 600
    st.observe(t, 15.0, "REST", 0.4, "upright", 900, ()); t += 10
    assert st.break_interval_min == [20.0]
    rec = st.record(t, 15.2, 9)
    assert rec["start_hour_local"] == 14.0 and rec["end_hour_local"] == 15.2
    assert rec["fatigue_by_hour"]["14"][1] == 19 and rec["fatigue_by_hour"]["15"][1] == 3


def test_fallback_decision_shape_matches_log_schema():
    d = fallback_decision(GateMode.AUTO, fsm_action="adjust_environment")
    assert set(d) == {"arm", "candidates", "excluded", "source", "policy_mode", "n_personal", "scores",
                      "delivered_gate", "auto_threshold", "explanation", "ventilate_minutes"}
    assert d["arm"] is None and d["source"] == "fallback" and d["delivered_gate"] == "auto"


def test_auto_episode_without_feedback_closes_as_auto_kept(tmp_path):
    """자동 게이트 에피소드는 피드백이 없으면 auto_kept, 되돌리기(auto_undo)는 undo 로."""
    cfg = load_personalize_config()
    rec = EpisodeRecorder(cfg, str(tmp_path / "p.jsonl"), boot_id="b1", ingest_cfg=load_ingest_config())

    class View:
        weather = None

        def fresh(self, kind, now, max_age):
            return None

    def result(state, gate=GateMode.NONE, actions=()):
        return TickResult(state=state, context=Context.PC, scores=Scores(c_focus=0.3, c_fatigue=0.8,
                          focus_weights={}, fatigue_weights={}), actions=list(actions), gate=gate, cause="environment")

    def frame(now):
        return SensorFrame(now=now, present=True, pc_ratio=0.9)

    view = View()
    rec.on_tick(frame(0.0), result(State.START), State.IDLE, view, 0.0)
    assert rec.session_id == "b1-s1"
    eid = rec.on_tick(frame(60.0), result(State.ACTION_ENV, GateMode.AUTO, ["route:environment"]),
                      State.CAUSE_ANALYSIS, view, 60.0, fsm_action="adjust_environment")
    assert eid == "b1-pe1"
    assert rec.on_tick(frame(70.0), result(State.ACTION_ENV, GateMode.AUTO), State.ACTION_ENV, view, 70.0) is None
    # 되돌리기 → undo
    assert rec.on_feedback("reject", ts=80.0, kind="auto_undo") == eid
    rec.on_tick(frame(660.0), result(State.MONITOR), State.ACTION_ENV, view, 60.0 + 600.0)
    lines = read_records(rec.path)
    assert lines[0]["feedback"]["verdict"] == "undo" and lines[0]["outcome"]["reward_accept"] == -2.0
    assert lines[0]["outcome"]["status"] == "measured" and lines[0]["outcome"]["next_state"] == "MONITOR"
    # 두 번째 auto 에피소드: 피드백 없음 → auto_kept
    eid2 = rec.on_tick(frame(700.0), result(State.ACTION_ENV, GateMode.AUTO, ["route:environment"]),
                       State.CAUSE_ANALYSIS, view, 700.0)
    assert lines[0]["features"]["since_last_intervention_min"] is None
    assert lines[0]["features"]["session_elapsed_min"] == pytest.approx(1.0)
    rec.on_tick(frame(1400.0), result(State.MONITOR), State.ACTION_ENV, view, 1400.0)
    lines = read_records(rec.path)
    assert lines[1]["episode_id"] == eid2 and lines[1]["feedback"]["verdict"] == "auto_kept"
    assert lines[1]["features"]["since_last_intervention_min"] == pytest.approx((700 - 60) / 60, abs=0.01)
    # gate none 은 에피소드를 만들지 않는다
    assert rec.on_tick(frame(1500.0), result(State.ACTION_ENV, GateMode.NONE, ["route:environment"]),
                       State.CAUSE_ANALYSIS, view, 1500.0) is None
    rec.close_all(1600.0)
    assert read_records(rec.path)[-1]["schema_name"] == "personalize_session"


def test_live_imports_without_numpy():
    """Pi 4 제한 Python 에는 numpy 가 없다 — 정책까지(2026-10-23 순수 Python 이식) numpy 없이 import 돼야 한다."""
    code = ("import sys; sys.modules['numpy'] = None; "
            "import deskmate_hub.live, deskmate_hub.personalize.record; "
            "import deskmate_hub.personalize as p; print(p.InterventionPolicy is None)")
    out = subprocess.run([sys.executable, "-c", code], cwd=HUB_DIR, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"            # 정책이 보드에서도 있다


def test_arm_commands_translate_device_specs():
    from deskmate_hub.personalize.adapter import arm_commands

    assert arm_commands(None) == []
    lamp = {"target_id": "desk_lamp", "color_temperature": 1, "brightness": 200,
            "undo": {"color_temperature": 2, "brightness": 110}}
    assert arm_commands(lamp) == [{"target_id": "desk_lamp", "operation": "set_brightness", "value": 200, "undo_value": 110}]
    fan = {"target_id": "vent_fan", "operation": "set_power", "value": "on", "undo_value": "off"}
    assert arm_commands(fan) == [dict(fan)]


def test_dispatcher_uses_policy_specs_for_any_action_state():
    from deskmate_hub.control import ControlDispatcher
    from deskmate_hub.control.dispatcher import GateMode as G

    sent = []
    d = ControlDispatcher({"enabled": True, "actions": {"environment": [
        {"target_id": "vent_fan", "operation": "set_power", "value": "on", "undo_value": "off"}]}},
        publish_cmd=sent.append, clock=lambda: 0.0)
    ep = d.on_enter_action("ACTION_BREAK", "cognitive", G.AUTO, 0.0,
                           specs=[{"target_id": "desk_lamp", "operation": "set_brightness", "value": 200, "undo_value": 110}])
    assert ep.executed and [(c["target_id"], c["value"]) for c in sent] == [("desk_lamp", 200)]
    d.close_episode()
    ep2 = d.on_enter_action("ACTION_BREAK", "cognitive", G.AUTO, 10.0)      # specs 없으면 ENV 외 상태는 명령 없음
    assert ep2.outcome == "skipped"
