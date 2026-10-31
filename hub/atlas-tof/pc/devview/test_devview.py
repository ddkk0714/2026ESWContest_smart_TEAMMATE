"""devview.py 호스트 테스트 — 보드 없이. `python -m pytest hub/atlas-tof/pc/devview -q`"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.error
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
    # personalization 은 2단계 피로 관찰(판정 무관) — 개입 선택 개인화(personalize)와 섞지 않는다
    assert v["stage2"] == "mode record · status idle · decision_source prior" and v["explanation"] == "-"
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
                                              "personalization": {"explanation": "환기"}}}))
    assert v["place"] == "방" and v["presence"] == "없음" and v["explanation"] == "환기"   # 옛 hub 는 설명을 여기에


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


# 10-26 실보드 e415d23 /api/state 모양(값은 예시)
BOARD_STATE = {
    "valid": True, "posture": "recline", "posture_ko": "뒤로 젖힘", "conf": 0.862,
    "probs": {"recline": 0.862, "upright": 0.006, "misplaced": 0.0, "face_down": 0.011},
    "instant": {"posture": "recline", "conf": 0.587, "probs": {"recline": 0.587, "misplaced": 0.293}},
    "model_output": {"posture": "recline", "conf": 0.6195, "probs": {"recline": 0.619, "misplaced": 0.281, "away": 0.084}},
    "rule": {"enabled": True, "face_down": False, "applied": False, "conf": 0.995, "share": 0, "up": 0, "lo": 16,
             "dlo": 1.061, "dup": -1, "seated": False, "seated_upright": True, "seated_applied": False},
    "misplaced_rule": {"mode": "and", "baseline": True, "baseline_source": "post", "device_moved": False,
                       "model_misplaced": True, "gated": True},
    "radial": True, "fill_notarget_mm": 3000, "placement": "left",
}


def test_posture_view_rules_and_raw_model_output():
    v = dv.posture_view(BOARD_STATE)
    assert v["conf"] == 0.862 and dict((l, x) for l, _, x in v["probs"])["recline"] == 0.862   # 막대 = 게이트 뒤 평활 확률
    r = v["rules"]
    assert r["model_raw"] == "젖힘 0.62 · 위치 재조정 0.281"
    assert r["lines"][0].startswith("엎드림 규칙 꺼짐 (share 0 · up 0 · lo 16 · dlo 1.061")
    assert r["lines"][1] == "앉음→바른자세 대기"
    assert r["lines"][2] == "위치 재조정 게이트 and · 베이스라인 있음(post) · 장치 움직임 아니오 · 모델 misplaced 예 → 막음"
    assert r["lines"][3] == "입력 radial 켜짐 · fill 3000 mm" and r["gated"] is True
    on = dict(BOARD_STATE, rule=dict(BOARD_STATE["rule"], face_down=True, seated_applied=True))
    assert dv.posture_view(on)["rules"]["lines"][0].startswith("엎드림 규칙 켜짐 → face_down 0.995")
    assert dv.posture_view({"posture": "upright", "conf": 0.9, "valid": True})["rules"] is None   # 옛 서비스


def test_hub_floor_note():
    assert dv.parse_floors("face_down=0.99, drowsy=0.80") == {"face_down": 0.99, "drowsy": 0.80}
    assert dv.parse_floors("") == {}
    assert dv.posture_view({"posture": "drowsy", "conf": 0.54, "valid": True})["hub_note"] == "hub 무시 (0.54 < 0.80)"
    assert dv.posture_view({"posture": "face_down", "conf": 0.995, "valid": True})["hub_note"] is None
    assert dv.posture_view({"posture": "face_down", "conf": 0.7, "valid": True})["hub_note"] == "hub 무시 (0.70 < 0.99)"
    assert dv.posture_view({"posture": "upright", "conf": 0.3, "valid": True})["hub_note"] is None   # 하한 없는 라벨
    assert dv.posture_view({"posture": "drowsy", "conf": 0.54, "valid": True}, floors={})["hub_note"] is None


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
    assert snap["profile"] == "hub demo-b · 개인화 u-7f3a · 첫 설정 u-7f3a"   # 가짜 hub 가 받은 프로필을 되돌려 준다
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


# ----------------------------------------------------------------- 가상 기기 (Pi 5 앱 규칙)

@pytest.mark.parametrize("current,percent,min_raw,expected", [
    # display/atlas/ui_env_app/test/feedback_policy_test.dart 의 adjustBrightness 기대값 그대로
    (110, 60, None, (110, 176)), (110, -50, None, (110, 55)), (110, -90, None, (110, 30)), (200, 60, None, (200, 255)),
    (None, 60, None, (30, 48)), (30, 1, None, (30, 40)), (250, 1, None, (250, 253)), (254, 1, None, (254, 255)),
    (255, 10, None, (255, 255)), (30, -20, None, (30, 30)), (90, 0, None, (90, 90)), (10, 50, None, (30, 45)),
    (48, 60, 153, (48, 153)), (200, 10, 153, (200, 220)), (None, 60, 153, (30, 153)),
])
def test_adjust_brightness_matches_pi5_app(current, percent, min_raw, expected):
    assert dv.adjust_brightness(current, percent, min_raw=min_raw) == expected


def test_fan_valid_matches_fan_frame():
    ok = [("set_power", "on"), ("set_power", "off"), ("set_level", 3), ("set_speed", 100), ("set_mode", "natural"),
          ("set_timer", 65535)]
    bad = [("set_power", "1"), ("set_level", 4), ("set_level", True), ("set_speed", -1), ("set_mode", "turbo"),
           ("set_brightness", 10)]
    assert all(dv.fan_valid(o, v) for o, v in ok) and not any(dv.fan_valid(o, v) for o, v in bad)


def phase_env(fsm, phase, gate="none", cause=None, **pers):
    return env({"fsm_state": fsm, "phase": phase, "gate": gate, "cause": cause,
                "sensor_summary": {"personalize": pers} if pers else {}})


def cmd_env(cid, target, op, value, gate="auto", cause="posture", origin="ACTION_POSTURE", at=100.0):
    return env({"command_id": cid, "target_id": target, "operation": op, "value": value, "gate": gate, "cause": cause,
                "origin_state": origin, "requires_confirmation": False, "expires_ts_ms": int((at + 15) * 1000)})


def test_virtual_phase_lighting_rules():
    h = dv.VirtualHome()
    h.on_phase(phase_env("FOCUS_PC", "focus"), 1.0)
    assert h.lamp["power"] is True and h.lamp["brightness"] == 110
    h.on_phase(phase_env("FATIGUE", "fatigue"), 2.0)          # 피로 국면이지만 ACTION_* 아님 → 그대로
    assert h.lamp["brightness"] == 110 and h.speaker["music"] is False
    h.on_phase(phase_env("ACTION_POSTURE", "fatigue", "suggest"), 3.0)   # 제안, 수락 전 → 그대로
    assert h.lamp["brightness"] == 110
    h.on_phase(phase_env("ACTION_POSTURE", "fatigue", "auto"), 4.0)      # 자동 → 피로 조명 180 + 음악
    assert h.lamp["brightness"] == 180 and h.speaker["music"] is True
    h.on_phase(phase_env("RECOVERY", "recovery"), 5.0)
    assert h.lamp["brightness"] == 90 and h.speaker["music"] is False
    h.on_phase(phase_env("END", "end"), 6.0)
    assert h.lamp["power"] is False
    assert [e["text"] for e in h.timeline if e["kind"] == "phase_light"][-1] == "단계 조명 end → 무드등 꺼짐"


def test_virtual_suggest_accept_applies_phase_outputs():
    h = dv.VirtualHome()
    h.on_phase(phase_env("FOCUS_PC", "focus"), 1.0)
    h.on_phase(phase_env("ACTION_POSTURE", "fatigue", "suggest"), 2.0)
    h.on_request(env({"request_id": "r1", "kind": "posture_suggest", "cause": "posture", "expires_in_s": 180,
                      "arm": "posture_voice", "explanation": "허리를 펴 볼까요?"}), 2.5)
    assert h.speaker["voice"] == "음성 안내: 허리를 펴 볼까요?"
    assert h.pending_card(3.0)["request_id"] == "r1"
    h.on_feedback(env({"request_id": "r1", "verdict": "accept"}, node="laptop-devview"), 3.0)
    assert h.lamp["brightness"] == 180 and h.speaker["music"] is True and h.pending_card(3.1) is None


def test_virtual_commands_lamp_fan_air_and_real_result():
    h = dv.VirtualHome()
    h.on_phase(phase_env("FOCUS_PC", "focus", arm="wake_light", decision_source="policy",
                         explanation="졸음 6회"), 99.0)
    e = h.on_cmd(cmd_env("c1", "desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153}), at=100.0)
    assert e["virtual"] == {"status": "succeeded", "actual_value": 176, "actual": 176, "previous": 110}
    assert e["text"] == "자세 개입(자세 원인) → iLink 무드등 밝기 +60 % (최소 153) (110→176)"
    assert e["arm"] == "wake_light" and e["decision_source"] == "policy" and e["reason"] == "졸음 6회"
    assert h.on_cmd(cmd_env("c1", "desk_lamp", "adjust_brightness", {"percent": 60}), at=100.5) is e   # 중복 → 한 번만
    h.on_result(env({"command_id": "c1", "status": "failed", "error_code": "feedback_disabled"}, node="display-control"))
    tl = h.view(101.0)["timeline"][0]
    assert tl["real"] == "failed feedback_disabled (display-control)" and tl["real_ok"] is False and tl["virtual_ok"] is True
    f = h.on_cmd(cmd_env("c2", "vent_fan", "set_level", 2, cause="environment", origin="ACTION_ENV"), at=100.0)
    assert f["virtual"]["status"] == "succeeded" and h.fan["level"] == 2 and f["arm"] is None   # wake_light 는 무드등 arm
    assert h.on_cmd(cmd_env("c3", "vent_fan", "set_level", 9), at=100.0)["virtual"]["error_code"] == \
        "unsupported_operation_or_value"
    air = h.on_cmd(cmd_env("c4", "air_purifier", "set_mode", "high", cause="environment"), at=100.0)
    assert air["virtual"]["status"] == "succeeded" and h.air["mode"] == "high"
    # 꺼진 램프에 +60 %: previous 0(꺼짐), 기준은 하한 30 → 48 → min_raw 153
    h.on_phase(phase_env("END", "end"), 102.0)
    g = h.on_cmd(cmd_env("c5", "desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153}, at=103.0), at=103.0)
    assert g["virtual"]["previous"] == 0 and g["virtual"]["actual"] == 153


def test_virtual_rejects_like_app():
    h = dv.VirtualHome()
    bad = env({"command_id": "x", "target_id": "desk_lamp", "operation": "set_power", "value": "on", "gate": "auto"})
    assert h.on_cmd(bad, 1.0)["virtual"]["error_code"] == "invalid_command"          # expires·requires_confirmation 없음
    late = cmd_env("y", "desk_lamp", "set_power", "on", at=0.0)
    assert h.on_cmd(late, 100.0)["virtual"]["error_code"] == "expired"


def test_virtual_vent_card_opens_window_and_undo_window():
    h = dv.VirtualHome()
    h.on_request(env({"request_id": "v1", "kind": "vent_suggest", "ventilate_minutes": 5, "expires_in_s": 120}), 10.0)
    h.on_feedback(env({"request_id": "v1", "verdict": "accept"}), 11.0)
    assert h.window["open"] is True and h.window["minutes"] == 5
    h.on_cmd(cmd_env("a1", "desk_lamp", "set_power", "on", at=20.0), at=20.0)
    assert h.undoable(50.0)["command_id"] == "a1" and h.undoable(81.0) is None   # 60 s 되돌리기 창


def test_request_headline_uses_chosen_arm():
    c = dv.ControlTracker()
    c.on_message(dv.TOPIC_REQUEST, env({"request_id": "r", "kind": "env_suggest", "cause": "environment",
                                        "arm": "fan_on", "expires_in_s": 30}), at=1.0)
    assert c.view(None, now=2.0)["headline"] == "제안 카드 · 환경 → Pico 2 W 선풍기 (BLE) 켬"


# ----------------------------------------------------------------- 앱 화면 문구(Pi 5 intervention.dart·dashboard_view.dart)

def test_app_wording_matches_pi5():
    ph = {"cause": "environment", "env_flags": ["co2_high", "too_dark"], "co2_ppm": 1180.0, "lux": 20.0}
    assert dv.intervention_reason(ph) == "CO₂가 높아요 (1180 ppm) · 조명이 어두워요 (20 lx)"
    assert dv.intervention_reason({"cause": "posture"}) == "움직임이 줄고 졸음 신호가 보여요"
    assert dv.auto_action_title("ACTION_POSTURE") == "자세를 바꿀 때라고 알렸어요"
    assert dv.suggestion_title({"kind": "posture_suggest", "cause": "posture"}, {}) == "자세를 가볍게 바로잡아볼까요?"
    assert dv.suggestion_title({"kind": "env_suggest", "cause": "environment"}, ph) == "잠깐 환기해 볼까요?"
    assert dv.suggestion_title({"kind": "vent_suggest", "ventilate_minutes": 15}, {}) == "창문을 15분 열어 주세요"
    assert dv.suggestion_title({"kind": "checkin_suggest"}, {}) == "지금 피곤하세요?"
    assert dv.suggestion_buttons({"kind": "checkin_suggest"}) == {"accept": "예", "reject": "아니오"}
    assert dv.suggestion_buttons({"kind": "break_suggest"}) == {"accept": "적용할게요", "reject": "괜찮아요"}
    assert dv.suggestion_body({"kind": "break_suggest", "cause": "cognitive"}, {}) == "이유: 작업 시간이 길어지고 입력 리듬이 느려졌어요"
    assert dv.suggestion_body({"kind": "env_suggest", "explanation": "CO₂ 높음"}, {}) == "CO₂ 높음"


def test_virtual_notices_and_auto_notice():
    h = dv.VirtualHome()
    h.on_phase(phase_env("FOCUS_PC", "focus"), 1.0)
    h.on_phase(phase_env("ACTION_POSTURE", "fatigue", "auto", cause="posture"), 2.0)
    texts = [n["text"] for n in h.notices]
    assert "무드등 단계 조명(focus) 모름→110" in texts and any(t.startswith("스피커 켜짐 · 음악 재생") for t in texts)
    h.on_cmd(cmd_env("c1", "desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153}, at=3.0), at=3.0)
    h.on_cmd(cmd_env("c2", "vent_fan", "set_power", "on", at=3.5), at=3.5)      # 같은 개입 → 한 알림에
    v = h.view(10.0)
    assert v["auto"]["title"] == "자세를 바꿀 때라고 알렸어요" and v["auto"]["reason"] == "움직임이 줄고 졸음 신호가 보여요"
    assert v["auto"]["effects"] == ["iLink 무드등 밝기 +60 % (최소 153) (180→255)", "Pico 2 W 선풍기 켬"]
    assert h.view(3.0 + dv.AUTO_NOTICE_S + 1)["auto"] is None
    assert v["home"] == "잠시 조정이 필요해요" and v["notices"][0]["text"] == "선풍기 켬"
    h.on_request(env({"request_id": "r", "kind": "checkin_suggest", "expires_in_s": 120}), 20.0)
    card = h.view(21.0)["card"]
    assert card["title"] == "지금 피곤하세요?" and card["buttons"]["accept"] == "예"
    assert h.notices[-1]["text"] == "제안 카드 — 지금 피곤하세요?"


# ----------------------------------------------------------------- 무드등 올릴 시점

import os as _os

# demo-b(bd47052)에서 무드등 판단에 쓰는 값만 옮긴 최소 설정 — load_hub_rules 가 hub 처럼 base 위에 프로파일을 얹는지 본다
FSM_YAML = """
weights:
  fatigue:
    npc: {keystroke: 0.00, posture: 0.40, respiration: 0.25, environment: 0.20, elapsed: 0.15}
