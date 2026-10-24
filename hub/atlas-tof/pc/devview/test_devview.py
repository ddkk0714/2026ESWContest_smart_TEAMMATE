"""devview.py 호스트 테스트 — 보드 없이. `python -m pytest hub/atlas-tof/pc/devview -q`"""
from __future__ import annotations

import json
import socket
import struct
import threading
import urllib.request

import pytest

import devview as dv


def env(data, node="hub", seq=1):
    return {"schema_version": "1.0", "ts": 1.0, "node": node, "boot_id": "b", "seq": seq, "data": data}


def payload(data, **kw):
    return json.dumps(env(data, **kw), ensure_ascii=False).encode("utf-8")


# ----------------------------------------------------------------- UTF-8

def test_decode_payload_korean_utf8():
    raw = payload({"explanation": "무드등 밝게 — 졸음"})
    assert dv.decode_payload(raw)["data"]["explanation"] == "무드등 밝게 — 졸음"
    # Latin-1 로 읽으면 깨지는 바이트열이 정확히 복원되는지(Pi 5 앱에서 실제로 깨졌던 버그)
    assert raw.decode("latin-1") != raw.decode("utf-8")


def test_decode_payload_bom_and_garbage():
    assert dv.decode_payload("﻿{\"a\": \"한\"}".encode("utf-8")) == {"a": "한"}
    assert dv.decode_payload(b"\xff\xfe not json") is None
    assert dv.decode_payload(b"online") is None


def test_store_logs_korean_request_text():
    st = dv.Store()
    st.on_mqtt(dv.TOPIC_REQUEST, payload({"kind": "suggestion", "action": "break",
                                          "explanation": "5분 쉬고 환기할까요?", "expires_in_s": 30}), at=0)
    assert st.snapshot()["log"][0]["text"] == 'suggestion break "5분 쉬고 환기할까요?" 30 s'


