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
    assert v["explanation"] == "-" and v["episode"] == "-" and v["mmwave_distance"] == "-"
    assert dv.profile_text(None, None, None) == "-"
    secs = dv.sensor_sections(None, {}, None, 0.0)
    assert [s["key"] for s in secs] == ["mmwave", "env", "tof_feature", "tof_posture", "heartbeat", "keystroke"]
    assert all(s["source"] == "-" and s["rows"] == [] for s in secs)


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
    assert v["explanation"] == "무드등 밝게"


PHASE_FULL = env({"sensor_summary": {
    "tof": {"posture": "drowsy", "motion_score": 0.42, "nod_rate_hz": 0.3},
    "mmwave": {"motion_state": "still", "heart_bpm": 71, "heart_valid": True, "resp_valid": False},
    "co2_ppm": 1024, "temp_c": 24.56, "lux": 310, "env_flags": ["co2_high"],
    "keystroke": {"typing_active": False, "idle_ratio": 0.9},
    "control": {"active": True, "state": "ACTION_POSTURE", "cause": "posture", "gate": "auto",
                "outcome": "pending", "awaiting_user": True},
    "personalize": {"active_profile_id": "u-1"},
}, "profile": "demo-b"})


def test_sensor_sections_fall_back_to_hub_summary():
    secs = {s["key"]: s for s in dv.sensor_sections(None, {}, PHASE_FULL, 0.0)}
    assert secs["mmwave"]["source"].startswith("state/phase")
    assert dict(secs["mmwave"]["rows"]) == {"motion_state": "still", "heart_bpm": "71", "heart_valid": "true",
                                            "resp_valid": "false"}
    assert dict(secs["env"]["rows"]) == {"co2_ppm": "1024", "temp_c": "24.56", "lux": "310", "env_flags": "co2_high"}
    assert dict(secs["tof_feature"]["rows"])["motion_score"] == "0.42"
    assert secs["heartbeat"]["rows"] == []  # hub 요약에는 하트비트가 없다


def test_sensor_sections_prefer_raw_other_then_mqtt_topic():
    raw = {"other": {"mmwave": {"have": True, "age_ms": 300, "seq": 9, "count": 9, "len": 11, "ts_ms": 1,
                                "present": True, "distance_cm": None, "drowsy_state": "AWAKE"},
                     "env": {"have": False}, "heartbeat": {"have": True, "age_ms": 0, "fw": "0x0125", "tof_a": True}}}
    topics = {"deskmate/sensor/env/esp32-1": {"env": env({"co2_ppm": 900, "ts": 5}), "count": 3, "last": 8.0},
              "deskmate/sensor/mmwave/esp32-1": {"env": env({"present": False}), "count": 1, "last": 9.0}}
    secs = {s["key"]: s for s in dv.sensor_sections(raw, topics, PHASE_FULL, 10.0)}
    mm = secs["mmwave"]
    assert mm["source"].startswith("/raw other") and mm["age_s"] == 0.3 and mm["meta"] == "seq 9 · 9개"
    assert dict(mm["rows"]) == {"present": "true", "distance_cm": "null", "drowsy_state": "AWAKE"}  # 머리 키 제외
    e = secs["env"]  # other 가 have=false 면 MQTT 토픽
    assert e["source"] == "MQTT deskmate/sensor/env/esp32-1" and e["age_s"] == 2.0 and dict(e["rows"]) == {"co2_ppm": "900"}
    assert dict(secs["heartbeat"]["rows"]) == {"fw": "0x0125", "tof_a": "true"}


def test_profile_and_episode_text():
    pr = env({"profile_id": "u-7f3a"}, node="pi5-display")
    assert dv.profile_text(PHASE_FULL, pr, None) == "hub demo-b · 개인화 u-1 · 첫 설정 u-7f3a"
    assert dv.phase_view(PHASE_FULL)["episode"] == "ACTION_POSTURE · posture · auto · pending · 사용자 응답 대기"