thresholds: {fatigue_focus_low: 0.40, fatigue_suspect_low: 0.40, fatigue_confirm: 0.70, natural_recovery: 0.30}
gate: {conf_auto: 0.75, conf_suggest: 0.45}
timers: {score_period_sec: 10, fatigue_elevated_hold_sec: 180, fatigue_confirm_hold_sec: 180, fatigue_hold_min_frac: 1.0}
routing: {environment: [environment], posture: [posture, respiration], cognitive: [elapsed, keystroke]}
"""
INGEST_YAML = """
tof:
  min_confidence: 0.3
  min_confidence_by_label: {}
  posture_delta: {upright: 0.0, recline: 0.6, face_down: 1.0, drowsy: 1.0, chin_rest: 0.6}
"""
CONTROL_YAML = """
cooldown_sec: 300
actions:
  posture:
    - {target_id: desk_lamp, operation: adjust_brightness, value: {percent: 60, min_raw: 153}, undo: previous}
"""
PROFILE_YAML = """
fsm:
  weights:
    fatigue:
      npc: {keystroke: 0.00, posture: 0.80, respiration: 0.0, environment: 0.10, elapsed: 0.10}
  timers: {fatigue_elevated_hold_sec: 20, fatigue_confirm_hold_sec: 20, fatigue_hold_min_frac: 0.8}
  gate: {conf_auto: 0.55}
  thresholds: {fatigue_confirm: 0.60}
