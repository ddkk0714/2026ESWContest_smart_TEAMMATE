"""결측·유실 시나리오 — 센서가 빠지거나 끊겨도 hub 는 죽지 않고 판정·발행을 이어 간다 (W6).

MQTT 없이 가상 시계로 cache 에 같은 payload 를 넣고 10 s 마다 tick 한다. 브로커 재시작은
test_mqtt_reconnect.py, 디스플레이 무응답(제안 카드 미응답)은 tools/test_demo_scenario.py 가 본다.
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

from deskmate_hub.inference import load_config
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.live import LiveHub

DEMO = Path(__file__).resolve().parents[1] / "deskmate_hub" / "config" / "fsm.demo.yaml"

SEATED = {"present": True, "motion_level": 35, "motion_state": "active", "distance_cm": 50,
          "resp_valid": True, "resp_bpm": 14, "heart_valid": False, "drowsy_state": "AWAKE", "valid": True}
ENV = {"co2_ppm": 700, "temp_c": 24.0, "humidity_pct": 45.0, "lux": 420,
       "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True}
TYPING = {"window_s": 60, "flight_cv": 0.3, "idle_ratio": 0.15, "correction_rate": 0.04,
          "typing_active": True, "input_active": True, "mouse_active": True}


class Rig:
    """초 단위로 센서를 흘리고 10 s 마다 hub tick."""

    def __init__(self) -> None:
        self.t = 1_790_000_000.0
        self.cache = SensorCache()
        self.hub = LiveHub(self.cache, fsm_cfg=load_config(str(DEMO)),
                           control_cfg={"enabled": False, "esm_log_path": str(Path("unused"))}, out=io.StringIO())
        self.envelopes: list[dict] = []
        self.second = 0
        self.seq = {}

    def _send(self, topic: str, data: dict) -> None:
        self.seq[topic] = self.seq.get(topic, 0) + 1
        body = {"schema_version": "1.0", "ts": self.t, "node": "t", "boot_id": "b", "seq": self.seq[topic], "data": data}
        route_mqtt_message(self.cache, topic, json.dumps(body).encode(), self.t)

    def run(self, seconds: int, *, mmwave: dict | None = SEATED, env: dict | None = ENV,
            keystroke: dict | None = TYPING) -> list[dict]:
        out = []
        for _ in range(seconds):
            if mmwave is not None:
                self._send("deskmate/sensor/mmwave/t", mmwave)
            if keystroke is not None:
                self._send("deskmate/sensor/keystroke", keystroke)
            if env is not None and self.second % 5 == 0:
                self._send("deskmate/sensor/env/t", env)
            if self.second % 10 == 0:
                out.append(self.hub.tick_once(self.t))
            self.t += 1
            self.second += 1
        self.envelopes += out
        return out

    def focus(self) -> None:
        self.run(90)
        assert self.state.startswith("FOCUS_"), self.state

    @property
    def state(self) -> str:
        return self.envelopes[-1]["data"]["fsm_state"]


def _healthy(envelopes: list[dict]) -> None:
    """발행이 끊기지 않고(seq 연속) 점수가 유한하다."""
    seqs = [e["seq"] for e in envelopes]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    for e in envelopes:
        d = e["data"]
        assert math.isfinite(d["c_fatigue"]) and math.isfinite(d["c_focus"])
        assert 0.0 <= d["c_fatigue"] <= 1.0 and 0.0 <= d["c_focus"] <= 1.0


def test_env_sensor_lost_keeps_focus_and_drops_environment():
    rig = Rig()
    rig.focus()
    after = rig.run(60, env=None)           # SCD41·BH1750 노드 분리 (freshness 30 s 경과)
    _healthy(rig.envelopes)
    assert rig.state.startswith("FOCUS_")
    last = after[-1]["data"]["sensor_summary"]
    assert last.get("env_flags", []) == []
    assert rig.hub.engine.scores is not None


def test_keystroke_collector_lost_does_not_end_the_session():
    rig = Rig()
    rig.focus()
    rig.run(60, keystroke=None)             # PC collector 종료 — 재실은 mmWave 가 계속 본다
    _healthy(rig.envelopes)
    assert rig.state not in ("IDLE", "END")


def test_mmwave_unplugged_counts_as_absent_then_restarts_cleanly():
    # 재실은 mmWave 로만 판단한다 — 레이더가 빠지면 타이핑 중이어도 부재로 보고, absent_idle_sec(데모 60 s) 뒤 IDLE.
    # 다시 꽂히면 재실 상승엣지로 새 세션이 시작된다. 그 사이에도 발행은 끊기지 않는다.
    rig = Rig()
    rig.focus()
    rig.run(80, mmwave=None)
    assert rig.state == "IDLE"
    # 요약은 끊긴 센서의 필드를 생략한다 — "없음(present=false)"이 아니라 "모름". 화면이 둘을 구분할 수 있다.
    summary = rig.envelopes[-1]["data"]["sensor_summary"]
    assert "present" not in summary and "mmwave" not in summary
    rig.run(20)
    assert rig.state in ("START", "CONTEXT_DETECT") or rig.state.startswith("FOCUS_")
    _healthy(rig.envelopes)


def test_all_sensors_silent_hub_keeps_publishing():
    rig = Rig()
    rig.focus()
    rig.run(120, mmwave=None, env=None, keystroke=None)
    _healthy(rig.envelopes)
    assert rig.state == "IDLE"


def test_garbage_and_out_of_contract_payloads_are_ignored():
    rig = Rig()
    rig.focus()
    for topic, payload in [("deskmate/sensor/mmwave/t", b"\xff\xfe not json"),
                           ("deskmate/sensor/env/t", b"[1, 2, 3]"),
                           ("deskmate/sensor/unknown/t", b"{}"),
                           ("deskmate/feedback/user", b"{")]:
        assert route_mqtt_message(rig.cache, topic, payload, rig.t) is None
    # 계약 밖 verdict 도 hub 를 멈추지 않는다
    rig.cache.put_feedback({"verdict": "maybe"})
    rig.run(30)
    _healthy(rig.envelopes)
    assert rig.state.startswith("FOCUS_")
