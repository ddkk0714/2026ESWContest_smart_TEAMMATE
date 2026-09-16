"""ingest: 센서 표본 → SensorFrame 매핑과 라이브 루프. 브로커·스레드 없이 검증한다."""
from __future__ import annotations

import io
import json

import pytest

from deskmate_hub.ingest import (
    Sample, SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary,
)
from deskmate_hub.ingest.protocol import parse_sensor_message
from deskmate_hub.inference import State
from deskmate_hub.live import LiveHub
from deskmate_hub.replay import iter_frames


@pytest.fixture
def cfg():
    return load_ingest_config()


def mm(now, *, present=True, level=5, drowsy="AWAKE", resp_valid=True, seq=None):
    return Sample(received=now, ts=now, seq=seq, data={
        "present": present, "motion_state": "still" if level <= 10 else "active",
        "motion_level": level, "distance_cm": 55, "resp_bpm": 15, "resp_valid": resp_valid,
        "heart_bpm": None, "heart_valid": False, "drowsy_state": drowsy, "valid": True,
    })


def ks(now, *, typing=True, idle=0.2, cv=0.3, corr=0.05, input_active=True):
    return Sample(received=now, ts=now, seq=None, data={
        "window_s": 60, "dwell_mean_ms": 90, "dwell_std_ms": 20, "flight_mean_ms": 150, "flight_std_ms": 45,
        "idle_ratio": idle, "correction_rate": corr, "typing_active": typing, "mouse_active": input_active,
        "input_active": input_active, "flight_cv": cv, "mouse_event_rate": 30.0,
    })


def env(now, *, co2=700, valid=True):
    return Sample(received=now, ts=now, seq=None, data={
        "co2_ppm": co2, "temp_c": 25.0, "humidity_pct": 45.0, "lux": 300,
        "co2_valid": valid, "temp_valid": True, "humidity_valid": True, "lux_valid": True,
    })


# ── 매핑 ────────────────────────────────────────────────────────────

def test_no_samples_everything_unavailable(cfg):
    cache = SensorCache()
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.IDLE)
    assert f.present is False and f.touch is False
    assert all(not s.available for s in f.signals.values())


