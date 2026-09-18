"""세션 작업 리포트 테스트."""
from __future__ import annotations

from deskmate_hub.__main__ import main
from deskmate_hub.demo import demo_frames
from deskmate_hub.inference import (
    Context,
    SensorFrame,
    SessionRecorder,
    State,
    TickResult,
    build_report,
    format_session_report,
    report_envelope,
)
from deskmate_hub.inference.types import Scores


def _fr(t: float) -> SensorFrame:
    return SensorFrame(now=t, present=True, pc_ratio=0.9)


def _res(state, *, actions=(), cause=None, cfat=0.0):
    return TickResult(
        state=state, context=Context.PC,
        scores=Scores(0.0, cfat, {}, {}), actions=list(actions), cause=cause,
    )


def _feed(seq):
    rec = SessionRecorder()
    for t, r in enumerate(seq):
        rec.observe(_fr(t * 10), r)
    return rec.finalize()


# ── 통합: 데모 세션 ────────────────────────────────────────
def test_demo_report_happy_path():
    r = build_report(demo_frames())
    assert r.t_start == 0 and r.t_end == 1080
    assert r.duration == 1080 and r.focus_time > 0
    assert len(r.episodes) == 1
    ep = r.episodes[0]
    assert ep.t_onset == 810 and ep.t_resolved == 960
    assert ep.peak_fatigue > 0.8
    assert len(r.interventions) == 1
    iv = r.interventions[0]
    assert iv.action == "ACTION_BREAK" and iv.cause == "cognitive"
    assert iv.outcome == "recovered"
    assert r.esm_labels == []


# ── recorder 결과 라벨링 (합성 결과 직접 주입) ─────────────
def test_intervention_recovered():
    r = _feed([
        _res(State.START), _res(State.FATIGUE, cfat=0.8),
        _res(State.CAUSE_ANALYSIS, cfat=0.8),
        _res(State.ACTION_ENV, cause="environment", cfat=0.8),
        _res(State.MONITOR, cfat=0.3), _res(State.RECOVERY, cfat=0.2),
        _res(State.FOCUS_PC, cfat=0.1), _res(State.END),
    ])
    assert r.interventions[0].outcome == "recovered"
    assert r.episodes[0].t_resolved is not None


def test_intervention_escalated():
    r = _feed([
        _res(State.FATIGUE, cfat=0.8),
        _res(State.ACTION_POSTURE, cause="posture", cfat=0.8),
        _res(State.MONITOR, cfat=0.8), _res(State.ESCALATE, cfat=0.8),
    ])
    assert r.interventions[0].outcome == "escalated"
    assert r.interventions[0].cause == "posture"


def test_break_rejected_records_esm_label():
    r = _feed([
        _res(State.FATIGUE, cfat=0.8),
        _res(State.ACTION_BREAK, cause="cognitive", cfat=0.8),
        _res(State.MONITOR, actions=["esm_label:reject"], cfat=0.8),
    ])
    assert r.esm_labels == [{"t": 20, "label": "break_reject"}]
    assert r.interventions[0].accepted is False
    assert r.interventions[0].outcome == "rejected"


def test_break_accepted_records_esm_label():
    r = _feed([
        _res(State.FATIGUE, cfat=0.8),
        _res(State.ACTION_BREAK, cause="cognitive", cfat=0.8),
        _res(State.REST, actions=["rest_timer.start"], cfat=0.8),
    ])
    assert r.esm_labels == [{"t": 20, "label": "break_accept"}]
    assert r.interventions[0].accepted is True


def test_peak_fatigue_tracked():
    r = _feed([
        _res(State.FATIGUE, cfat=0.72),
        _res(State.CAUSE_ANALYSIS, cfat=0.95),
        _res(State.ACTION_BREAK, cause="cognitive", cfat=0.80),
    ])
    assert r.episodes[0].peak_fatigue == 0.95


# ── 포맷/CLI ───────────────────────────────────────────────
def test_format_session_report_contains_sections():
    text = format_session_report(build_report(demo_frames()))
    for token in ("세션 리포트", "작업 시간", "몰입 시간", "피로 에피소드", "개입", "ESM 라벨"):
        assert token in text


def test_main_report_flag(capsys):
    assert main(["--demo", "--report", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "세션 리포트" in out and "피로 에피소드: 1회" in out


def test_report_envelope_snapshots_a_running_session():
    """세션이 끝나기 전에도 화면이 읽을 수 있는 요약이 나와야 한다."""
    rec = SessionRecorder()
    rec.observe(_fr(100.0), _res(State.START))
    rec.observe(_fr(110.0), _res(State.FOCUS_PC))
    rec.observe(_fr(130.0), _res(State.FATIGUE, cfat=0.8))

    env = report_envelope(rec.finalize(), now=140.0)

    assert env["schema_version"] == "1.0"
    data = env["data"]
    # 아직 END 를 안 봤으므로 t_end 는 '지금' 이고 세션은 40 초째다.
    assert data["t_start"] == 100.0
    assert data["t_end"] == 140.0
    assert data["duration_s"] == 40.0
    # START 10 s + FOCUS_PC 20 s 중 집중은 FOCUS_PC 20 s 다.
    assert data["focus_time_s"] == 20.0
    assert data["focus_ratio"] == 0.5
    assert len(data["fatigue_episodes"]) == 1
    assert data["state_durations_s"]["FOCUS_PC"] == 20.0


def test_report_envelope_says_unknown_when_nothing_was_asked():
    """물어본 적이 없으면 수락률은 0% 가 아니라 모름이다."""
    env = report_envelope(SessionRecorder().finalize(), now=10.0)
    assert env["data"]["break_accept_rate"] is None
    # 길이가 0 인 세션에서 0 으로 나누지 않는다.
    assert env["data"]["duration_s"] == 0.0
    assert env["data"]["focus_ratio"] == 0.0
