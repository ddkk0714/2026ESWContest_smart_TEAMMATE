"""mqtt_watch 요약 줄 — 점검표에서 눈으로 볼 값이 빠지지 않는지."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mqtt_watch import summarize  # noqa: E402


def _env(data, seq=3):
    return json.dumps({"schema_version": "1.0", "seq": seq, "data": data}).encode()


def test_state_line_shows_state_gate_flags_and_control():
    line = summarize("deskmate/state/phase", _env({
        "fsm_state": "ACTION_ENV", "gate": "suggest", "cause": "environment", "c_fatigue": 0.79, "c_focus": 0.1,
        "sensor_summary": {"present": True, "co2_ppm": 1400, "env_flags": ["co2_high", "too_hot"],
                           "control": {"active": True, "state": "ACTION_ENV", "outcome": "executed"}}}))
    for part in ("seq=3", "ACTION_ENV", "gate=suggest", "co2_ppm=1400", "flags=co2_high,too_hot", "ctl=ACTION_ENV/executed"):
        assert part in line


def test_feedback_request_control_report_lines():
    assert "응답 correct → REST" in summarize("deskmate/feedback/user",
                                            json.dumps({"verdict": "correct", "corrected_state": "REST"}).encode())
    assert "만료=30s" in summarize("deskmate/interaction/request", _env({"kind": "env_suggest", "expires_in_s": 30}))
    assert "vent_fan.set_power='on'" in summarize("deskmate/control/cmd", _env(
        {"target_id": "vent_fan", "operation": "set_power", "value": "on", "gate": "auto"}))
    assert "suggest_accept_rate" in summarize("deskmate/session/report", _env(
        {"duration_s": 60, "metrics": {"suggest_accept_rate": 1.0}}))
    assert summarize("deskmate/state/phase", b"\xff") .startswith("(JSON 아님")