def test_board_c3441f4_summary_shape():
    """10-26 실기 hub(c3441f4) 의 sensor_summary 모양: personalization{mode,status,decision_source}, learning, tof_service,
    co2_window. 시연 프로파일은 어디에도 실리지 않는다."""
    ph = env({"fsm_state": "FOCUS_BREAK", "sensor_summary": {
        "tof": {"posture": "face_down", "posture_source": "service"},
        "tof_service": {"posture": "recline", "posture_confidence": 0.67, "valid": True, "online_sensors": 2},
        "co2_ppm": 636, "co2_window": {"co2_avg": 640.5, "waiting": False, "counts": {"open": 1}},
        "personalization": {"mode": "record", "status": "idle", "decision_source": "prior"},
        "learning": {"enabled": True, "n_labels": 3, "acc_ai": 0.5, "acc_fsm": 0.25, "p": 0.5, "pending": False},
        "control": {"active": False}}})
    v = dv.phase_view(ph)
    assert v["explanation"] == "mode record · status idle · decision_source prior"
    assert v["learning"] == "켜짐 · 라벨 3 · AI 정확 0.50 · FSM 정확 0.25 · p 0.50 · 대기 false"
    assert v["episode"] == "없음"
    assert dv.profile_text(ph, None, env({"status": "online"}), hint="demo-b") == "hub demo-b (노트북 지정)"
    assert dv.profile_text(ph, None, None) == "-"
    secs = {s["key"]: s for s in dv.sensor_sections({"frames": 1, "sensors": []}, {}, ph, 0.0)}
    tof = dict(secs["tof_feature"]["rows"])
    assert tof["tof_service.posture"] == "recline" and tof["tof_service.online_sensors"] == "2"
    env_rows = dict(secs["env"]["rows"])
    assert env_rows["co2_window.co2_avg"] == "640.5" and env_rows["co2_window.counts.open"] == "1"
    assert secs["heartbeat"]["source"] == "-"


def test_raw_value_literals():
    assert [dv.raw_value(v) for v in (None, True, False, 0.5, 1.23456, 3, [1, 2], "a")] == \
        ["null", "true", "false", "0.5", "1.235", "3", "1, 2", "a"]


def test_store_keeps_every_topic_and_non_json():
    st = dv.Store()
    st.on_mqtt("deskmate/sensor/keystroke", payload({"typing_active": True}), at=1.0)
    st.on_mqtt("deskmate/sensor/keystroke", payload({"typing_active": False}), at=2.0)
    st.on_mqtt("deskmate/health/esp32", b"online", at=2.0)
    topics = {t["topic"]: t for t in dv.topics_view(st.topics, 3.0)}
    assert topics["deskmate/sensor/keystroke"]["count"] == 2 and topics["deskmate/sensor/keystroke"]["age_s"] == 1.0
    assert st.topics["deskmate/health/esp32"]["env"] == {"text": "online"}


# ----------------------------------------------------------------- 엎드림 0.99 추적

def _state(smooth, instant, a=None, b=None):
    j = {"probs": {"face_down": smooth}, "instant": {"probs": {"face_down": instant}}}
    if a is not None:
        j["sensors"] = [{"name": "A", "online": True, "probs": {"face_down": a}},
                        {"name": "B", "online": False, "probs": {"face_down": b}}]
    return j


def test_face_down_tracker_max_hits_and_window():
    tr = dv.FaceDownTracker(window_s=10)
    tr.add(_state(0.95, 0.991, 0.97, 0.5), now=0.0)
    tr.add(_state(0.993, 0.98), now=5.0)
    tr.add(_state(0.2, 0.1), now=20.0)  # 0 s·5 s 표본은 10 s 창 밖
    v = tr.view(20.0)
    assert v["max"]["smooth"] == 0.993 and v["max"]["instant"] == 0.991 and v["max"]["A"] == 0.97
    assert v["max"]["B"] is None  # 오프라인 센서는 세지 않는다
    assert v["hits"] == {"smooth": 1, "instant": 1} and v["samples"] == 3
    assert v["current"]["smooth"] == 0.2 and v["max_recent"] == 0.2
    assert len(v["spark"]) == 120 and v["spark"][-1][0] == 0.2


def test_face_down_tracker_ignores_empty():
    tr = dv.FaceDownTracker()
    tr.add(None, 0.0)
    tr.add({}, 0.0)
    assert tr.view(0.0)["samples"] == 0 and tr.view(0.0)["max"]["smooth"] is None