ingest:
  tof:
    min_confidence_by_label: {face_down: 0.99, drowsy: 0.80}
"""


@pytest.fixture()
def hub_cfg(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "profiles").mkdir()
    for name, text in (("fsm.yaml", FSM_YAML), ("ingest.yaml", INGEST_YAML), ("control.yaml", CONTROL_YAML),
                       ("profiles/demo-b.yaml", PROFILE_YAML)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    return str(tmp_path)


def test_load_hub_rules_merges_profile(hub_cfg):
    r = dv.load_hub_rules(hub_cfg, "demo-b")
    assert r["error"] is None
    assert (r["suspect"], r["confirm"], r["natural"], r["elevated_hold"], r["confirm_hold"], r["hold_frac"]) == \
        (0.40, 0.60, 0.30, 20, 20, 0.8)
    assert (r["conf_auto"], r["conf_suggest"], r["cooldown"], r["tick"]) == (0.55, 0.45, 300, 10)
    assert r["floors"] == {"face_down": 0.99, "drowsy": 0.80} and r["weights"]["npc"]["posture"] == 0.80
    assert r["posture_actions"][0]["value"] == {"percent": 60, "min_raw": 153}
    base = dv.load_hub_rules(hub_cfg, "")
    assert base["confirm"] == 0.70 and base["elevated_hold"] == 180   # 프로파일 없으면 보드 기본
    assert dv.load_hub_rules(hub_cfg + "/nope", "demo-b")["error"]     # 못 읽으면 숫자 대신 오류


def test_load_hub_rules_reads_repo_config():
    """레포의 실제 hub config(이 브랜치의 main 기준)를 읽을 수 있는지 — 숫자를 박지 않았다는 확인."""
    pytest.importorskip("yaml")
    cfg = _os.path.normpath(_os.path.join(_os.path.dirname(dv.__file__), "..", "..", "..", "deskmate_hub", "config"))
    if not _os.path.exists(_os.path.join(cfg, "fsm.yaml")):
        pytest.skip("레포 밖에서 돌림")
    r = dv.load_hub_rules(cfg, "")
    assert r["error"] is None and r["suspect"] is not None and r["tick"] == 10


def _run(hub_cfg, kind, seconds):
    rules = dv.load_hub_rules(hub_cfg, "demo-b")
    st = dv.Store()
    st.trigger = dv.TriggerTracker(rules)
    sc = dv.FakeHubScenario(st, kind, rules)
    base = 1_000_000.0
    real = dv.time.time
    seen = []
    try:
        for i in range(int(seconds * 10)):
            t = i * 0.1
            dv.time.time = lambda t=t: base + t
            sc.step(t)
            if i % 50 == 0 and st.trigger.ticks:
                v = st.trigger.view(base + t)
                seen.append((t, st.trigger.ticks[-1]["state"], v.get("eta_s"), v["headline"]))
    finally:
        dv.time.time = real
    return st, seen, base


def test_trigger_facedown_counts_down_then_fires(hub_cfg):
    st, seen, base = _run(hub_cfg, "facedown", 100)
    lamp = [m for m in st.trigger.marks if m["kind"] == "lamp"]
    assert len(lamp) == 1
    fired = lamp[0]["at"] - base
    # 엎드림을 처음 본 tick 부터 예상 시각이 실제 명령 시각과 같다(추정이 hub 규칙과 맞음)
    preds = [round(t + e) for t, _, e, _ in seen if e is not None]
    assert preds and all(p == round(fired) for p in preds)
    # ad76ff0: 자세 개입에서 무엇을 할지는 개인화가 고른다(결정 없으면 기본 무드등)
    assert any("초 더 이어지면 자세 개입 — 개인화가 고름(" in h and "결정 없으면 무드등 +60 % (최소 153)) (자동)" in h
               for *_, h in seen)
    assert any(h.startswith("무드등 올림") for *_, h in seen)
    assert {m["kind"] for m in st.trigger.marks} >= {"confirm", "lamp", "action_auto"}
    v = st.trigger.view(base + 100)
    assert any(c["name"].startswith("쿨다운") and not c["ok"] for c in v["checks"])   # 명령 뒤 300 s 쿨다운
    assert all(0 <= p["c"] <= 1 for p in v["series"])


def test_trigger_recline_stays_suspect(hub_cfg):
    st, seen, base = _run(hub_cfg, "recline", 150)
    assert not [m for m in st.trigger.marks if m["kind"] == "lamp"]
    assert seen[-1][1] == "FATIGUE_SUSPECT" and seen[-1][3] == "올리지 않음: 피로 0.49 < 0.6 (의심 유지)"
    v = st.trigger.view(base + 150)
    check = {c["name"]: c for c in v["checks"]}
    assert check["피로도 ≥ 의심 0.4"]["ok"] and not check["피로도 ≥ 확정 0.6"]["ok"]
    assert check["hub 가 센 자세"]["value"].startswith("젖힘 0.86")


def test_trigger_floor_blocks_low_confidence_posture(hub_cfg):
    rules = dv.load_hub_rules(hub_cfg, "demo-b")
    tr = dv.TriggerTracker(rules)
    tr.on_phase(env({"fsm_state": "FOCUS_NPC", "context": "npc", "c_fatigue": 0.2,
                     "sensor_summary": {"tof": {"posture": "drowsy", "posture_confidence": 0.55}}}), at=10.0)
    v = tr.view(11.0)
    row = {c["name"]: c for c in v["checks"]}["hub 가 센 자세"]
    assert row["ok"] is False and row["value"].endswith("(하한 0.8) → δ 0.0")
    assert v["headline"] == "올리지 않음: 피로 0.20 < 0.4"


def test_trigger_without_config_says_so():
    v = dv.TriggerTracker({"error": "FileNotFoundError: fsm.yaml", "source": "/x"}).view(0.0)
    assert v["error"].startswith("FileNotFoundError")


# ----------------------------------------------------------------- 두 개인화·첫 설정 문구·신호별 몫

BOARD_PERSONALIZE = {"signal": {"kind": "profile", "text_ko": "첫 설정 반영: 밝은 조명 선호 → 밝은 조명 먼저 · 소리 작게 → 알림음·음성 덜 고름",
                                "ts": 1.0}, "active_profile_id": "pbf0"}


def test_two_personalizations_and_prior_text():
    ph = env({"fsm_state": "FATIGUE_SUSPECT", "sensor_summary": {
        "personalize": BOARD_PERSONALIZE, "personalization": {"mode": "observe", "status": "unavailable", "decision_source": "fsm"}}})
    v = dv.phase_view(ph)
    assert v["stage2"] == "mode observe · status unavailable · decision_source fsm"
    assert v["prior"] == "밝은 조명 선호 → 밝은 조명 먼저 · 소리 작게 → 알림음·음성 덜 고름"
    assert v["explanation"].startswith("개입 결정 없음 · 기준: 밝은 조명 선호")
    dec = dv.phase_view(env({"sensor_summary": {"personalize": {"arm": "wake_light", "arm_label": "무드등 밝혀 깨우기",
                                                                "explanation": "졸음 4회", "source": "bandit", "delivered_gate": "auto"}}}))
    assert dec["explanation"] == "졸음 4회 (bandit · auto)"
    st = dv.Store()
    st.on_mqtt(dv.TOPIC_PHASE, json.dumps(ph, ensure_ascii=False).encode())
    assert st.persona_view()["prior"].startswith("밝은 조명 선호")


def test_breakdown_from_live_shape(hub_cfg):
    """10-26 실보드 tick: 바른자세(δ 0)인데 피로 0.48 — 끄덕임 0.017 Hz 가 0.3 으로 잡히고, 학습기가 c_fsm 0.535 → 0.485."""
    rules = dv.load_hub_rules(hub_cfg, "demo-b")
    rules.update(nod_full=0.05, nod_max=0.3, rule_drowsy=0.3,
                 weights={"pc": {"keystroke": 0.25, "posture": 0.60, "respiration": 0.0, "environment": 0.075, "elapsed": 0.075}})
    tr = dv.TriggerTracker(rules)
    tr.on_phase(env({"fsm_state": "FATIGUE_SUSPECT", "context": "pc", "c_fatigue": 0.485, "sensor_summary": {
        "tof": {"posture": "upright", "posture_confidence": 0.65, "nod_rate_hz": 0.017, "drowsy": False},
        "env_flags": ["too_hot", "too_dark"], "keystroke": {"typing_active": True},
        "learning": {"c_fsm": 0.535, "c_adj": 0.485, "alpha": 0.3}, "personalize": BOARD_PERSONALIZE}}), at=100.0)
    v = tr.view(101.0)
    b = v["breakdown"]
    post = b["rows"][0]
    assert post["delta"] == 0.3 and post["share"] == 0.18 and post["note"].startswith("끄덕임")
    assert b["sum_w"] == 1.0 and b["c_fsm"] == 0.535 and b["rest"] == 0.355 and b["c_adj"] == 0.485
    rows = {r["key"]: r for r in b["rows"]}
    assert rows["keystroke"]["share_max"] == 0.25 and rows["environment"]["note"] == "환경 경고 too_hot, too_dark"
    assert rows["respiration"]["share_max"] == 0.0 and b["mismatch"] is False
    assert v["prior"].startswith("밝은 조명 선호") and any(m["kind"] == "prior" for m in v["marks"])


# ----------------------------------------------------------------- 노트북 대역 기기(가짜 백엔드 — 진짜 밝기·볼륨은 안 건드림)

class FakeLaptop:
    def __init__(self, brightness=43, volume=0.41):
        self.b, self.v, self.calls, self.sounds, self.spoken = brightness, volume, [], [], []

    def get_brightness(self):
        return self.b

    def set_brightness(self, n):
        self.calls.append(("b", n))
        self.b = n

    def get_volume(self):
        return self.v

    def set_volume(self, x):
        self.calls.append(("v", round(x, 3)))
        self.v = x

    def chime(self, kind):
        self.sounds.append(kind)

    def speak(self, text):
        if getattr(self, "no_voice", False):
            raise OSError("no korean voice")
        self.spoken.append(text)
        return "Microsoft Heami Desktop"


def _laptop_store():
    st = dv.Store()
    fake = FakeLaptop()
    act = dv.LaptopActuator(st, fake)
    st.laptop = act
    act.start(thread=False)  # 스레드 대신 tick() 을 직접 부른다
    return st, act, fake


def test_laptop_lamp_to_brightness_with_rate_limit_and_restore():
    st, act, fake = _laptop_store()
    assert act.orig == {"brightness": 43, "volume": 0.41}
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "FOCUS_PC", "phase": "focus"}), at=1.0)   # 단계 조명 110
    act.tick(10.0)
    assert fake.calls == [("b", 43)] or fake.b == 43            # 110/255 = 43 % → 이미 43 이라 쓰지 않음
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "c1", "target_id": "desk_lamp", "operation": "adjust_brightness",
                                      "value": {"percent": 60, "min_raw": 153}, "gate": "auto", "requires_confirmation": False,
                                      "expires_ts_ms": int((time.time() + 15) * 1000)}), at=time.time())
    act.tick(20.0)
    assert fake.b == 69                                         # 176/255 = 69 %
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "END", "phase": "end"}), at=time.time())   # 꺼짐 → 10 %(까맣게 안 함)
    act.tick(21.0)
    assert fake.b == 69                                         # 3 s 안 → 아직
    act.tick(23.5)
    assert fake.b == dv.LAMP_OFF_PERCENT
    texts = [e["text"] for e in st.virtual.timeline if e["kind"] == "laptop"]
    assert any("노트북 화면 밝기 43 % → 69 %" in t for t in texts)
    act.restore()
    assert fake.b == 43 and fake.v == 0.41 and act.active is False
    act.restore()                                               # 두 번 불러도 한 번
    assert fake.calls.count(("b", 43)) == 1


def test_laptop_original_values_survive_a_kill(tmp_path):
    f = str(tmp_path / "orig.json")
    st = dv.Store()
    fake = FakeLaptop(brightness=43, volume=0.41)
    a1 = dv.LaptopActuator(st, fake, f)
    a1.start(thread=False)
    fake.b, fake.v = 10, 0.8                                    # 바꾼 채로 강제 종료(restore 없음)
    st2 = dv.Store()
    a2 = dv.LaptopActuator(st2, fake, f)
    a2.start(thread=False)
    assert a2.orig == {"brightness": 43, "volume": 0.41}        # 지난번 원래 값을 그대로 쓴다
    a2.restore()
    assert (fake.b, fake.v) == (43, 0.41) and not _os.path.exists(f)


def test_laptop_speaker_to_volume_by_sound_pref():
    st, act, fake = _laptop_store()
    st.profile_sent = {"profile_id": "p", "label": "x", "at": 0.0, "data": {"profile_id": "p", "sound_pref": "mid"}}
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "ACTION_BREAK", "phase": "fatigue", "gate": "auto"}), at=1.0)
    act.tick(5.0)
    assert fake.v == 0.5                                        # 음악 재생(깨우기 아님) → sound_pref mid
    time.sleep(0.05)
    assert "music" in fake.sounds
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "RECOVERY", "phase": "recovery"}), at=2.0)
    act.tick(10.0)
    assert fake.v == 0.41                                       # 음악 정지 → 원래 볼륨
    st.profile_sent["data"]["sound_pref"] = "off"
    st.virtual.speaker["music"] = False
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "ACTION_POSTURE", "phase": "fatigue", "gate": "auto"}), at=3.0)
    act.tick(20.0)
    assert fake.v == 0.41                                       # 소리 싫음(off) → 올리지 않음
    assert act.wake is None                                     # 자세 개입이어도 off 면 깨우기 음량도 안 올림


def test_laptop_manual_change_logged_not_published():
    st, act, fake = _laptop_store()
    st.publisher = FakePub()
    fake.b = 25                                                 # 사용자가 직접 낮춤
    act.tick(100.0)
    texts = [e["text"] for e in st.virtual.timeline if e["kind"] == "laptop"]
    assert any("노트북 화면 밝기를 바꿈 43 → 25 %" in t and "보고하지 않음" in t for t in texts)
    assert st.publisher.sent == []                              # manual_adjust·결과 둘 다 안 보냄
    assert act.view()["result_policy"].startswith("결과 안 보냄")


def test_fan_view_from_command():
    h = dv.VirtualHome()
    h.on_cmd(cmd_env("f1", "vent_fan", "set_level", 3, cause="environment", origin="ACTION_ENV"), at=100.0)
    f = h.view(101.0)["fan"]
    assert f["power"] is False and f["level"] == 3                  # 풍량만 바꿨지 켜지는 않음(앱과 같음)
    h.on_cmd(cmd_env("f2", "vent_fan", "set_power", "on", cause="environment", origin="ACTION_ENV"), at=100.0)
    f = h.view(101.0)["fan"]
    assert f["power"] is True and f["why"]["cause"] == "환경" and f["why"]["gate"] == "자동"
    h2 = dv.VirtualHome()
    h2.on_phase(phase_env("ACTION_ENV", "fatigue", "auto", arm="wake_light", explanation="무드등 설명"), 1.0)
    h2.on_request(env({"request_id": "r", "kind": "env_suggest", "arm": "fan_on", "explanation": "CO₂ 오름 — 선풍기?",
                       "expires_in_s": 30}), 2.0)
    h2.on_feedback(env({"request_id": "r", "verdict": "accept"}), 3.0)
    h2.on_cmd(cmd_env("f3", "vent_fan", "set_power", "on", gate="suggest", cause="environment", origin="ACTION_ENV", at=4.0), at=4.0)
    w = h2.view(5.0)["fan"]["why"]
    assert w["reason"] == "CO₂ 오름 — 선풍기?"   # 무드등을 고른 설명이 아니라 선풍기 카드의 설명


# ----------------------------------------------------------------- 휴식(REST) 배너·말하기

def test_rest_notice_banner_and_countdown():
    h = dv.VirtualHome()
    h.rest_min_s = 60
    h.on_phase(phase_env("REST", "recovery"), 100.0)                  # 알림이 먼저 안 와도 배너는 뜬다
    v = h.rest_view(101.0)
    assert v["source"] == "phase" and v["label"] == "hub 알림 없음 (구버전)" and v["left_s"] == 59
    h.on_request(env({"request_id": "r1", "kind": "rest_notice", "message": "잠깐 쉬어 가요", "rest_sec": 300,
                      "reason": "작업이 길어졌어요"}), 102.0)
    v = h.rest_view(110.0)
    assert v["source"] == "notice" and v["message"] == "잠깐 쉬어 가요" and v["left_s"] == 290   # REST 진입 시각부터
    assert v["reason"] == "작업이 길어졌어요" and h.pending_card(110.0) is None                    # 답하는 카드가 아니다
    h.on_phase(phase_env("RECOVERY", "recovery"), 200.0)
    assert h.rest_view(203.0)["ended"] is True and h.rest_view(209.0) is None


def test_rest_notice_lenient_keys():
    h = dv.VirtualHome()
    h.on_request(env({"request_id": "r", "kind": "rest_notice", "text": "5분 휴식", "minutes": 5, "why": ["elapsed", "keystroke"]}), 1.0)
    v = h.rest_view(2.0)
    assert v["message"] == "5분 휴식" and v["duration_s"] == 300 and v["reason"] == "elapsed, keystroke"
    c = dv.ControlTracker()
    c.on_message(dv.TOPIC_REQUEST, env({"request_id": "r", "kind": "rest_notice", "text": "x"}), 1.0)
    assert c.request is None                                          # 제어 결론의 '제안 카드'로 안 본다


def test_speak_command_goes_to_virtual_speaker_and_laptop_tts():
    st, act, fake = _laptop_store()
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "s1", "target_id": "speaker", "operation": "speak",
                                      "value": {"text": "잠깐 쉬어 가요"}, "gate": "auto", "requires_confirmation": False,
                                      "expires_ts_ms": int((time.time() + 15) * 1000)}), at=time.time())
    assert st.virtual.speaker["voice"] == "음성 안내: 잠깐 쉬어 가요"
    act.tick(time.time() + 5)                                         # 볼륨 먼저(기본 0.3)
    assert fake.v == dv.DEFAULT_VOLUME
    deadline = time.time() + 5                                        # 볼륨을 올린 뒤(약 1.8 s) 스스로 한 번 말한다
    said = lambda: any("노트북이 말함" in e["text"] for e in list(st.virtual.timeline) if e["kind"] == "laptop")
    while not (fake.spoken and said()) and time.time() < deadline:   # 말한 뒤 타임라인 기록까지 기다린다
        time.sleep(0.1)
    assert fake.spoken == ["잠깐 쉬어 가요"]
    assert any("노트북이 말함(Microsoft Heami Desktop)" in e["text"] for e in st.virtual.timeline if e["kind"] == "laptop")
    fake.no_voice = True                                              # 한국어 목소리 없음 → 알림음
    act._chime("speak:다시")
    assert fake.sounds[-1] == "voice" and "음성 실패" in act.error


# hub f91afd8 확정 모양
F91_REST = {"request_id": "b1-42-rest", "kind": "rest_notice", "prompt_code": "rest_now", "options": ["dismiss"],
            "expires_in_s": 60, "reason": "asleep", "cause": "posture", "text": "잠깐 눈을 붙여요", "minutes": 5,
            "rest_min_sec": 60, "speak": True, "variant": "study", "evidence": ["tof_face_down"], "c_fatigue": 0.81}
F91_SPEAK = {"command_id": "b1-43-speak", "target_id": "speaker", "operation": "speak", "value": {"text": "잠깐 눈을 붙여요", "lang": "ko"},
             "origin_state": "REST", "gate": "notice", "requires_confirmation": False}


def test_rest_notice_final_shape_f91afd8():
    h = dv.VirtualHome()
    h.rest_min_s = 60
    h.on_request(env(F91_REST), 100.0)
    v = h.rest_view(110.0)
    assert v["message"] == "잠깐 눈을 붙여요" and v["duration_s"] == 300 and v["left_s"] == 290   # minutes 가 먼저
    assert v["reason"] == "졸고 있어서 바로 휴식 · study 문구" and v["label"] == "hub 휴식 알림"
    h.on_phase(env({"fsm_state": "REST", "phase": "recovery", "sensor_summary": {"rest_notice": {
        "enabled": True, "variant": "study", "counts": {"notices": 2, "speaks": 1, "reminders": 1, "suppressed": 0, "dismissed": 0},
        "last": {"ts": 1.0, "reason": "asleep", "spoken": True}, "in_rest_since": 1.0}}}), 111.0)
    assert h.rest_view(112.0)["counts"] == "알림 2 · 말함 1 · 다시 알림 1 · 생략 0 · 닫음 0"
    h.on_cmd(env(dict(F91_SPEAK, expires_ts_ms=int((113 + 15) * 1000))), 113.0)   # gate notice 도 받는다
    assert h.speaker["voice"] == "음성 안내: 잠깐 눈을 붙여요"
    e = [x for x in h.timeline if x.get("operation") == "speak"][-1]
    assert e["virtual"]["status"] == "succeeded" and e["text"].endswith("스피커 말하기 “잠깐 눈을 붙여요”")
    bad = dict(F91_SPEAK, operation="set_power", target_id="desk_lamp", value="on", command_id="x",
               expires_ts_ms=int((113 + 15) * 1000))
    assert h.on_cmd(env(bad), 113.0)["virtual"]["error_code"] == "invalid_command"   # 램프에 notice 게이트는 앱처럼 거절


def test_auto_info_card_is_a_banner_not_a_card():
    h = dv.VirtualHome()
    h.on_request(env({"request_id": "i1", "kind": "posture_auto", "auto": True, "options": ["dismiss"], "arm": "posture_card",
                      "arm_label": "자세 안내 카드", "channel": "display", "explanation": "고개가 앞으로", "text": "허리를 펴 볼까요",
                      "expires_in_s": 30}), 10.0)
    assert h.pending_card(11.0) is None
    info = h.view(12.0)["info"]
    assert info[0]["text"] == "허리를 펴 볼까요" and info[0]["arm_label"] == "자세 안내 카드" and info[0]["left_s"] == 28
    assert h.view(41.0)["info"] == []
    c = dv.ControlTracker()
    c.on_message(dv.TOPIC_REQUEST, env({"request_id": "i1", "auto": True, "text": "x"}), 10.0)
    assert c.request is None


def test_dismiss_rest_notice_and_info_card():
    st = dv.Store()
    st.publisher = FakePub()
    now = time.time()
    st.on_mqtt(dv.TOPIC_REQUEST, json.dumps(env(F91_REST), ensure_ascii=False).encode(), at=now - 3)
    st.on_mqtt(dv.TOPIC_REQUEST, payload({"request_id": "i1", "auto": True, "text": "허리를 펴 볼까요", "expires_in_s": 30}), at=now - 1)
    assert st.send_feedback("nope", "dismiss")["ok"] is False
    assert st.send_feedback("b1-42-rest", "dismiss") == {"ok": True, "error": None}
    topic, data = st.publisher.sent[-1]
    assert topic == dv.TOPIC_FEEDBACK and data["request_id"] == "b1-42-rest" and data["verdict"] == "dismiss"
    assert 2500 <= data["response_ms"] <= 10000
    assert st.snapshot()["virtual"]["rest"]["dismissed"] is True
    assert st.send_feedback("b1-42-rest", "dismiss")["ok"] is False          # 두 번은 안 보낸다
    assert st.send_feedback("i1", "dismiss")["ok"] is True
    assert st.snapshot()["virtual"]["info"] == []                             # 닫은 안내는 띠에서 빠진다


# ----------------------------------------------------------------- 깨우기: 노트북 음악 음량
def _b(e):
    return json.dumps(e, ensure_ascii=False).encode()


def _laptop_lines(st):
    return [e["text"] for e in st.virtual.timeline if e["kind"] == "laptop"]


def test_wake_raises_volume_once_and_restores_on_recovery():
    st, act, fake = _laptop_store()
    now = time.time()
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "ACTION_POSTURE", "phase": "fatigue", "gate": "auto", "cause": "posture"}), at=now)
    st.profile_sent = {"data": {"sound_pref": "low"}}                     # 첫 설정 '작게'여도 깨우기는 크게
    st.on_mqtt(dv.TOPIC_CMD, _b(cmd_env("w1", "desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153}, at=now)),
               at=now)
    act.tick(now + 5)
    assert fake.v == 0.8
    assert [x for x in _laptop_lines(st) if x.startswith("깨우기:")] == ["깨우기: 노트북 음량 41 % → 80 % (자세 개입)"]
    st.on_mqtt(dv.TOPIC_CMD, _b(cmd_env("w2", "speaker", "chime", {"pattern": "double"}, gate="notice", at=now + 6)),
               at=now + 6)                                                 # 다시 와도 쌓지 않는다
    act.tick(now + 10)
    assert fake.v == 0.8 and act.wake["before"] == 0.41
    time.sleep(0.2)
    assert fake.sounds == []                                               # 음악 위에 삑 소리 없음
    assert any("알림음 생략" in x for x in _laptop_lines(st))
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "MONITOR", "phase": "fatigue"}), at=now + 20)
    act.tick(now + 21)
    assert fake.v == 0.8
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "RECOVERY", "phase": "recovery"}), at=now + 30)
    act.tick(now + 31)
    assert fake.v == 0.41 and act.wake is None
    assert "깨우기 끝: 노트북 음량 80 % → 41 % (회복)" in _laptop_lines(st)
    assert act.view()["wake"] is None


def test_wake_keeps_louder_volume_and_times_out():
    st, act, fake = _laptop_store()
    fake.v = act.cur["volume"] = 0.9
    now = time.time()
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "ACTION_POSTURE", "phase": "fatigue", "gate": "auto"}), at=now)
    st.on_mqtt(dv.TOPIC_CMD, _b(cmd_env("w1", "desk_lamp", "adjust_brightness", {"percent": 60}, at=now)), at=now)
    act.tick(now + 5)
    assert fake.v == 0.9 and not any(c[0] == "v" for c in fake.calls)    # 이미 더 크면 그대로(max)
    act.tick(now + dv.WAKE_MAX_S + 1)                                      # 회복이 안 와도 5분 뒤 되돌림(같은 값)
    assert act.wake is None


def test_rest_speak_wakes_and_still_talks_but_non_wake_chime_keeps_volume():
    st, act, fake = _laptop_store()
    now = time.time()
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "REST", "phase": "recovery"}), at=now)
    st.on_mqtt(dv.TOPIC_CMD, payload(dict(F91_SPEAK, expires_ts_ms=int((now + 15) * 1000))), at=now)
    act.tick(now + 2)
    assert fake.v == 0.8 and act.wake["why"] == "휴식 말하기"
    deadline = time.time() + 4
    while not fake.spoken and time.time() < deadline:
        time.sleep(0.05)
    assert fake.spoken == ["잠깐 눈을 붙여요"]                              # 말하기는 그대로
    fake.no_voice = True
    act._chime("speak:다시")
    assert fake.sounds == []                                               # TTS 실패여도 깨우는 중엔 삑 없음
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "FOCUS_PC", "phase": "focus"}), at=now + 60)
    act.tick(now + 62)
    assert fake.v == 0.41
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "n1", "target_id": "speaker", "operation": "chime",
                                      "value": {"pattern": "single"}, "gate": "notice", "requires_confirmation": False,
                                      "expires_ts_ms": int((now + 80) * 1000)}), at=now + 64)
    deadline = time.time() + 3
    while "single" not in fake.sounds and time.time() < deadline:
        time.sleep(0.05)
    assert "single" in fake.sounds and act.wake is None and fake.v == 0.41  # 깨우기 아닌 알림음은 지금 음량으로


def test_wake_disabled_keeps_sound_pref_volume():
    st = dv.Store()
    fake = FakeLaptop()
    act = dv.LaptopActuator(st, fake, wake_volume=None)
    st.laptop = act
    act.start(thread=False)
    now = time.time()
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "ACTION_POSTURE", "phase": "fatigue", "gate": "auto"}), at=now)
    st.on_mqtt(dv.TOPIC_CMD, _b(cmd_env("w1", "desk_lamp", "adjust_brightness", {"percent": 60}, at=now)), at=now)
    act.tick(now + 5)
    assert act.wake is None and fake.v == dv.DEFAULT_VOLUME


def test_fake_facedown_wakes_then_restores(hub_cfg):
    rules = dv.load_hub_rules(hub_cfg, "demo-b")
    st = dv.Store()
    fake = FakeLaptop()
    act = dv.LaptopActuator(st, fake)
    st.laptop = act
    act.start(thread=False)
    sc = dv.FakeHubScenario(st, "facedown", rules)
    base, real = 1_000_000.0, dv.time.time
    seen = []
    try:
        for i in range(0, 1300):
            t = i * 0.1
            dv.time.time = lambda t=t: base + t
            sc.step(t)
            act.tick(base + t)
            seen.append(fake.v)
    finally:
        dv.time.time = real
    lines = _laptop_lines(st)
    assert any(x.startswith("깨우기: 노트북 음량 41 % → 80 %") for x in lines)
    assert any(x.startswith("깨우기 끝: 노트북 음량 80 % → 41 %") for x in lines)
    assert max(seen) == 0.8 and seen[-1] == 0.41


def test_chime_command_plays_laptop_sound():
    """ad76ff0: chime {pattern: single|double|triple, volume: low|mid|high} — 볼륨을 잠깐 바꿔 울리고 되돌린다."""
    st, act, fake = _laptop_store()
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "c9", "target_id": "speaker", "operation": "chime",
                                      "value": {"pattern": "double", "volume": "high"}, "gate": "notice",
                                      "requires_confirmation": False, "expires_ts_ms": int((time.time() + 15) * 1000)}), at=time.time())
    assert st.virtual.speaker["voice"] == "알림음 (double · high)"
    deadline = time.time() + 3
    while "double" not in fake.sounds and time.time() < deadline:
        time.sleep(0.05)
    assert "double" in fake.sounds
    time.sleep(0.05)
    assert ("v", 0.8) in fake.calls and fake.v == 0.41          # high = 0.8 로 울리고 원래 볼륨으로
    assert any(e["text"] == "노트북 알림음 double · 볼륨 80 %" for e in st.virtual.timeline if e["kind"] == "laptop")


def test_arm_notice_and_trigger_headline(hub_cfg):
    rules = dv.load_hub_rules(hub_cfg, "demo-b")
    tr = dv.TriggerTracker(rules)
    tr.on_phase(env({"fsm_state": "FATIGUE_SUSPECT", "context": "npc", "c_fatigue": 0.81, "sensor_summary": {
        "tof": {"posture": "face_down", "posture_confidence": 0.995}}}), at=100.0)
    tr.on_phase(env({"fsm_state": "FATIGUE_SUSPECT", "context": "npc", "c_fatigue": 0.81, "sensor_summary": {
        "tof": {"posture": "face_down", "posture_confidence": 0.995}}}), at=110.0)
    h = tr.view(111.0)["headline"]
    assert "자세 개입 — 개인화가 고름(무드등·음성 안내·알림음·자세 카드 중, 결정 없으면 무드등 +60 % (최소 153))" in h
    tr.on_phase(env({"fsm_state": "ACTION_POSTURE", "context": "npc", "c_fatigue": 0.81, "gate": "auto", "sensor_summary": {
        "tof": {"posture": "face_down", "posture_confidence": 0.995},
        "personalize": {"arm": "sound_cue", "arm_label": "알림음", "source": "policy", "delivered_gate": "auto"},
        "arm_notice": {"enabled": True, "counts": {"cards": 1, "speaks": 0, "chimes": 1, "dismissed": 0},
                       "last": {"ts": 120.0, "arm": "sound_cue", "channel": "sound", "gate": "auto", "delivered": ["card", "chime"]}}}}),
        at=120.0)
    v = tr.view(121.0)
    assert any(m["kind"] == "arm" and m["text"] == "개인화가 고른 것: 알림음 (sound) → 카드, 알림음" for m in v["marks"])
    assert v["arm_counts"] == "안내 1 · 음성 0 · 알림음 1 · 닫음 0"
    assert v["headline"].startswith("자세 개입 — 개인화가 고른 것: 알림음(무드등 아님)")


def test_fake_rest_scenarios(hub_cfg):
    for kind, label in (("rest", "hub 휴식 알림"), ("rest_old", "hub 알림 없음 (구버전)")):
        rules = dv.load_hub_rules(hub_cfg, "demo-b")
        rules["rest_min"] = 60
        st = dv.Store()
        st.virtual.rest_min_s = 60
        sc = dv.FakeHubScenario(st, kind, rules)
        base, real = 1_000_000.0, dv.time.time
        try:
            for i in range(0, 400):
                t = i * 0.1
                dv.time.time = lambda t=t: base + t
                sc.step(t)
            v = st.virtual.rest_view(base + 40)
        finally:
            dv.time.time = real
        assert v["label"] == label and v["left_s"] == 40
        spoke = [e for e in st.virtual.timeline if e.get("operation") == "speak"]
        assert bool(spoke) == (kind == "rest")                        # 구버전은 말하지 않는다


# ----------------------------------------------------------------- 보내기(프로필·카드 응답)

class FakePub(dv.Publisher):
    def __init__(self):
        super().__init__("test")
        self.sent = []

    def publish(self, topic, data):
        if topic not in dv.PUBLISH_TOPICS:
            raise ValueError(topic)
        self.sent.append((topic, data))
        return self.envelope(data)


def test_validate_profile_and_personas():
    import re
    for key, p in dv.PERSONAS.items():
        prof, err = dv.validate_profile({"profile_id": dv.new_profile_id(), **p["data"]})
        assert prof is not None, (key, err)
    assert dv.validate_profile({"profile_id": "bad id!"})[0] is None
    assert dv.validate_profile({"profile_id": "p1", "light_pref": "ultra"})[1] == "light_pref 값 오류"
    assert dv.validate_profile({"profile_id": "p1", "bedtime": "25:00"})[1] == "bedtime 는 HH:MM"
    assert dv.validate_profile({"profile_id": "p1", "focus_help": ["light", "dance"]})[1] == "focus_help 값 오류"
    prof, _ = dv.validate_profile({"profile_id": "p1", "name": "지우", "job": "수험생", "age_band": "10s"})
    assert prof == {"profile_id": "p1", "age_band": "10s"}   # 이름·직업 같은 식별 단서는 버린다
    assert re.fullmatch(r"p[0-9a-f]{16}", dv.new_profile_id())


def test_send_profile_and_hub_ack():
    st = dv.Store()
    st.publisher = FakePub()
    assert st.send_profile({"profile_id": "pabc", "age_band": "20s"}, "대학생") == {"ok": True, "error": None}
    assert st.publisher.sent == [(dv.TOPIC_PROFILE, {"profile_id": "pabc", "age_band": "20s"})]
    assert st.persona_view()["ack"] is False
    st.on_mqtt(dv.TOPIC_PHASE, payload({"fsm_state": "FOCUS_PC", "sensor_summary": {   # 10-26 실보드: personalize 에 실린다
        "personalize": {"active_profile_id": "pabc", "source": "prior"}}}))
    pv = st.persona_view()
    assert pv["ack"] is True and pv["hub_active"] == "pabc" and pv["hub_fields"]["source"] == "prior"
    st.publisher = None
    assert st.send_profile({"profile_id": "pabc"})["ok"] is False


def test_send_feedback_card_and_undo():
    st = dv.Store()
    st.publisher = FakePub()
    assert st.send_feedback("r9", "accept")["error"].startswith("지금 떠 있는 카드가 아님")
    now = time.time()
    st.on_mqtt(dv.TOPIC_REQUEST, payload({"request_id": "r9", "kind": "posture_suggest", "expires_in_s": 180}), at=now - 2)
    assert st.send_feedback("r9", "maybe")["ok"] is False
    assert st.send_feedback("r9", "accept")["ok"] is True
    topic, data = st.publisher.sent[-1]
    assert topic == dv.TOPIC_FEEDBACK and data["request_id"] == "r9" and data["verdict"] == "accept"
    assert data["kind"] == "posture_suggest" and 1500 <= data["response_ms"] <= 10000
    assert st.send_feedback(None, "undo")["error"].startswith("되돌릴 자동 실행이 없음")
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "a1", "target_id": "desk_lamp", "operation": "set_power",
                                      "value": "on", "gate": "auto", "requires_confirmation": False,
                                      "expires_ts_ms": int((now + 15) * 1000)}), at=now)
    assert st.send_feedback(None, "undo")["ok"] is True
    assert st.publisher.sent[-1] == (dv.TOPIC_FEEDBACK, {"request_id": "atlas-display", "verdict": "reject",
                                                          "response_ms": 0})


def test_publisher_qos1_retain_and_envelope_over_socket():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = {}

    def broker():
        c, _ = srv.accept()
        dv.read_packet(c)
        c.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
        h, body = dv.read_packet(c)
        got["h"], got["body"] = h, body
        tlen = struct.unpack("!H", body[:2])[0]
        pid = body[2 + tlen:4 + tlen]
        c.sendall(bytes([0x40, 0x02]) + pid)
        got["disc"] = dv.read_packet(c)[0]
        c.close()

    t = threading.Thread(target=broker, daemon=True)
    t.start()
    pub = dv.Publisher("127.0.0.1", port)
    pub.publish(dv.TOPIC_PROFILE, {"profile_id": "pabc", "age_band": "20s"})
    t.join(2)
    srv.close()
    assert got["h"] == 0x33                        # PUBLISH · QoS 1 · retain(profile/user)
    topic, raw, pid = dv.parse_publish(got["h"] & 0x0F, got["body"])
    envd = json.loads(raw.decode("utf-8"))
    assert topic == dv.TOPIC_PROFILE and pid == 2 and got["disc"] == 0xE0
    assert envd["node"] == "laptop-devview" and envd["data"] == {"profile_id": "pabc", "age_band": "20s"}
    assert set(envd) == {"schema_version", "ts", "node", "boot_id", "seq", "data"}
    with pytest.raises(ValueError):
        pub.publish(dv.TOPIC_CMD, {"target_id": "desk_lamp"})   # 제어 명령은 보낼 수 없다


def test_http_post_requires_header_and_routes():
    st = dv.Store()
    st.publisher = FakePub()
    server = dv.ThreadingHTTPServer(("127.0.0.1", 0), dv.make_handler(st))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body, header=True):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"X-Devview": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    try:
        assert post("/api/profile", {"data": {"profile_id": "p1"}}, header=False)[0] == 403
        assert post("/api/profile", {"data": {"profile_id": "p1", "age_band": "20s"}, "label": "대학생"}) == \
            (200, {"ok": True, "error": None})
        assert post("/api/feedback", {"request_id": "nope", "verdict": "accept"})[0] == 409
    finally:
        server.shutdown()
        server.server_close()


def test_fake_card_waits_for_my_answer():
    st = dv.Store()
    st.publisher = dv.LoopbackPublisher(st)
    sc = dv.FakeScenario(st)
    t = 0.0
    while t < 32:
        sc.step(t)
        t += 0.5
    card = st.snapshot()["virtual"]["card"]
    assert card["kind"] == "env_suggest"
    assert st.send_feedback(card["request_id"], "reject")["ok"] is True
    while t < 40:
        sc.step(t)
        t += 0.5
    log = st.snapshot()["log"]
    assert not any(e["text"].startswith("vent_fan") for e in log)   # 거절 → 선풍기 명령 없음, 가짜 앱 자동 수락도 없음
    assert sum(1 for e in log if e["kind"] == "FEEDBACK") == 1


# ----------------------------------------------------------------- 로컬 HTTP

def test_tof_panels_in_sensor_view_order():
    """Pi 5 앱 PR #83 과 같은 순서: 격자가 사진 방향이라 B(사용자 오른쪽)를 화면 왼쪽, A(사용자 왼쪽)를 화면 오른쪽."""
    import os
    page = open(os.path.join(os.path.dirname(dv.__file__), "index.html"), encoding="utf-8").read()
    assert page.index('id="tof1"') < page.index('id="tof0"') and page.index('id="vtof1"') < page.index('id="vtof0"')
    assert "센서가 보는 방향 — 사용자 왼쪽 센서 A 가 화면 오른쪽" in page
    assert dv.SENSOR_NAMES == {0: "A · 왼쪽", 1: "B · 오른쪽"}          # 이름표는 사용자 기준 그대로


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