def test_mqtt_subscriber_reads_utf8_publish_from_socket():
    """작은 가짜 브로커: CONNACK → SUBACK → 한글 PUBLISH(QoS 0) 하나 보내고 닫는다."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    st, stop = dv.Store(), threading.Event()
    got = {}

    def broker():
        c, _ = srv.accept()
        h, body = dv.read_packet(c)
        got["connect"] = (h, body)
        c.sendall(b"\x20\x02\x00\x00")
        h, body = dv.read_packet(c)
        got["subscribe"] = body
        c.sendall(b"\x90\x03" + body[:2] + b"\x00")
        msg = payload({"fsm_state": "FATIGUE", "sensor_summary": {"personalize": {"explanation": "졸음 → 무드등"}}})
        topic = dv._mqtt_str(dv.TOPIC_PHASE)
        c.sendall(dv._packet(0x30, topic + msg))
        threading.Event().wait(0.3)
        stop.set()
        c.close()

    t = threading.Thread(target=broker, daemon=True)
    t.start()
    dv.mqtt_loop("127.0.0.1", port, st, stop)
    t.join(2)
    srv.close()
    assert got["connect"][0] == 0x10 and b"MQTT" in got["connect"][1]
    assert b"deskmate/#" in got["subscribe"]
    ph = st.snapshot()["phase"]
    assert ph["fsm_state"] == "FATIGUE"
    assert ph["explanation"] == "졸음 → 무드등"


def test_parse_publish_qos1_packet_id():
    body = dv._mqtt_str("deskmate/control/result") + struct.pack("!H", 7) + "결과".encode("utf-8")
    topic, data, pid = dv.parse_publish(0x02, body)
    assert (topic, data.decode("utf-8"), pid) == ("deskmate/control/result", "결과", 7)


def test_remaining_length_roundtrip():
    assert dv._remaining_length(0) == b"\x00"
    assert dv._remaining_length(127) == b"\x7f"
    assert dv._remaining_length(128) == b"\x80\x01"
    assert dv._remaining_length(16383) == b"\xff\x7f"


# ----------------------------------------------------------------- state/phase → 화면

def test_phase_view_empty_is_all_dash():
    v = dv.phase_view(None)
    assert v["fsm_state"] == v["cause"] == v["gate"] == v["hub_posture"] == v["presence"] == "-"
    assert v["fatigue"] is None and v["fatigue_text"] == "-"
    assert v["explanation"] == "-" and v["keystroke"] == "-"
    assert all(val == "-" for _, val in v["mmwave"] + v["env"])


def test_phase_view_full():
    v = dv.phase_view(env({
        "fsm_state": "ACTION_POSTURE", "phase": "action", "c_fatigue": 0.8123, "c_focus": 0.3,
        "gate": "auto", "cause": "posture", "reasons": ["tof_drowsy"],
        "sensor_summary": {
            "present": True, "presence_source": "both", "in_room": True,
            "tof": {"posture": "drowsy", "posture_confidence": 0.84, "posture_source": "service"},
            "mmwave": {"motion_state": "still", "motion_level": 12, "distance_cm": 62.4,
                       "heart_bpm": 71, "heart_valid": True, "resp_bpm": 14, "resp_valid": False, "drowsy_state": 1},
            "co2_ppm": 1024, "temp_c": 24.56, "humidity_pct": 41, "lux": 310, "env_flags": ["co2_high"],
            "keystroke": {"typing_active": False, "idle_ratio": 0.9, "correction_rate": 0.04, "flight_mean_ms": 180},
            "personalize": {"arm": "lamp_up", "explanation": "무드등 밝게"},
        }}))
    assert v["fsm_state"] == "ACTION_POSTURE" and v["fatigue_text"] == "0.81"
    assert v["hub_posture"] == "졸음 drowsy (service, 0.84)"
    assert v["presence"] == "있음 (근거 both)" and v["place"] == "책상"
    assert v["mmwave_distance"] == "62 cm"
    mm = dict(v["mmwave"])
    assert mm["재실"] == "-"  # sensor_summary.mmwave 에 present 가 없으면 '-'
    assert mm["심박"] == "71 bpm" and mm["호흡"] == "14 회/분 (무효)"
    e = dict(v["env"])
    assert e["CO₂"] == "1024 ppm" and e["온도"] == "24.6 ℃" and e["환경 경고"] == "co2_high"
    assert v["keystroke"].startswith("타이핑 없음")
    assert v["explanation"] == "무드등 밝게"


def test_phase_view_old_personalization_key_and_room_only():
    v = dv.phase_view(env({"sensor_summary": {"present": False, "in_room": True,
                                              "personalization": {"arm_label": "환기"}}}))
    assert v["place"] == "방" and v["presence"] == "없음" and v["explanation"] == "환기"


# ----------------------------------------------------------------- /raw·/api/state

def test_parse_raw_two_sensors_invalid_and_missing():
    dist = [500] * 64
    dist[0] = -1
    status = [5] * 64
    status[1] = 255
    r = dv.parse_raw({"frames": 9, "sensors": [
        {"id": 0, "have": True, "age_ms": 30, "w": 8, "h": 8, "dist_mm": dist, "status": status},
        {"id": 1, "have": False}]})
    a, b = r["sensors"]
    assert a["name"] == "A · 왼쪽" and b["name"] == "B · 오른쪽"
    assert a["valid_count"] == 62 and a["zone_count"] == 64 and a["min_mm"] == 500
    assert a["valid"][0] is False and a["valid"][1] is False and a["valid"][2] is True
    assert b["have"] is False and b["grid"] is None


def test_parse_raw_garbage():
    r = dv.parse_raw("nope")
    assert [s["have"] for s in r["sensors"]] == [False, False]


def test_posture_view_probs_and_placement():
    p = dv.posture_view({"posture": "chin_rest", "conf": 0.7, "valid": True, "online_sensors": 2,
                         "probs": {"chin_rest": 0.7, "upright": 0.3}, "placement": "right",
                         "model": {"file": "/opt/m/opt_l8_c256.bin"}})
    assert p["label"] == "턱괴기" and p["placement"] == "right" and p["model"] == "opt_l8_c256.bin"
    assert len(p["probs"]) == 9 and dict((l, v) for l, _, v in p["probs"])["chin_rest"] == 0.7
    assert dv.posture_view({})["placement"] is None


def test_poll_loop_falls_back_to_second_url():
    st, stop = dv.Store(), threading.Event()
    calls = []

    def fetch(url):
        calls.append(url)
        if "8772" in url:
            raise ConnectionRefusedError()
        stop.set()
        return {"frames": 1}

    dv.poll_loop("raw", ["http://h:8772/raw", "http://h:8771/raw"], 0.0, st.set_raw, st, stop, fetch=fetch)
    assert calls == ["http://h:8772/raw", "http://h:8771/raw"]
    assert st.raw == {"frames": 1} and st.links["raw"]["ok"]


def test_poll_failure_reason_shown():
    st, stop = dv.Store(), threading.Event()

    def fetch(url):
        stop.set()
        raise socket.timeout("timed out")

    dv.poll_loop("posture", ["http://h:8771/api/state"], 0.0, st.set_posture, st, stop, fetch=fetch)
    link = st.snapshot()["links"]["posture"]
    assert link["ok"] is False and "시간 초과" in link["reason"]


# ----------------------------------------------------------------- 명령 로그·기기 상태

def test_command_log_cap_200():
    st = dv.Store()
    for i in range(250):
        st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": f"c{i:05d}", "target_id": "vent_fan",
                                          "operation": "set_level", "value": i, "gate": "auto"}), at=0)
    log = st.snapshot()["log"]
    assert len(log) == 200
    assert log[0]["text"].startswith("vent_fan set_level 50 ") and log[-1]["text"].startswith("vent_fan set_level 249")


def test_log_lines_match_pi5_format():
    cmd = dv.log_entry(dv.TOPIC_CMD, env({"command_id": "9f3c1a77", "target_id": "desk_lamp",
                                          "operation": "adjust_brightness", "value": {"percent": 60},
                                          "gate": "auto", "cause": "posture"}), 0)
    assert cmd["kind"] == "CMD" and cmd["text"] == "desk_lamp adjust_brightness +60% (auto, id 9f3c1a) posture"
    res = dv.log_entry(dv.TOPIC_RESULT, env({"command_id": "9f3c1a77", "status": "failed",
                                             "error_code": "lamp_not_configured"}, node="display-control"), 0)
    assert res["text"] == "9f3c1a failed lamp_not_configured (display-control)" and res["ok"] is False
    fb = dv.log_entry(dv.TOPIC_FEEDBACK, env({"verdict": "reject", "corrected_state": "FOCUS_PC",
                                              "request_id": "abcdefgh"}), 0)
    assert fb["text"] == "reject → FOCUS_PC (req abcdef)"
    assert dv.log_entry(dv.TOPIC_PHASE, env({"fsm_state": "X"}), 0) is None


def test_device_board_adjust_brightness_previous_actual():
    b = dv.DeviceBoard()
    b.on_message(dv.TOPIC_CMD, env({"command_id": "c1", "target_id": "desk_lamp",
                                    "operation": "adjust_brightness", "value": {"percent": 60}}))
    lamp = b.lines()[0]
    assert lamp["name"] == "무드등" and lamp["value"] == "밝기 +60%" and lamp["mark"] == "… 결과 없음"
    b.on_message(dv.TOPIC_RESULT, env({"command_id": "old", "status": "failed"}))  # 다른 명령의 결과는 무시
    b.on_message(dv.TOPIC_RESULT, env({"command_id": "c1", "status": "succeeded", "previous": 110, "actual": 176}))
    lamp = b.lines()[0]
    assert lamp["value"] == "밝기 110→176" and lamp["mark"] == "✓" and lamp["ok"] is True
    assert [d["name"] for d in b.lines()] == ["무드등", "선풍기", "공기청정기"]


# ----------------------------------------------------------------- --fake 각본

def test_fake_scenario_runs_fsm_and_lamp_command():
    st = dv.Store()
    sc = dv.FakeScenario(st)
    states = []
    t = 0.0
    while t < dv.FAKE_PERIOD:
        sc.step(t)
        s = st.snapshot()["phase"]["fsm_state"]
        if not states or states[-1] != s:
            states.append(s)
        t += 0.2
    assert states == ["START", "FOCUS_PC", "FATIGUE_SUSPECT", "FATIGUE", "ACTION_POSTURE"]
    snap = st.snapshot()
    kinds = [e["kind"] for e in snap["log"]]
    assert kinds == ["CMD", "RESULT", "REQUEST", "FEEDBACK"]
    assert "desk_lamp adjust_brightness +60%" in snap["log"][0]["text"]
    assert "110→176" in snap["log"][1]["text"]
    assert snap["devices"][0]["value"] == "밝기 110→176"
    assert all(s["have"] and s["valid_count"] > 0 for s in snap["raw"]["sensors"])
    assert snap["posture"]["placement"] == "center"
    assert snap["phase"]["explanation"] != "-"


def test_fake_scenario_repeats_each_cycle():
    st = dv.Store()
    sc = dv.FakeScenario(st)
    for i in range(int(2 * dv.FAKE_PERIOD / 0.5)):
        sc.step(i * 0.5)
    assert [e["kind"] for e in st.snapshot()["log"]].count("CMD") == 2


# ----------------------------------------------------------------- 로컬 HTTP

def test_http_serves_page_and_data():
    st = dv.Store()
    dv.FakeScenario(st).step(30.0)
    server = dv.ThreadingHTTPServer(("127.0.0.1", 0), dv.make_handler(st))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "이 화면은 노트북 보기 전용" in page
        assert "http://" not in page and "https://" not in page  # 외부 자원 없음(오프라인)
        data = json.loads(urllib.request.urlopen(base + "/data").read().decode("utf-8"))
        assert data["phase"]["fsm_state"] == "ACTION_POSTURE"
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