def test_fetch_bypasses_system_proxy(monkeypatch):
    """노트북 시스템 프록시가 172.20.x 요청을 가로채지 않게 — 빈 ProxyHandler 로 기본 프록시 처리기를 뺀다."""
    assert not any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in dv._NO_PROXY.handlers)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # 닿지 않는 프록시
    srv = dv.ThreadingHTTPServer(("127.0.0.1", 0), dv.make_handler(dv.Store()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        data = dv.fetch_json(f"http://127.0.0.1:{srv.server_address[1]}/data")
        assert "phase" in data
    finally:
        srv.shutdown()
        srv.server_close()


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
    assert kinds == ["CMD", "RESULT", "REQUEST", "FEEDBACK", "CMD", "RESULT"]
    assert "desk_lamp adjust_brightness +60%" in snap["log"][0]["text"]
    assert "110→176" in snap["log"][1]["text"]
    assert snap["devices"][0]["value"] == "밝기 110→176"
    assert all(s["have"] and s["valid_count"] > 0 for s in snap["raw"]["sensors"])
    assert snap["posture"]["placement"] == "center"
    assert snap["face_down"]["max"]["smooth"] >= 0.99 and snap["face_down"]["hits"]["smooth"] > 0
    assert all(s["rows"] for s in snap["sections"])  # 0x20·0x10·0x03·0x04·0xF0·키 입력 모두 채워짐
    assert snap["profile"] == "hub demo-b · 첫 설정 u-7f3a"
    assert snap["phase"]["explanation"] != "-"


def test_fake_scenario_repeats_each_cycle():
    st = dv.Store()
    sc = dv.FakeScenario(st)
    for i in range(int(2 * dv.FAKE_PERIOD / 0.5)):
        sc.step(i * 0.5)
    assert [e["kind"] for e in st.snapshot()["log"]].count("CMD") == 4


# ----------------------------------------------------------------- 키 입력 패널

def ks_env(ts, **data):
    base = {"node": "pc-collector", "window_s": 60, "dwell_mean_ms": 90.0, "flight_mean_ms": 180.0,
            "event_count": 120, "valid": True, "typing_active": True}
    return {"ts": ts, "node": "pc-collector", "data": {**base, **data}}


def test_keystroke_tracker_status_rate_and_history():
    k = dv.KeystrokeTracker()
    assert k.view(0.0)["status"] == "PC 수집기 대기"
    k.on_sample(ks_env(10.0), at=10.0)
    k.on_sample(ks_env(10.0), at=10.5)               # 같은 ts → 추이에 다시 넣지 않음
    k.on_sample(ks_env(11.0, valid=False), at=11.0)  # 무효 → 추이 제외
    k.on_sample(ks_env(12.0, typing_active=False, dwell_mean_ms=0.0), at=12.0)
    v = k.view(13.0)
    assert v["status"] == "입력 없음 · 수신 중" and v["rate_per_min"] == 120.0 and v["age_s"] == 1.0
    assert v["spark"] == [[90.0, 180.0], [0.0, 180.0]]
    assert k.view(20.0)["status"] == "수신 끊김"
    k.on_health({"ts": 1, "node": "pc-collector", "status": "online", "reconnects": 1}, at=5.0)  # envelope 없는 health
    assert k.view(20.0)["link"] == {"status": "online", "reconnects": 1, "age_s": 15.0}
    k.on_sample(ks_env(21.0, valid=False), at=21.0)
    assert k.view(21.0)["status"] == "유효하지 않은 표본" and k.view(21.0)["ok"] is False


def test_store_routes_keystroke_and_health_topics():
    st = dv.Store()
    st.on_mqtt(dv.TOPIC_KEYSTROKE, json.dumps(ks_env(1.0)).encode(), at=1.0)
    st.on_mqtt(dv.TOPIC_HEALTH_PC, b'{"ts": 1, "node": "pc-collector", "status": "online", "reconnects": 2}', at=1.0)
    v = st.snapshot()["keystroke"]
    assert v["event_count"] == 120 and v["link"]["status"] == "online"
    assert "key" not in json.dumps(v["spark"])  # 숫자 추이뿐


# ----------------------------------------------------------------- 제어 결론

# 10-26 실기에서 받은 모양 그대로(hub c3441f4 → Pi 5 display-control)
REAL_CMD = {"schema_version": "1.0", "ts": 1792990644.825, "node": "hub", "data": {
    "command_id": "85b381353be3", "target_id": "desk_lamp", "operation": "adjust_brightness", "value": {"percent": 30},
    "origin_state": "ACTION_ENV", "cause": "environment", "gate": "auto", "requires_confirmation": False,
    "expires_ts_ms": 1792990659819}}
REAL_RESULT = {"schema_version": "1.0", "ts": 1792990644.853, "node": "display-control", "seq": 8, "data": {
    "command_id": "85b381353be3", "status": "failed", "error_code": "feedback_disabled", "completed_ts_ms": 1}}


def test_control_view_real_board_command_and_result():
    c = dv.ControlTracker()
    c.on_phase(env({"fsm_state": "ACTION_ENV", "reasons": ["co2_high"]}))
    c.on_message(dv.TOPIC_CMD, REAL_CMD, at=100.0)
    v = c.view(env({"fsm_state": "MONITOR"}), now=101.0)
    assert v["kind"] == "command" and v["headline"] == "자동 · 환경 → iLink 무드등 (BLE) 밝기 +30 %"
    assert v["status"] == "… 결과 대기" and v["reason"] == "co2_high" and v["origin"] == "환경 개입"
    c.on_message(dv.TOPIC_RESULT, REAL_RESULT, at=100.1)
    v = c.view(env({"fsm_state": "MONITOR"}), now=101.0)
    line = v["commands"][0]
    assert line["result"] == "✗ failed feedback_disabled" and line["ok"] is False and line["by"] == "display-control"
    assert v["status"] == "✗ 실행 안 됨" and v["gate"] == "자동 실행"
    # 오래되면 지금 일이 아니라 추천 줄로(마지막 명령은 세부에 남긴다)
    v = c.view(env({"fsm_state": "FOCUS_PC"}), now=100.0 + dv.RECENT_CONTROL_S + 1)
    assert v["kind"] == "idle" and v["headline"] == "지금 개입 없음 — 집중 (FOCUS_PC)" and len(v["commands"]) == 1


def test_control_view_groups_env_commands_and_success_text():
    c = dv.ControlTracker()
    for i, (t, op, val) in enumerate([("vent_fan", "set_power", "on"),
                                      ("desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153})]):
        c.on_message(dv.TOPIC_CMD, env({"command_id": f"c{i}", "target_id": t, "operation": op, "value": val,
                                        "origin_state": "ACTION_ENV", "cause": "environment", "gate": "auto"}), at=10.0 + i)
    c.on_message(dv.TOPIC_RESULT, env({"command_id": "c1", "status": "succeeded", "previous": 110, "actual": 176},
                                      node="display-control"), at=12.0)
    c.on_message(dv.TOPIC_RESULT, env({"command_id": "c0", "status": "succeeded", "actual_value": "on"}), at=12.0)
    v = c.view(None, now=13.0)
    assert v["headline"] == "자동 · 환경 → Pico 2 W 선풍기 (BLE) 켬 + iLink 무드등 (BLE) 밝기 +60 % (최소 153)"
    assert [l["result"] for l in v["commands"]] == ["✓ 성공 on", "✓ 성공 110→176"] and v["status"] == "✓ 실행됨"


def test_control_view_suggest_card_then_feedback():
    c = dv.ControlTracker()
    c.on_message(dv.TOPIC_REQUEST, env({"request_id": "r1", "kind": "posture_suggest", "cause": "posture",
                                        "expires_in_s": 15, "explanation": "졸음 — 무드등을 밝힐까요?"}), at=5.0)
    v = c.view(None, now=6.0)
    assert v["kind"] == "request" and v["headline"] == "제안 카드 · 자세 → iLink 무드등 (BLE) 밝기 +60 % (최소 153)"
    assert v["status"] == "응답 대기 · 15 s 시한" and v["reason"] == "졸음 — 무드등을 밝힐까요?"
    c.on_message(dv.TOPIC_FEEDBACK, env({"request_id": "r1", "verdict": "accept"}), at=7.0)
    assert c.view(None, now=8.0)["status"].startswith("수락")


def test_recommendation_lines():
    assert dv.recommendation("") == "hub 판정 대기"
    assert dv.recommendation("MONITOR") == "효과 확인 중(MONITOR) — 새 명령 없음"
    assert dv.recommendation("FATIGUE", "posture") == "피로 확정 · 원인 자세 → iLink 무드등 (BLE) 밝기 +60 % (최소 153)"
    assert dv.recommendation("FATIGUE_SUSPECT").startswith("피로 의심: 확정되면 원인별로 — 자세 → iLink 무드등")
    assert dv.recommendation("ACTION_BREAK") == "휴식 개입 중 → 휴식 카드(기기 제어 없음) (명령 수신 대기)"


def test_op_text():
    assert dv.op_text("set_power", "off") == "끔" and dv.op_text("set_level", 2) == "풍량 2"
    assert dv.op_text("adjust_brightness", {"percent": -20}) == "밝기 -20 %"


# ----------------------------------------------------------------- 영상 모드

def test_video_view_bar_fields():
    ph = env({"fsm_state": "FOCUS_NPC", "context": "npc", "c_fatigue": 0.83,
              "sensor_summary": {"co2_ppm": 1106, "temp_c": 22.5, "lux": 350}})
    v = dv.video_view(ph, {"posture": "drowsy", "conf": 0.9, "valid": True}, {"headline": "지금 개입 없음"})
    assert v["posture"] == "졸음(꾸벅)" and v["chips"] == ["허브", "집중", "책·필기"]
    assert v["fatigue"] == 0.83 and v["env"] == "CO₂ 1106 ppm · 22.5 ℃ · 350 lx" and v["decision"] == "지금 개입 없음"
    assert dv.video_view(ph, None, {"headline": "x", "status": "✓ 실행됨", "time": "12:00:01"})["decision_status"] ==         "✓ 실행됨 · 12:00:01"
    e = dv.video_view(None, None, {})
    assert e["posture"] == "-" and e["chips"] == ["허브", "-", "-"] and e["env"] == "-"


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