# ----------------------------------------------------------------- 자리 비움: 점수 없음
def test_away_ticks_show_no_score_and_skip_trigger():
    away = {"fsm_state": "IDLE", "phase": "idle", "c_fatigue": 0.0, "c_focus": 0.0, "scores_valid": False,
            "sensor_summary": {"present": False}}
    v = dv.phase_view(env(away))
    assert v["away"] is True and v["fatigue"] is None and v["focus"] is None
    old = dict(away, c_fatigue=1.0, c_focus=1.0)            # 예전 hub: present false 인데 1.0
    old.pop("scores_valid")
    assert dv.phase_view(env(old))["fatigue"] is None
    assert dv.video_view(env(away), None, {})["away"] is True
    here = {"fsm_state": "FOCUS_NPC", "phase": "focus", "c_fatigue": 0.3, "c_focus": 0.1, "scores_valid": True,
            "sensor_summary": {"present": True}}
    assert dv.phase_view(env(here))["fatigue"] == 0.3 and dv.phase_view(env(here))["away"] is False
    tr = dv.TriggerTracker({"suspect": 0.4, "confirm": 0.6, "focus_low": 0.4, "tick": 10})
    tr.on_phase(env(here), 100.0)
    for i in range(3):
        tr.on_phase(env(away), 110.0 + i * 10)
    assert len(tr.ticks) == 1                               # 그래프·유지 판정에 안 들어감
    view = tr.view(135.0)
    assert view["away"] is True and view["headline"].startswith("자리 비움 — 점수 없음") and view["eta_s"] is None
    assert [p["c"] for p in view["series"]] == [0.3]
    tr.on_phase(env(here), 140.0)
    assert len(tr.ticks) == 2 and tr.view(141.0)["away"] is False


