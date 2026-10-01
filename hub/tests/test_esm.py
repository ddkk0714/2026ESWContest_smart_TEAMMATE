"""ESM 라벨은 기록·집계하되 FSM 판정은 바꾸지 않는다."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from deskmate_hub.esm import calculate_metrics, make_label, write_label
from deskmate_hub.ingest import SensorCache
from deskmate_hub.live import LiveHub


def test_label_fields_for_each_verdict():
    for verdict in ("accept", "reject", "timeout", "correct"):
        label = make_label(
            label_id="boot-7", ts=110.0, verdict=verdict, request_id="req" if verdict != "correct" else None,
            kind="correction" if verdict == "correct" else "env_suggest", predicted_state="FATIGUE",
            cause="environment", gate="suggest", c_fatigue=0.8, c_focus=0.2,
            corrected_state="FOCUS_PC" if verdict == "correct" else None,
            response_ms=4000, request_ts=100 if verdict != "correct" else None,
            score_period_sec=10,
        )
        assert label["label_id"] == "boot-7" and label["source"] == "display"
        assert label["target_window_start_ms"] == 100000
        assert label["target_window_end_ms"] == 110000
        assert label["confidence"] == 0.8
        assert label["answer_code"] == ("correct:FOCUS_PC" if verdict == "correct" else verdict)


def test_write_failure_is_logged_without_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("occupied", encoding="utf-8")
    messages = []
    assert not write_label({"verdict": "correct"}, str(blocker / "esm.jsonl"), on_log=messages.append)
    assert messages and "기록 실패" in messages[0]


def test_metrics_denominators_medians_and_recovery():
    assert calculate_metrics([]) == {
        "suggest_accept_rate": None, "timeout_rate": None, "auto_undo_rate": None,
        "correction_rate": None, "correction_count": 0,
        "median_response_ms": None, "recovery_time_s": None,
    }
    from deskmate_hub.control import Episode
    from deskmate_hub.inference import FatigueEpisode, GateMode, Intervention

    episodes = [Episode("ACTION_ENV", "environment", GateMode.AUTO, 0, executed=True, outcome="undone"),
                Episode("ACTION_ENV", "environment", GateMode.AUTO, 1, executed=True, outcome="executed")]
    fatigue = [FatigueEpisode(0, t_resolved=50, interventions=[Intervention(20, "environment", "ACTION_ENV")])]
    records = [{"kind": "env_suggest", "verdict": v, "response_ms": ms}
               for v, ms in (("accept", 100), ("reject", 300), ("timeout", None))]
    records.append({"kind": "correction", "verdict": "correct", "response_ms": None})
    metrics = calculate_metrics(records, episodes=episodes, duration_s=3600, fatigue_episodes=fatigue)
    assert metrics == {
        "suggest_accept_rate": pytest.approx(1 / 3), "timeout_rate": pytest.approx(1 / 3),
        "auto_undo_rate": 0.5, "correction_rate": 1.0, "correction_count": 1,
        "median_response_ms": 200, "recovery_time_s": 30,
    }


@pytest.mark.parametrize("verdict", ["accept", "reject", "timeout"])
def test_live_feedback_writes_one_label_and_clears_matching_request(tmp_path, verdict):
    path = tmp_path / "logs" / "esm.jsonl"
    cache = SensorCache()
    hub = LiveHub(cache, control_cfg={"enabled": False, "esm_log_path": str(path)}, out=io.StringIO())
    hub.tick_once(100)
    hub.pending_request = {"ts": 101, "data": {"request_id": "req-1", "kind": "env_suggest", "cause": "environment"}}
    cache.put_feedback({"request_id": "req-1", "verdict": verdict, "response_ms": 2000})
    hub.tick_once(110)
    assert hub.pending_request is None
    labels = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(labels) == 1 and labels[0]["verdict"] == verdict
    assert labels[0]["kind"] == "env_suggest"
    assert labels[0]["target_window_start_ms"] == 101000
    assert hub.recorder.r.esm_labels[-1] == labels[0]
    assert hub.report_envelope(120)["data"]["metrics"]["suggest_accept_rate"] == (
        1.0 if verdict == "accept" else 0.0)


def test_correct_records_without_changing_state_or_pending(tmp_path):
    path = tmp_path / "logs" / "esm.jsonl"
    cache = SensorCache()
    hub = LiveHub(cache, control_cfg={"enabled": False, "esm_log_path": str(path)}, out=io.StringIO())
    hub.tick_once(100)
    state = hub.engine.state
    hub.pending_request = {"ts": 100, "data": {"request_id": "req-1", "kind": "env_suggest"}}
    pending = hub.pending_request
    cache.put_feedback({"verdict": "correct", "corrected_state": "FATIGUE"})
    hub.tick_once(110)
    assert hub.engine.state is state and hub.pending_request is pending
    label = json.loads(path.read_text(encoding="utf-8"))
    assert label["predicted_state"] == state.value
    assert label["target_window_start_ms"] == 100000
    assert label["answer_code"] == "correct:FATIGUE"


def test_invalid_feedback_is_ignored_and_timeout_requires_matching_id(tmp_path):
    path = tmp_path / "logs" / "esm.jsonl"
    cache = SensorCache()
    hub = LiveHub(cache, control_cfg={"enabled": False, "esm_log_path": str(path)}, out=io.StringIO())
    hub.pending_request = {"ts": 100, "data": {"request_id": "req-1", "kind": "env_suggest"}}
    for feedback in ({"verdict": "unknown"}, {"verdict": "correct", "corrected_state": "START"},
                     {"verdict": "timeout", "request_id": "stale"}):
        cache.put_feedback(feedback)
        hub.tick_once(110)
    assert hub.pending_request is not None and not path.exists()
    assert "알 수 없는" in hub.out.getvalue()


def test_evaluate_sessions_json_cli(tmp_path):
    path = tmp_path / "esm.jsonl"
    path.write_text(json.dumps({"kind": "env_suggest", "verdict": "accept", "response_ms": 200}) + "\n",
                    encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "tools" / "evaluate_sessions.py"
    completed = subprocess.run([sys.executable, str(script), str(path), "--json"],
                               capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["files"][str(path)]["suggest_accept_rate"] == 1.0
    assert data["overall"]["median_response_ms"] == 200


def test_labels_do_not_leave_gaps_in_state_seq(tmp_path):
    # 수신 측(Node-RED·앱)은 state/phase seq 구멍을 유실로 센다. 라벨 번호는 따로 센다.
    cache = SensorCache()
    hub = LiveHub(cache, control_cfg={"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")},
                  out=io.StringIO())
    seqs = [hub.tick_once(100)["seq"]]
    cache.put_feedback({"verdict": "correct", "corrected_state": "REST"})
    seqs.append(hub.tick_once(110)["seq"])
    cache.put_feedback({"verdict": "correct", "corrected_state": "IDLE"})
    seqs.append(hub.tick_once(120)["seq"])
    assert seqs == [seqs[0], seqs[0] + 1, seqs[0] + 2]
    ids = [r["label_id"] for r in hub.recorder.r.esm_labels if "label_id" in r]
    assert len(set(ids)) == 2
