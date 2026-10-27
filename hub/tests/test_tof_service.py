"""별도 자세 서비스(deskmate_tofposture, GRU) 판정 → hub FSM posture Signal (2026-10-23).

10-23 까지 hub ingest 는 `posture_source: mlp`(hub MLP, drowsy·misplaced 없음)만 알았고 서비스 /api/state 를 읽는 곳이
없어, 실기 FSM 의 졸음 근거가 0x03 규칙 플래그뿐이었다. 여기서는 (1) /api/state JSON 파싱, (2) `TOFSVC` 줄 수신,
(3) 매핑의 service → mlp → rule 폴백, (4) 0x03 이 없어도 서비스 자세가 FSM 에 닿는지, (5) PC 용 폴러를 확인한다.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from deskmate_hub.ingest import Sample, SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary
from deskmate_hub.ingest.tof_service import (
    LINE_PREFIX, SERVICE_LABELS, TofServiceLineSource, TofServicePoller, parse_service_state, put_service_state,
    service_config, should_poll,
)
from deskmate_hub.inference import State

from test_ingest_mapping import mlp, mm, tof


@pytest.fixture
def cfg():
    c = load_ingest_config()
    assert c["tof"]["posture_source"] == "service"     # Pi 4 배포판 기본(10-23)
    return c


def state_json(*, posture="drowsy", conf=0.91, valid=True, online=2, present=None, misplaced=None, **extra):
    """hub/atlas-tof device_posture.cpp stateJson 과 같은 모양."""
    idx = SERVICE_LABELS.index(posture) if posture in SERVICE_LABELS else -1
    probs = {label: (conf if label == posture else round((1 - conf) / 8, 4)) for label in SERVICE_LABELS}
    out = {
        "schema": "deskmate/tof_posture/1", "ts_ms": 1_700_000_000_000, "valid": valid, "posture": posture,
        "posture_ko": "졸음", "index": idx, "conf": conf, "since_ms": 2400,
        "instant": {"posture": posture, "conf": conf, "probs": probs}, "probs": probs,
        "present": (valid and posture != "away") if present is None else present,
        "misplaced": (valid and posture == "misplaced") if misplaced is None else misplaced,
        "online_sensors": online, "ticks": 1234,
        "sensors": [
            {"name": "s0", "online": online >= 1, "posture": posture, "conf": conf, "probs": probs, "frames": 500,
             "steps": 500, "reused": 0, "resets": 1, "age_ms": 180, "hz": 5.0, "has_status": True,
             "flip_rows": True, "flip_cols": False, "seq_gaps": 0, "duplicates": 0, "idle_frames": 0,
             "alive": True, "stale": False, "rule": "", "hello": "", "info": "", "error": ""},
            {"name": "s1", "online": online >= 2, "posture": posture, "conf": conf, "probs": probs, "frames": 498,
             "steps": 498, "reused": 2, "resets": 1, "age_ms": 190, "hz": 4.9, "has_status": True,
             "flip_rows": True, "flip_cols": False, "seq_gaps": 1, "duplicates": 0, "idle_frames": 0,
             "alive": True, "stale": False, "rule": "", "hello": "", "info": "", "error": ""},
        ],
        "model": {"file": "models/opt_l8_c256/posture_net.bin", "classes": 9, "rate_hz": 10, "fusion": "logsoftmax_sum",
                  "flip_rows": "auto", "flip_cols": "auto", "labels": list(SERVICE_LABELS)},
    }
    out.update(extra)
    return out


def svc(now, **kw):
    return Sample(received=now, ts=now, seq=None, data=parse_service_state(state_json(**kw)))


# ── 파싱 ────────────────────────────────────────────────────────────

def test_parse_service_state_keeps_contract_fields_only():
    d = parse_service_state(state_json(posture="forward_head", conf=0.77))
    assert d["posture"] == "forward_head" and d["posture_confidence"] == 0.77 and d["valid"] is True
    assert d["present"] is True and d["misplaced"] is False and d["drowsy"] is False
    assert d["online_sensors"] == 2 and d["baseline_ready"] is True and d["source"] == "service"
    assert d["model"] == {"file": "models/opt_l8_c256/posture_net.bin", "classes": 9, "rate_hz": 10,
                          "fusion": "logsoftmax_sum"}
    assert [s["name"] for s in d["sensors"]] == ["s0", "s1"] and "probs" not in d["sensors"][0]
    assert "instant" not in d and "ticks" not in d           # 원본 전부를 싣지 않는다
    assert set(d["probs"]) == set(SERVICE_LABELS)


def test_parse_invalid_or_away_or_unknown_label():
    d = parse_service_state(state_json(posture="drowsy", valid=False))
    assert d["posture"] == "unknown" and d["present"] is False and d["drowsy"] is False and d["valid"] is False
    d = parse_service_state(state_json(posture="away"))
    assert d["posture"] == "away" and d["present"] is False
    d = parse_service_state(state_json(posture="misplaced"))
    assert d["misplaced"] is True and d["present"] is True
    d = parse_service_state(state_json(posture="head_stand"))     # 모르는 라벨은 unknown
    assert d["posture"] == "unknown"
    d = parse_service_state({"valid": True, "posture": "upright", "probs": {"upright": 0.6}})   # conf 없으면 probs 에서
    assert d["posture_confidence"] == 0.6 and d["online_sensors"] == 0 and d["baseline_ready"] is False
    with pytest.raises(ValueError):
        parse_service_state(["not", "an", "object"])  # type: ignore[arg-type]


def test_parse_passes_through_newer_service_fields():
    d = parse_service_state(state_json(presence_source="mmwave", mmwave={"present": True, "distance_cm": 70},
                                       model_output=[0.1] * 9))
    assert d["presence_source"] == "mmwave" and d["mmwave"]["distance_cm"] == 70 and len(d["model_output"]) == 9


def test_line_source_feeds_cache_and_counts_rejects():
    cache = SensorCache()
    src = TofServiceLineSource(cache)
    line = LINE_PREFIX + json.dumps(state_json(posture="chin_rest", conf=0.8), separators=(",", ":")) + "\n"
    assert src.feed_line(line, 100.0) is True
    assert cache.tof_service is not None and cache.tof_service.received == 100.0
    assert cache.tof_service.data["posture"] == "chin_rest"
    assert src.feed_line("TOFSVC\t{not json\n") is False
    assert src.feed_line("UART\t{}\n") is False
    assert src.feed_line(LINE_PREFIX + "[1,2]\n") is False
    assert (src.stats.lines, src.stats.accepted, src.stats.rejected) == (4, 1, 3)
    put_service_state(cache, state_json(posture="upright"), 101.0)
    assert cache.tof_service.data["posture"] == "upright"


# ── 매핑: 서비스 → FSM ───────────────────────────────────────────────

def test_service_verdict_overrides_rule_and_mlp(cfg):
    cache = SensorCache()
    tr = SessionTracker()
    cache.put("mmwave", mm(100.0, present=True))
    cache.put("tof", tof(100.0, posture="upright"))            # 0x03 규칙: 바른 자세
    cache.put("tof_mlp", mlp(100.0, posture="lean_forward"))   # hub MLP: 앞으로 숙임
    cache.put("tof_service", svc(100.0, posture="face_down", conf=0.9))
    f = build_frame(cache.snapshot(), 100.0, cfg, tr, fsm_state=State.MONITOR)
    assert f.signals["posture"].phi == 1.0 and f.signals["posture"].delta == 1.0
    out = sensor_summary(cache.snapshot(), 100.0, cfg, tr)
    assert out["tof"]["posture"] == "face_down" and out["tof"]["posture_source"] == "service"
    assert out["tof_service"]["posture"] == "face_down" and out["tof_service"]["online_sensors"] == 2
    assert out["tof_service"]["model"] == "posture_net.bin"


def test_service_drowsy_is_fatigue_evidence_without_0x03_flag(cfg):
    """실기 FSM 의 졸음 근거가 0x03 규칙 플래그뿐이던 공백(10-23) — 서비스 라벨 drowsy 가 delta 1.0 과 drowsy 플래그를 켠다."""
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, present=True))
    cache.put("tof", tof(100.0, posture="upright", drowsy=False))
    cache.put("tof_service", svc(100.0, posture="drowsy", conf=0.88))
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.FOCUS_PC)
    assert f.signals["posture"].delta == 1.0 and f.signals["posture"].phi == cfg["tof"]["posture_phi"]["drowsy"]
    out = sensor_summary(cache.snapshot(), 100.0, cfg)
    assert out["tof"]["drowsy"] is True and out["tof"]["posture"] == "drowsy"


def test_service_misplaced_and_forward_head_use_tables(cfg):
    cache = SensorCache()
    cache.put("tof", tof(100.0, posture="upright"))
    cache.put("tof_service", svc(100.0, posture="misplaced", conf=0.95))
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.signals["posture"].phi == pytest.approx(0.05 * cfg["tof"]["motion_phi_weight"])   # 설치 어긋남은 자세 증거 0
    assert f.signals["posture"].delta == 0.0
    assert sensor_summary(cache.snapshot(), 100.0, cfg)["tof"]["misplaced"] is True
    cache.put("tof_service", svc(100.0, posture="forward_head", conf=0.7))
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.signals["posture"].phi == pytest.approx(cfg["tof"]["posture_phi"]["forward_head"])
    assert f.signals["posture"].delta == pytest.approx(cfg["tof"]["posture_delta"]["forward_head"])


def test_service_low_confidence_is_unknown(cfg):
    cache = SensorCache()
    cache.put("tof", tof(100.0, posture="upright"))
    cache.put("tof_service", svc(100.0, posture="face_down", conf=0.2))      # min_confidence 0.3 미만
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.signals["posture"].delta == 0.0


def test_service_falls_back_to_mlp_then_rule(cfg):
    cache = SensorCache()
    tr = SessionTracker()
    cache.put("tof", tof(100.0, posture="recline"))
    cache.put("tof_mlp", mlp(100.0, posture="chin_rest"))
    # 낡은 서비스 표본 → mlp
    cache.put("tof_service", svc(90.0, posture="face_down"))
    f = build_frame(cache.snapshot(), 100.0, cfg, tr, fsm_state=State.MONITOR)
    assert f.signals["posture"].delta == pytest.approx(cfg["tof"]["posture_delta"]["chin_rest"])
    assert sensor_summary(cache.snapshot(), 100.0, cfg, tr)["tof"]["posture_source"] == "mlp"
    # valid=false → mlp
    cache.put("tof_service", svc(100.0, posture="face_down", valid=False))
    assert sensor_summary(cache.snapshot(), 100.0, cfg, tr)["tof"]["posture_source"] == "mlp"
    # 센서 0개 → mlp
    cache.put("tof_service", svc(100.0, posture="face_down", online=0))
    assert sensor_summary(cache.snapshot(), 100.0, cfg, tr)["tof"]["posture_source"] == "mlp"
    # 서비스도 mlp 도 낡음 → 규칙(0x03 recline)
    cache.put("tof_mlp", mlp(90.0, posture="chin_rest"))
    f = build_frame(cache.snapshot(), 100.0, cfg, tr, fsm_state=State.MONITOR)
    assert f.signals["posture"].delta == pytest.approx(cfg["tof"]["posture_delta"]["recline"])
    out = sensor_summary(cache.snapshot(), 100.0, cfg, tr)
    assert out["tof"]["posture_source"] == "rule"
    assert out["tof_service"]["valid"] is True and out["tof_service"]["online_sensors"] == 0   # 요약에는 그대로 보인다


def test_service_fallback_rule_skips_mlp(cfg):
    rule_cfg = json.loads(json.dumps(cfg))
    rule_cfg["tof"]["service"]["fallback"] = "rule"
    cache = SensorCache()
    cache.put("tof", tof(100.0, posture="upright"))
    cache.put("tof_mlp", mlp(100.0, posture="face_down"))
    f = build_frame(cache.snapshot(), 100.0, rule_cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.signals["posture"].delta == 0.0
    assert sensor_summary(cache.snapshot(), 100.0, rule_cfg)["tof"]["posture_source"] == "rule"


def test_posture_source_mlp_ignores_service(cfg):
    """10-17 동작 그대로: posture_source: mlp 면 서비스 표본이 있어도 보지 않는다(요약에는 남긴다)."""
    mlp_cfg = json.loads(json.dumps(cfg))
    mlp_cfg["tof"]["posture_source"] = "mlp"
    cache = SensorCache()
    cache.put("tof", tof(100.0, posture="upright"))
    cache.put("tof_mlp", mlp(100.0, posture="chin_rest"))
    cache.put("tof_service", svc(100.0, posture="face_down"))
    f = build_frame(cache.snapshot(), 100.0, mlp_cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.signals["posture"].delta == pytest.approx(cfg["tof"]["posture_delta"]["chin_rest"])
    out = sensor_summary(cache.snapshot(), 100.0, mlp_cfg)
    assert out["tof"]["posture_source"] == "mlp" and out["tof_service"]["posture"] == "face_down"


def test_service_alone_supplies_presence_and_posture_when_0x03_missing(cfg):
    """ESP32 가 0x05 만 보내거나 0x03 이 끊겨도 서비스 자세가 FSM 에 닿는다(재실도 서비스 present)."""
    cache = SensorCache()
    tr = SessionTracker()
    cache.put("tof_service", svc(100.0, posture="drowsy", conf=0.9))
    f = build_frame(cache.snapshot(), 100.0, cfg, tr, fsm_state=State.IDLE)
    assert f.present is True and f.touch is True
    assert f.signals["posture"].available and f.signals["posture"].delta == 1.0
    out = sensor_summary(cache.snapshot(), 100.0, cfg, tr)
    assert out["presence_source"] == "tof" and out["tof"]["posture_source"] == "service"
    assert out["tof"]["sensors_ok"] == 2
    # 서비스가 away 라 하면 부재
    cache.put("tof_service", svc(100.0, posture="away", conf=0.9))
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.IDLE)
    assert f.present is False and not f.signals["posture"].available


def test_mmwave_gate_still_blocks_service_posture(cfg):
    """책상이 비면(mmWave 거리 게이트) 서비스가 무엇을 보든 자세로 쓰지 않는다(10-23 재실 규칙 유지)."""
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, present=True, dist=300))          # 방엔 있지만 책상은 빔
    cache.put("tof", tof(100.0, posture="upright"))
    cache.put("tof_service", svc(100.0, posture="face_down"))
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.present is False and not f.signals["posture"].available


def svc_probs(now, probs, *, posture="away"):
    """확률을 직접 준 서비스 표본(평활 probs 의 1위가 posture)."""
    state = state_json(posture=posture, conf=probs[posture])
    state["probs"] = dict(probs)
    return Sample(received=now, ts=now, seq=None, data=parse_service_state(state))


# 10-27 실측 모양: 뒤로 젖혀 75 cm 넘게 멀어졌을 때 away 0.90, 나머지 1위는 recline
LEANED_BACK = {"upright": 0.02, "lean_forward": 0.005, "recline": 0.04, "face_down": 0.005, "chin_rest": 0.005,
               "drowsy": 0.015, "away": 0.90, "misplaced": 0.0, "forward_head": 0.01}


def test_seated_away_is_masked_when_mmwave_sees_desk(cfg):
    """mmWave 가 책상 재실이면 서비스 away 를 away·misplaced 를 뺀 1위로 — 화면 두 자세 줄 모두. FSM 증거는 그대로 0."""
    assert cfg["tof"]["service"]["mask_away_when_present"] is True
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, present=True, dist=60))
    cache.put("tof_service", svc_probs(100.0, LEANED_BACK))
    out = sensor_summary(cache.snapshot(), 100.0, cfg, SessionTracker())
    assert out["present"] is True
    assert out["tof"]["posture"] == "recline" and out["tof"]["away_masked"] is True
    assert out["tof_service"]["posture"] == "recline" and out["tof_service"]["service_posture"] == "away"
    assert out["tof_service"]["present"] is True
    assert out["tof"]["posture_confidence"] == pytest.approx(0.04)      # 원래 확률 그대로 — 다시 나눠 키우지 않는다
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.present is True
    assert f.signals["posture"].phi == 0.0 and f.signals["posture"].delta == 0.0   # min_confidence 아래 → away 와 같은 0


def test_seated_away_mask_never_makes_drowsy_evidence(cfg):
    """away 다음 1위가 drowsy 여도 확률이 낮으면 졸음 증거가 아니다(무드등 헛켜짐 방지)."""
    probs = dict(LEANED_BACK, recline=0.01, drowsy=0.06)
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, present=True, dist=60))
    cache.put("tof_service", svc_probs(100.0, probs))
    f = build_frame(cache.snapshot(), 100.0, cfg, SessionTracker(), fsm_state=State.MONITOR)
    assert f.signals["posture"].delta == 0.0


@pytest.mark.parametrize("mm_kw", [dict(present=False), dict(present=True, dist=300), dict(present=True, dist=None)])
def test_away_kept_when_mmwave_does_not_see_desk(cfg, mm_kw):
    """mmWave 가 없음·책상 밖·거리 모름이면 away 그대로(재실을 ToF 에 맡기는 경우)."""
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, **mm_kw))
    cache.put("tof_service", svc_probs(100.0, LEANED_BACK))
    out = sensor_summary(cache.snapshot(), 100.0, cfg, SessionTracker())
    assert out["tof_service"]["posture"] == "away" and "away_masked" not in out["tof_service"]


def test_away_mask_can_be_turned_off(cfg):
    cfg["tof"]["service"]["mask_away_when_present"] = False
    cache = SensorCache()
    cache.put("mmwave", mm(100.0, present=True, dist=60))
    cache.put("tof_service", svc_probs(100.0, LEANED_BACK))
    out = sensor_summary(cache.snapshot(), 100.0, cfg, SessionTracker())
    assert out["tof"]["posture"] == "away" and out["tof_service"]["posture"] == "away"


# ── 설정·폴러 ───────────────────────────────────────────────────────

def test_service_config_defaults_and_should_poll(cfg):
    scfg = service_config(cfg["tof"])
    assert scfg["url"].endswith("/api/state") and scfg["poll_s"] == 1.0 and scfg["fallback"] == "mlp"
    assert service_config(None)["min_online_sensors"] == 1
    assert should_poll(cfg["tof"], {}) is True                                   # PC: Python 폴러
    assert should_poll(cfg["tof"], {"DESKMATE_NATIVE_MQTT": "1"}) is False       # 보드: C++ 가 TOFSVC 줄로
    forced = dict(cfg["tof"], service=dict(cfg["tof"]["service"], poller="on"))
    assert should_poll(forced, {"DESKMATE_NATIVE_MQTT": "1"}) is True
    off = dict(cfg["tof"], service=dict(cfg["tof"]["service"], poller="off"))
    assert should_poll(off, {}) is False
    assert should_poll(dict(cfg["tof"], posture_source="mlp"), {}) is False


class _StateHandler(BaseHTTPRequestHandler):
    payload: dict = state_json(posture="lean_forward", conf=0.66)
    status = 200

    def do_GET(self):  # noqa: N802
        if self.path != "/api/state":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def state_server():
    server = HTTPServer(("127.0.0.1", 0), _StateHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_poller_fetches_state_and_reports_errors_once(cfg, state_server):
    cache = SensorCache()
    logs: list[str] = []
    port = state_server.server_address[1]
    tof_cfg = dict(cfg["tof"], service=dict(cfg["tof"]["service"], url=f"http://127.0.0.1:{port}/api/state"))
    poller = TofServicePoller(cache, tof_cfg, on_log=logs.append, now=lambda: 500.0)
    assert poller.poll_once() is True
    assert cache.tof_service is not None and cache.tof_service.received == 500.0
    assert cache.tof_service.data["posture"] == "lean_forward" and cache.tof_service.data["posture_confidence"] == 0.66
    assert poller.stats.ok == 1 and len(logs) == 1 and "연결" in logs[0]
    # 서비스가 죽으면 한 번만 알리고 조용히 재시도, 캐시는 그대로(낡아서 fallback 으로 넘어간다)
    dead = TofServicePoller(cache, dict(tof_cfg, service=dict(tof_cfg["service"], url="http://127.0.0.1:9/api/state",
                                                                    timeout_s=0.2)), on_log=logs.append)
    assert dead.poll_once() is False and dead.poll_once() is False
    assert dead.stats.errors == 2 and len(logs) == 2 and "응답 없음" in logs[1]
    assert cache.tof_service.data["posture"] == "lean_forward"


def test_poller_thread_runs_and_stops(cfg, state_server):
    cache = SensorCache()
    port = state_server.server_address[1]
    tof_cfg = dict(cfg["tof"], service=dict(cfg["tof"]["service"], url=f"http://127.0.0.1:{port}/api/state",
                                              poll_s=0.1))
    poller = TofServicePoller(cache, tof_cfg)
    poller.start()
    deadline = threading.Event()
    for _ in range(50):
        if poller.stats.ok >= 2:
            break
        deadline.wait(0.05)
    poller.stop()
    assert poller.stats.ok >= 2 and cache.tof_service is not None