def test_seated_away_is_masked_like_hub():
    """hub away_bottom_row_mm 과 같게: 격자 가장 아랫줄에 70 cm 보다 가까운 칸이 있으면 away 대신 1위 자세(10-27)."""
    svc = {"valid": True, "posture": "away", "conf": 0.9, "posture_ko": "자리비움",
           "probs": {"upright": 0.02, "recline": 0.04, "away": 0.9, "misplaced": 0.03, "drowsy": 0.01}}

    def raw(bottom, status=5):
        far = {"id": 1, "have": True, "w": 8, "h": 8, "dist_mm": [1500] * 64, "status": [5] * 64}
        return {"sensors": [{"id": 0, "have": True, "w": 8, "h": 8, "dist_mm": [1500] * 56 + [bottom] * 8,
                             "status": [5] * 56 + [status] * 8}, far]}
    v = dv.posture_view(dv.mask_seated_away(svc, raw(620)))
    assert v["posture"] == "recline" and v["away_masked"] is True and v["conf"] == 0.04
    for r in (None, raw(700), raw(950), raw(-1), raw(500, status=255)):          # far 거나 격자 없음 → away 그대로
        v = dv.posture_view(dv.mask_seated_away(svc, r))
        assert v["posture"] == "away" and v["away_masked"] is False
    up = dict(svc, posture="upright")
    assert dv.mask_seated_away(up, raw(500))["posture"] == "upright"
    fd = dict(svc, posture="face_down", conf=0.99)
    v = dv.posture_view(dv.mask_seated_away(fd, raw(870)))
    assert v["posture"] == "away" and v["away_forced"] is True
    assert dv.mask_seated_away(fd, raw(200))["posture"] == "face_down"
    dead = {"sensors": [{"id": 0, "have": True, "w": 8, "h": 8, "dist_mm": [-1] * 64, "status": [255] * 64}]}
    assert dv.mask_seated_away(fd, dead)["posture"] == "face_down"


