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
