from __future__ import annotations

import json
import threading
import urllib.request

from deskmate_hub.inference import FSMEngine, SensorFrame, State
from deskmate_hub.demo import _test_frame, demo_steps, keystroke_summary
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

        test_frame = {
            "command": "reset",
            "advance_sec": 0,
            "present": True,
            "pc_ratio": 0.9,
            "signals": {
                key: {"phi": 0.1, "delta": 0.1, "available": True}
                for key in ("keystroke", "posture", "environment", "elapsed")
            },
        }
        request = urllib.request.Request(
            f"{base}/api/test-frame",
            data=json.dumps(test_frame).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 202
        assert store.pop_test_frame() == test_frame
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sensor_test_frames_drive_the_real_configured_fsm():
    payload = {
        "command": "reset",
        "advance_sec": 0,
        "present": True,
        "pc_ratio": 0.9,
        "signals": {
            key: {"phi": 0.1, "delta": 0.1, "available": True}
            for key in ("keystroke", "posture", "environment", "elapsed")
        },
    }
    engine = FSMEngine()
    engine.tick(_test_frame(payload, 0, touch=True))
    engine.tick(_test_frame(payload, engine.tm["baseline_sec"] + 1))
    result = engine.tick(_test_frame(payload, engine.tm["baseline_sec"] + 2))
    assert result.state is State.FOCUS_PC

    for signal in payload["signals"].values():
        signal["delta"] = 0.95
    now = engine.tm["baseline_sec"] + 32
    engine.tick(_test_frame(payload, now))
    result = engine.tick(_test_frame(payload, now + engine.tm["fatigue_elevated_hold_sec"]))
    assert result.state is State.FATIGUE_SUSPECT


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


# `deskmate/sensor/keystroke` 규약 필드 + collector 규약 추가분. 이 밖은 나가면 안 된다.
_KEYSTROKE_FIELDS = {
    "node", "ts", "window_s", "event_count",
    "dwell_mean_ms", "dwell_std_ms", "flight_mean_ms", "flight_std_ms",
    "idle_ratio", "correction_rate", "mouse_event_rate", "typing_active", "valid",
}


def test_keystroke_summary_tracks_the_fatigue_signal():
    steps = list(demo_steps())
    calm = keystroke_summary(steps[0].signals["keystroke"], ts=1.0)
    tired = keystroke_summary(steps[3].signals["keystroke"], ts=2.0)

    assert tired["dwell_mean_ms"] > calm["dwell_mean_ms"]
    assert tired["flight_mean_ms"] > calm["flight_mean_ms"]
    assert tired["idle_ratio"] > calm["idle_ratio"]
    assert tired["correction_rate"] > calm["correction_rate"]
    assert 0 <= calm["idle_ratio"] <= 1 and 0 <= tired["idle_ratio"] <= 1


def test_keystroke_summary_carries_no_key_content():
    summary = keystroke_summary(None, ts=1.0)

    assert set(summary) <= _KEYSTROKE_FIELDS
    # display 가 신선도를 재려면 자체 ts 가 반드시 있어야 한다.
    assert summary["ts"] == 1.0


def test_demo_envelope_carries_the_keystroke_summary():
    engine = FSMEngine()
    step = list(demo_steps())[0]
    result = engine.tick(
        SensorFrame(now=step.now, present=True, pc_ratio=0.9, signals=step.signals, touch=True)
    )
    payload = state_envelope(
        result,
        boot_id="testboot",
        seq=1,
        ts=50.0,
        sensor_summary={"keystroke": keystroke_summary(step.signals["keystroke"], ts=49.0)},
    )

    keystroke = payload["data"]["sensor_summary"]["keystroke"]
    assert keystroke["window_s"] == 60
    assert keystroke["ts"] == 49.0


def test_keystroke_summary_wobbles_between_windows():
    """FSM 신호가 그대로여도 창마다 값이 달라야 화면이 멈춘 것처럼 안 보인다."""
    steps = list(demo_steps())
    calm = steps[0].signals["keystroke"]

    samples = [keystroke_summary(calm, ts=float(i), seq=i) for i in range(8)]
    dwell = {s["dwell_mean_ms"] for s in samples}

    assert len(dwell) > 1, "같은 신호에서도 창마다 dwell 이 흔들려야 한다"
    # 흔들림은 어디까지나 잔물결이다. calm/tired 구분을 덮으면 안 된다.
    tired = keystroke_summary(steps[3].signals["keystroke"], ts=0.0, seq=0)
    assert max(dwell) < tired["dwell_mean_ms"]
    for s in samples:
        assert 0.0 <= s["idle_ratio"] <= 1.0
        assert 0.0 <= s["correction_rate"] <= 1.0


def test_keystroke_summary_is_deterministic():
    """리플레이와 테스트가 재현되려면 같은 seq 는 같은 값이어야 한다."""
    calm = list(demo_steps())[0].signals["keystroke"]
    assert keystroke_summary(calm, ts=1.0, seq=5) == keystroke_summary(calm, ts=1.0, seq=5)