# ----------------------------------------------------------------- 제어 진행(10-31)

def test_progress_from_mqtt_when_hub_log_missing():
    st = dv.Store()
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "c1", "target_id": "desk_lamp", "operation": "adjust_brightness",
                                      "value": {"percent": 60}, "origin_state": "ACTION_POSTURE", "cause": "posture",
                                      "gate": "auto"}), at=100.0)
    st.on_mqtt(dv.TOPIC_RESULT, payload({"command_id": "c1", "status": "succeeded", "previous": 110, "actual": 176},
                                        node="display-control"), at=100.8)
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "c2", "target_id": "vent_fan", "operation": "set_power",
                                      "value": "on", "gate": "auto"}), at=101.0)
    st.on_mqtt(dv.TOPIC_RESULT, payload({"command_id": "zz", "status": "failed"}), at=101.5)     # 못 본 명령의 결과
    v = st.progress.view(now=110.0)
    assert v["source"] == "mqtt" and [r["id"] for r in v["rows"]] == ["c2", "c1"]
    lamp = v["rows"][1]
    assert (lamp["status"], lamp["latency_ms"], lamp["value"], lamp["previous"], lamp["actual"], lamp["node"]) == \
        ("succeeded", 800, "+60%", 110, 176, "display-control")
    assert v["rows"][0]["status"] == "executing"
    late = st.progress.view(now=116.5)["rows"][0]
    assert late["status"] == "timeout" and "추정" in late["reason"]


