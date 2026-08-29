from __future__ import annotations

import json
import threading
import urllib.request

from deskmate_hub.inference import FSMEngine, SensorFrame, State
from deskmate_hub.demo import demo_steps
from deskmate_hub.presentation import state_envelope
from deskmate_hub.preview_api import PreviewStateStore, make_server


def test_state_envelope_matches_display_contract():
    result = FSMEngine().tick(SensorFrame(now=10, present=True, pc_ratio=0.8, touch=True))
    payload = state_envelope(result, boot_id="testboot", seq=7, ts=12.5)

    assert payload["schema_version"] == "1.0"
    assert payload["seq"] == 7
    assert payload["data"]["fsm_state"] == State.START.value
    assert payload["data"]["phase"] == "start"
    assert 0 <= payload["data"]["confidence"] <= 1


def test_preview_api_serves_state_and_accepts_feedback():
    store = PreviewStateStore()
    store.publish({"schema_version": "1.0", "data": {"fsm_state": "IDLE"}})
    server = make_server("127.0.0.1", 0, store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base}/api/state") as response:
            assert json.load(response)["data"]["fsm_state"] == "IDLE"

        request = urllib.request.Request(
            f"{base}/api/feedback",
            data=json.dumps({"verdict": "accept", "request_id": "demo"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 202
        assert store.pop_feedback()["verdict"] == "accept"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_demo_drives_representative_fsm_path():
    engine = FSMEngine()
    states = []
    for step in demo_steps():
        result = engine.tick(SensorFrame(
            now=step.now,
            present=True,
            pc_ratio=0.9,
            signals=step.signals,
            touch=step.touch,
            end_touch=step.end_touch,
            action_done=step.action_done,
        ))
        states.append(result.state)

    assert State.FOCUS_PC in states
    assert State.FATIGUE_SUSPECT in states
    assert State.FATIGUE in states
    assert State.ACTION_BREAK in states
    assert State.RECOVERY in states
    assert states[-1] is State.END
