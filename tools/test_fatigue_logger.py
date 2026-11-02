"""fatigue_logger — 세션 경계·중복·retain 처리와 파일 위치."""
from __future__ import annotations

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fatigue_logger import FATIGUE_TOPIC, STATE_TOPIC, FatigueRecorder  # noqa: E402


def _env(data, seq=1, ts=100.0):
    return json.dumps({"schema_version": "1.0", "ts": ts, "seq": seq, "data": data}).encode()


def _label(event, ts, **extra):
    return _env({"event": event, "session_id": "A-1-9", "profile_id": "A-1", "ts": ts, **extra})


def _tick(seq):
    return _env({"fsm_state": "FOCUS_PC", "c_fatigue": 0.2, "sensor_summary": {"co2_ppm": 800}}, seq=seq)


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_records_ticks_only_inside_a_session(tmp_path):
    rec = FatigueRecorder(str(tmp_path), out=io.StringIO())
    assert rec.handle(STATE_TOPIC, _tick(1)) is None            # 시작 전
    rec.handle(FATIGUE_TOPIC, _label("session_start", 1.0, kss=3, sleep_hours_last_night=6.0))
    assert rec.handle(STATE_TOPIC, _tick(2), retained=True) is None
    assert rec.handle(STATE_TOPIC, _tick(3)) == "tick"
    rec.handle(FATIGUE_TOPIC, _label("kss", 2.0, kss=6, prompt_ts=1.5, response_ms=500))
    rec.handle(FATIGUE_TOPIC, _label("session_end", 3.0, kss=8))
    assert rec.handle(STATE_TOPIC, _tick(4)) is None            # 종료 뒤

    rows = _lines(tmp_path / "A-1" / "A-1-9.jsonl")
    assert [r["kind"] for r in rows] == ["label", "tick", "label", "label"]
    assert rows[0]["event"] == "session_start" and rows[0]["sleep_hours_last_night"] == 6.0
    assert rows[1]["seq"] == 3 and rows[1]["sensor_summary"] == {"co2_ppm": 800}
    assert rows[2]["kss"] == 6 and rows[3]["event"] == "session_end"


def test_duplicate_labels_are_written_once(tmp_path):
    rec = FatigueRecorder(str(tmp_path), out=io.StringIO())
    msg = _label("session_start", 1.0, kss=3)
    assert rec.handle(FATIGUE_TOPIC, msg) == "label"
    assert rec.handle(FATIGUE_TOPIC, msg) is None
    assert len(_lines(tmp_path / "A-1" / "A-1-9.jsonl")) == 1


def test_late_start_attaches_ticks_after_the_first_label(tmp_path):
    rec = FatigueRecorder(str(tmp_path), out=io.StringIO())
    rec.handle(FATIGUE_TOPIC, _label("kss", 5.0, kss=4))       # 기록기를 세션 중간에 켬
    assert rec.handle(STATE_TOPIC, _tick(7)) == "tick"


def test_rejects_ids_that_would_escape_the_folder(tmp_path):
    rec = FatigueRecorder(str(tmp_path), out=io.StringIO())
    bad = _env({"event": "kss", "session_id": "../x", "profile_id": "A-1", "ts": 1.0, "kss": 3})
    assert rec.handle(FATIGUE_TOPIC, bad) is None
    assert rec.handle(FATIGUE_TOPIC, b"not json") is None
    assert not any(tmp_path.iterdir())