def test_progress_prefers_fresh_hub_log():
    st = dv.Store()
    st.on_mqtt(dv.TOPIC_CMD, payload({"command_id": "m1", "target_id": "speaker", "operation": "speak",
                                      "value": {"text": "쉬어요", "lang": "ko"}}), at=50.0)
    hub_rows = [{"command_id": "h1", "source": "dispatcher", "created_ts": 40.0, "state": "ACTION_ENV",
                 "cause": "environment", "gate": "auto", "target_id": "vent_fan", "operation": "set_power",
                 "value": "on", "status": "skipped", "reason": "cooldown 40s/300s"},
                {"command_id": "h2", "source": "dispatcher", "created_ts": 45.0, "target_id": "desk_lamp",
                 "operation": "set_brightness", "value": 100, "status": "succeeded", "latency_ms": 320,
                 "undo": True, "undo_of": "abcdef123456"}]
    st.set_control_log(hub_rows, at=60.0)
    v = st.progress.view(now=62.0)
    assert v["source"] == "hub" and [r["id"] for r in v["rows"]] == ["h2", "h1"]
    assert v["rows"][1]["reason"] == "cooldown 40s/300s" and v["rows"][0]["undo_of"] == "abcdef"
    assert v["counts"] == {"skipped": 1, "succeeded": 1}
    fallback = st.progress.view(now=70.0)                                     # 5 s 넘게 안 오면 MQTT 로 돌아간다
    assert fallback["source"] == "mqtt" and fallback["rows"][0]["value"] == "“쉬어요”"
    with pytest.raises(ValueError):
        st.set_control_log({"error": "not_found"})
    snap = st.snapshot()
    assert "progress" in snap and snap["progress"]["hub_link"]["ok"] is False
