"""`python -m deskmate_hub bridge` 를 실제 프로세스로 띄워 보드 라인 계약을 검증한다 (C++ 서비스 역할을 테스트가 맡는다).

stdin  ← `MQTT\\t<topic>\\t<payload>`  (센서·피드백)  /  `POST\\t<id>\\t/api/feedback\\t{...}` (HTTP 어댑터 경로)
stdout → `STATE\\t{json}` 한 줄 JSON, `ACK\\t<id>\\t<status>`
paho 없이 돈다(보드 조건). 두 번째 tick(≈ score_period 10 s) 까지 기다리므로 ~12 s 걸린다.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parents[1]


def _mm(now, present=True, level=40):
    return json.dumps({"schema_version": "1.0", "ts": now, "node": "esp32-desk1", "seq": 1,
                       "data": {"present": present, "motion_state": "active" if level > 10 else "still",
                                "motion_level": level, "distance_cm": 55, "resp_bpm": None, "resp_valid": False,
                                "heart_bpm": None, "heart_valid": False, "drowsy_state": "AWAKE", "valid": True}})


def test_bridge_process_speaks_line_protocol():
    env = dict(os.environ, DESKMATE_HUB_MODE="live", DESKMATE_MQTT_HOST="", PYTHONUNBUFFERED="1",
               PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "deskmate_hub", "bridge"], cwd=HUB_DIR, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", bufsize=1)
    lines: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout], daemon=True).start()

    def wait_for(prefix, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line.rstrip("\n")
        raise AssertionError(f"no {prefix!r} line within {timeout}s; stderr:\n{proc.stderr.read()[-2000:]}")

    try:
        first = wait_for("STATE\t", 20)
        state0 = json.loads(first.split("\t", 1)[1])
        assert state0["data"]["fsm_state"] == "IDLE" and "\n" not in first

        # 실제 ESP32 처럼 1 Hz 로 mmWave 를 계속 넣는다 (신선도 5 s — 한 번만 넣으면 다음 tick 에 stale)
        stop_feed = threading.Event()

        def feed():
            while not stop_feed.is_set():
                proc.stdin.write(f"MQTT\tdeskmate/sensor/mmwave/esp32-desk1\t{_mm(time.time())}\n")
                proc.stdin.flush()
                stop_feed.wait(1.0)

        threading.Thread(target=feed, daemon=True).start()
        proc.stdin.write("MQTT\tdeskmate/feedback/user\t{\"verdict\":\"accept\",\"request_id\":\"none\"}\n")
        proc.stdin.write("POST\t7\t/api/feedback\t{\"verdict\":\"reject\"}\n")
        proc.stdin.write("MQTT\tgarbage line without payload\n")   # 무시되고 죽지 않아야 한다
        proc.stdin.flush()
        ack = wait_for("ACK\t", 5)
        assert ack == "ACK\t7\t202"

        second = wait_for("STATE\t", 20)                              # 다음 tick 에 mmWave 가 반영된다
        state1 = json.loads(second.split("\t", 1)[1])
        stop_feed.set()
        summary = state1["data"]["sensor_summary"]
        assert summary["present"] is True and summary["mmwave"]["motion_state"] == "active" and summary["valid"] is True
        assert state1["data"]["fsm_state"] in ("IDLE", "START", "CONTEXT_DETECT")   # 자동 시작 여부는 ingest 설정에 따름
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)