def test_presence_rising_edge_starts_session_only_in_idle(cfg):
    cache = SensorCache()
    tr = SessionTracker()
    cache.put("mmwave", mm(100.0))
    f = build_frame(cache.snapshot(), 100.0, cfg, tr, fsm_state=State.IDLE)
    assert f.present and f.touch is True
    f2 = build_frame(cache.snapshot(), 101.0, cfg, tr, fsm_state=State.IDLE)
    assert f2.touch is False                      # 엣지는 한 번만
    cache.put("mmwave", mm(200.0, present=False))
    cache.put("mmwave", mm(201.0, present=True))
    f3 = build_frame(cache.snapshot(), 201.0, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f3.touch is False                      # IDLE 이 아니면 세션 시작 이벤트 아님


def test_stale_sample_becomes_unavailable(cfg):
    cache = SensorCache()
    cache.put("mmwave", mm(100.0))
    f = build_frame(cache.snapshot(), 100.0 + cfg["freshness_sec"]["mmwave"] + 1, cfg, SessionTracker(),
                    fsm_state=State.FOCUS_PC)
    assert f.present is False and not f.signals["posture"].available


def test_stillness_and_drowsy_raise_posture_delta(cfg):
    cache = SensorCache()
    tr = SessionTracker()
    cache.put("mmwave", mm(0.0, level=2))
    f0 = build_frame(cache.snapshot(), 0.0, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f0.signals["posture"].delta == pytest.approx(cfg["mmwave"]["drowsy_delta"]["AWAKE"])
    half = cfg["mmwave"]["still_full_sec"] / 2
    cache.put("mmwave", mm(half, level=2))
    f1 = build_frame(cache.snapshot(), half, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f1.signals["posture"].delta == pytest.approx(0.5)
    cache.put("mmwave", mm(half + 1, level=2, drowsy="DROWSY"))
    f2 = build_frame(cache.snapshot(), half + 1, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f2.signals["posture"].delta == pytest.approx(1.0)
    cache.put("mmwave", mm(half + 2, level=80))
    f3 = build_frame(cache.snapshot(), half + 2, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f3.signals["posture"].phi == pytest.approx(0.8)
    assert f3.signals["posture"].delta < 0.5      # 움직이면 정적 타이머 리셋


def test_keystroke_inactive_is_unavailable_and_active_maps_ramps(cfg):
    cache = SensorCache()
    cache.put("keystroke", ks(10.0, typing=False, idle=1.0))
    f = build_frame(cache.snapshot(), 10.0, cfg, SessionTracker(), fsm_state=State.FOCUS_PC)
    assert not f.signals["keystroke"].available
    k = cfg["keystroke"]
    cache.put("keystroke", ks(11.0, idle=k["idle_ratio_high"], cv=k["flight_cv_high"], corr=0.0))
    f = build_frame(cache.snapshot(), 11.0, cfg, SessionTracker(), fsm_state=State.FOCUS_PC)
    assert f.signals["keystroke"].available
    assert f.signals["keystroke"].phi == pytest.approx(1.0)
    assert f.signals["keystroke"].delta == pytest.approx(1.0)


def test_pc_ratio_from_keystroke_history(cfg):
    cache = SensorCache()
    for t in range(10):
        cache.put("keystroke", ks(float(t), input_active=(t % 2 == 0)))
    f = build_frame(cache.snapshot(), 10.0, cfg, SessionTracker(), fsm_state=State.FOCUS_PC)
    assert f.pc_ratio == pytest.approx(0.5)


def test_environment_co2_ramp_and_invalid(cfg):
    cache = SensorCache()
    e = cfg["environment"]
    cache.put("env", env(0.0, co2=(e["co2_ppm_low"] + e["co2_ppm_high"]) / 2))
    f = build_frame(cache.snapshot(), 0.0, cfg, SessionTracker(), fsm_state=State.FOCUS_PC)
    assert f.signals["environment"].delta == pytest.approx(0.5)
    cache.put("env", env(1.0, co2=2000, valid=False))
    f = build_frame(cache.snapshot(), 1.0, cfg, SessionTracker(), fsm_state=State.FOCUS_PC)
    assert not f.signals["environment"].available


def test_auto_complete_actions_and_feedback(cfg):
    cache = SensorCache()
    tr = SessionTracker()
    f = build_frame(cache.snapshot(), 0.0, cfg, tr, fsm_state=State.ACTION_ENV)
    assert f.action_done is True
    tr.pending_feedback = "reject"
    f = build_frame(cache.snapshot(), 1.0, cfg, tr, fsm_state=State.ACTION_BREAK)
    assert f.break_accepted is False and f.action_done is False
    assert tr.pending_feedback is None


def test_sensor_summary_omits_missing_fields(cfg):
    cache = SensorCache()
    cache.put("mmwave", mm(0.0))
    s = sensor_summary(cache.snapshot(), 0.0, cfg)
    assert s["present"] is True and s["mmwave"]["drowsy_state"] == "AWAKE"
    assert "co2_ppm" not in s and "keystroke" not in s


# ── MQTT 파서 ──────────────────────────────────────────────────────

def test_parse_envelope_and_flat_payload():
    body = {"schema_version": "1.0", "ts": 1.5, "node": "esp32", "boot_id": "ab", "seq": 7,
            "data": {"present": True}}
    kind, s = parse_sensor_message("deskmate/sensor/mmwave/esp32", json.dumps(body).encode(), 9.0)
    assert kind == "mmwave" and s.seq == 7 and s.ts == 1.5 and s.data["present"] is True
    kind, s = parse_sensor_message("deskmate/sensor/keystroke", json.dumps({"ts": 2.0, "typing_active": False}).encode(), 9.0)
    assert kind == "keystroke" and s.seq is None and s.data["typing_active"] is False
    assert parse_sensor_message("deskmate/sensor/tof/x", b"not json", 9.0) is None
    assert parse_sensor_message("deskmate/state/phase", b"{}", 9.0) is None


def test_cache_counts_seq_gaps():
    cache = SensorCache()
    for seq in (1, 2, 4, 5, 9):
        cache.put("mmwave", mm(float(seq), seq=seq))
    assert cache.seq_gaps["mmwave"] == 2


# ── 라이브 루프 (브로커 없이) ────────────────────────────────────────

def test_live_hub_runs_real_sensor_path_to_focus_and_idle(cfg):
    cache = SensorCache()
    published = []
    flog = io.StringIO()
    hub = LiveHub(cache, ingest_cfg=cfg, publish=published.append, frame_log=flog, out=io.StringIO())
    period = hub.period
    baseline = float(hub.fsm_cfg["timers"]["baseline_sec"])
    t = 0.0
    # 착석 + 타이핑: START → CONTEXT_DETECT → FOCUS_PC
    while t <= baseline + 3 * period:
        cache.put("mmwave", mm(t, level=30))
        cache.put("keystroke", ks(t))
        hub.tick_once(t)
        t += period
    assert hub.engine.state is State.FOCUS_PC
    assert published[0]["data"]["fsm_state"] in ("START",)
    # 자리 비움: absent_idle_sec 뒤 IDLE
    absent = float(hub.fsm_cfg["timers"]["absent_idle_sec"])
    end = t + absent + 2 * period
    while t <= end:
        cache.put("mmwave", mm(t, present=False))
        hub.tick_once(t)
        t += period
    assert hub.engine.state is State.IDLE
    # 프레임 로그는 리플레이 형식이어야 한다
    frames = list(iter_frames(io.StringIO(flog.getvalue())))
    assert len(frames) == len(published) and frames[0].touch is True
    assert all("sensor_summary" in e["data"] for e in published)


# ── interaction/request (제안 게이트 확인 질문) ─────────────────────────

def test_live_hub_publishes_request_on_suggest_and_matches_feedback(cfg):
    from deskmate_hub.inference import Context, GateMode, Scores, TickResult

    cache = SensorCache()
    states, requests = [], []
    hub = LiveHub(cache, ingest_cfg=cfg, publish=states.append, publish_request=requests.append, out=io.StringIO())

    def result(state, gate):
        return TickResult(state=state, context=Context.PC, scores=Scores(c_focus=0.2, c_fatigue=0.6, focus_weights={}, fatigue_weights={}),
                          actions=["break_suggest"], gate=gate, cause="cognitive")

    # 자동 실행(≥0.75)은 질문하지 않는다
    hub._maybe_request(result(State.ACTION_ENV, GateMode.AUTO), State.CAUSE_ANALYSIS, 100.0)
    assert requests == [] and hub.pending_request is None
    # 제안(0.45~0.75)으로 ACTION_BREAK 에 새로 들어오면 질문 1건
    hub._maybe_request(result(State.ACTION_BREAK, GateMode.SUGGEST), State.CAUSE_ANALYSIS, 100.0)
    assert len(requests) == 1
    req = requests[0]["data"]
    assert req["kind"] == "break_suggest" and req["options"] == ["accept", "reject"] and req["request_id"]
    # 같은 상태 유지 중에는 다시 묻지 않는다
    hub._maybe_request(result(State.ACTION_BREAK, GateMode.SUGGEST), State.ACTION_BREAK, 110.0)
    assert len(requests) == 1
    # 다른 질문의 응답은 무시, 맞는 request_id(또는 생략)는 반영
    cache.put_feedback({"verdict": "accept", "request_id": "stale-id"})
    hub.tick_once(120.0)
    assert hub.tracker.pending_feedback is None and hub.pending_request is not None
    hub.pending_request = requests[0]
    cache.put_feedback({"verdict": "reject", "request_id": req["request_id"]})
    view_before = hub.tracker.pending_feedback
    hub.tick_once(130.0)          # tick 안에서 build_frame 이 pending_feedback 을 소비한다(IDLE 상태라 무시)
    assert view_before is None and hub.pending_request is None


# ── 세션 리포트 발행 (START~IDLE/END) ─────────────────────────────────

def test_live_hub_publishes_session_report_when_session_ends(cfg):
    cache = SensorCache()
    reports = []
    hub = LiveHub(cache, ingest_cfg=cfg, publish_report=reports.append, out=io.StringIO())
    period = hub.period
    baseline = float(hub.fsm_cfg["timers"]["baseline_sec"])
    absent = float(hub.fsm_cfg["timers"]["absent_idle_sec"])
    t = 0.0
    while t <= baseline + 3 * period:
        cache.put("mmwave", mm(t, level=30)); cache.put("keystroke", ks(t)); hub.tick_once(t); t += period
    assert hub.recorder is not None and reports == []
    end = t + absent + 2 * period
    while t <= end:
        cache.put("mmwave", mm(t, present=False)); hub.tick_once(t); t += period
    assert hub.engine.state is State.IDLE and len(reports) == 1 and hub.recorder is None
    data = reports[0]["data"]
    assert data["ended_by"] == "absent_timeout" and data["duration_s"] > 0 and data["focus_time_s"] > 0
    assert "state_durations_s" in data and "intervention_counts" in data
    assert json.dumps(reports[0])            # JSON 직렬화 가능
