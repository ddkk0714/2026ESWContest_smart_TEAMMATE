"""노트북 개발자 화면 — Pi 4 의 판단·제어 상태를 녹화용으로 한 페이지에 보여 준다.

    python devview.py --pi4 172.20.10.3     # 핫스팟에서 Pi 4 에 붙는다
    python devview.py --fake                # 보드 없이 가짜 각본(녹화 연습)

브라우저로 http://127.0.0.1:8790/ 를 연다. 이 프로그램은 **보기 전용**이다:
  - Pi 4 브로커(1883)의 `deskmate/#` 를 구독만 한다(발행하지 않는다).
  - 자세 서비스 `:8771/api/state`(1 s)·`:8772/raw`(0.2 s, 없으면 `:8771/raw`) 를 GET 만 한다.
  - 받은 값은 메모리에만 두고 127.0.0.1 로만 연다. 파일로 쓰거나 다른 곳으로 보내지 않는다.
판단(FSM·개인화)과 제어는 전부 Pi 4·Pi 5 보드에서 한다.

표준 라이브러리만 쓴다(paho 등 설치 불필요). MQTT 는 3.1.1 구독에 필요한 만큼만 직접 구현했다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

HERE = os.path.dirname(os.path.abspath(__file__))

# 자세 서비스 라벨 순서(hub/atlas-tof/src/posture_net.h kLabels)와 한국어 이름(posture_view.html KO 표).
LABELS = ["upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away", "misplaced",
          "forward_head"]
POSTURE_KO = {"upright": "바른자세", "lean_forward": "상체 앞으로", "recline": "젖힘", "face_down": "엎드림",
              "chin_rest": "턱괴기", "drowsy": "졸음", "away": "자리비움", "misplaced": "위치 재조정",
              "forward_head": "거북목", "unknown": "모름"}
# VL53L8CX target_status 중 거리를 믿는 값(Pi 5 tof_raw_client.dart kTofValidStatus 와 같음).
VALID_STATUS = {5, 6, 9, 10}
SENSOR_NAMES = {0: "A · 왼쪽", 1: "B · 오른쪽"}
DEVICE_LABELS = {"desk_lamp": "무드등", "vent_fan": "선풍기", "air_purifier": "공기청정기", "plug_1": "플러그"}
KNOWN_DEVICES = ["desk_lamp", "vent_fan", "air_purifier"]

TOPIC_PHASE = "deskmate/state/phase"
TOPIC_CMD = "deskmate/control/cmd"
TOPIC_RESULT = "deskmate/control/result"
TOPIC_REQUEST = "deskmate/interaction/request"
TOPIC_FEEDBACK = "deskmate/feedback/user"
TOPIC_HEALTH_HUB = "deskmate/health/hub"
TOPIC_PROFILE = "deskmate/profile/user"

DASH = "-"


# --------------------------------------------------------------------------- 변환(순수 함수, 테스트 대상)

def decode_payload(payload: bytes) -> Any:
    """MQTT 페이로드 → JSON 값. 한글이 들어오므로 UTF-8(BOM 허용)로 읽는다. JSON 이 아니면 None."""
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def _map(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return None if math.isnan(v) else float(v)


def _txt(v: Any, fmt: str = "{}", unit: str = "") -> str:
    """값 → 화면 글자. None(키 없음) 이면 '-'."""
    if v is None:
        return DASH
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float) and fmt == "{}":
        fmt = "{:.1f}"
    try:
        return fmt.format(v) + unit
    except (ValueError, TypeError):
        return str(v) + unit


def phase_view(envelope: Any) -> dict[str, Any]:
    """`deskmate/state/phase` envelope → 판정 화면 데이터. 키가 없으면 '-' (숫자 막대는 None).

    센서 값은 여기서 다루지 않는다 — sensor_sections 가 /raw·MQTT 원값과 함께 고른다.
    """
    data = _map(_map(envelope).get("data"))
    ss = _map(data.get("sensor_summary"))
    tof = _map(ss.get("tof"))
    mm = _map(ss.get("mmwave"))
    pers = _map(ss.get("personalize")) or _map(ss.get("personalization"))
    fatigue = _num(data.get("c_fatigue", data.get("C_fatigue")))
    focus = _num(data.get("c_focus", data.get("C_focus")))

    hub_posture = DASH
    if tof.get("posture"):
        detail = [str(tof.get("posture_source") or "?")]
        if _num(tof.get("posture_confidence")) is not None:
            detail.append(f"{tof['posture_confidence']:.2f}")
        p = str(tof["posture"])
        hub_posture = f"{POSTURE_KO.get(p, p)} {p} ({', '.join(detail)})"

    present = ss.get("present")
    presence = DASH if present is None else ("있음" if present else "없음")
    if present is not None and ss.get("presence_source"):
        presence += f" (근거 {ss['presence_source']})"
    in_room = ss.get("in_room")
    place = DASH
    if present is not None or in_room is not None:
        place = "책상" if present else ("방" if in_room else "없음")

    reasons = data.get("reasons")
    explanation = pers.get("explanation") or pers.get("arm_label") or pers.get("arm")
    if not explanation and pers:
        # 기록 전용 모드처럼 설명이 없을 때: 보드 hub(c3441f4)는 personalization{mode,status,decision_source} 만 싣는다
        explanation = " · ".join(f"{k} {pers[k]}" for k in ("mode", "status", "decision_source") if pers.get(k))
    learning = _map(ss.get("learning"))
    learn = DASH
    if learning:
        bits = ["켜짐" if learning.get("enabled") else "꺼짐"]
        for k, label in (("n_labels", "라벨"), ("acc_ai", "AI 정확"), ("acc_fsm", "FSM 정확"), ("p", "p"),
                         ("pending", "대기")):
            if learning.get(k) is not None:
                v = learning[k]
                bits.append(f"{label} {v:.2f}" if isinstance(v, float) else f"{label} {raw_value(v)}")
        learn = " · ".join(bits)
    control = _map(ss.get("control"))
    episode = DASH
    if control.get("active"):
        episode = " · ".join(str(control[k]) for k in ("state", "cause", "gate", "outcome") if control.get(k))
        if control.get("awaiting_user"):
            episode += " · 사용자 응답 대기"
    elif control:
        episode = "없음"

    return {
        "fsm_state": str(data.get("fsm_state") or data.get("state") or DASH),
        "phase": str(data.get("phase") or DASH),
        "context": str(data.get("context") or DASH),
        "seq": _map(envelope).get("seq"),
        "fatigue": fatigue,
        "focus": focus,
        "fatigue_text": DASH if fatigue is None else f"{fatigue:.2f}",
        "focus_text": DASH if focus is None else f"{focus:.2f}",
        "cause": str(data.get("cause") or DASH),
        "gate": str(data.get("gate") or DASH),
        "reasons": ", ".join(map(str, reasons)) if isinstance(reasons, list) and reasons else DASH,
        "hub_posture": hub_posture,
        "presence": presence,
        "place": place,
        "mmwave_distance": _txt(_num(mm.get("distance_cm")), "{:.0f}", " cm"),
        "explanation": str(explanation) if explanation else DASH,
        "episode": episode,
        "learning": learn,
    }


def profile_text(phase_env: Any, profile_env: Any, health_env: Any, hint: str = "") -> str:
    """프로필 한 줄: hub 시연 프로파일(demo-a|b) · 개인화가 쓰는 사람 id · Pi 5 첫 설정 프로필 id.

    보드 hub(c3441f4)는 시연 프로파일을 어느 토픽·HTTP 에도 싣지 않는다 — 그땐 노트북에서 --hub-profile 로 적은
    값을 '(노트북 지정)' 표시와 함께 보여 준다(보드가 말한 값이 아니라는 뜻).
    """
    data = _map(_map(phase_env).get("data"))
    ss = _map(data.get("sensor_summary"))
    pers = _map(ss.get("personalize")) or _map(ss.get("personalization"))
    health = _map(_map(health_env).get("data")) or _map(health_env)
    parts = []
    demo = data.get("profile") or ss.get("profile") or health.get("profile")
    if demo:
        parts.append(f"hub {demo}")
    elif hint:
        parts.append(f"hub {hint} (노트북 지정)")
    if pers.get("active_profile_id"):
        parts.append(f"개인화 {pers['active_profile_id']}")
    user = _map(_map(profile_env).get("data"))
    if user.get("profile_id"):
        parts.append(f"첫 설정 {user['profile_id']}")
    return " · ".join(parts) if parts else DASH


def raw_value(v: Any) -> str:
    """원값 그대로에 가깝게: true/false/null, 소수는 3자리까지, 목록은 쉼표."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return "NaN" if math.isnan(v) else (f"{v:.3f}".rstrip("0").rstrip(".") or "0")
    if isinstance(v, list):
        return ", ".join(raw_value(x) for x in v) if v else "[]"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _flat(d: dict, prefix: str = "") -> list[list[str]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, dict) and v and len(v) <= 8:
            rows += _flat(v, f"{prefix}{k}.")
        else:
            rows.append([f"{prefix}{k}", raw_value(v)])
    return rows


_SUMMARY_ENV_KEYS = ("co2_ppm", "temp_c", "humidity_pct", "lux", "env_flags", "co2_window")


def _summary_tof(ss: dict) -> dict:
    """hub 가 쓴 ToF 요약(0x03 규칙 특징 + 자세) 뒤에 hub 가 본 자세 서비스(tof_service.*)를 붙인다."""
    out = dict(_map(ss.get("tof")))
    if _map(ss.get("tof_service")):
        out["tof_service"] = ss["tof_service"]
    return out
# (키, 화면 제목, /raw other 키, MQTT 토픽 접두어, state/phase sensor_summary 에서 꺼내는 함수)
SECTIONS: list[tuple[str, str, str | None, str | None, Callable[[dict], dict]]] = [
    ("mmwave", "mmWave C1001 · 0x20", "mmwave", "deskmate/sensor/mmwave", lambda ss: _map(ss.get("mmwave"))),
    ("env", "환경 SCD41·DHT22·BH1750 · 0x10", "env", "deskmate/sensor/env",
     lambda ss: {k: ss[k] for k in _SUMMARY_ENV_KEYS if k in ss}),
    ("tof_feature", "ToF 노드 규칙 특징 · 0x03", "tof_feature", "deskmate/sensor/tof", _summary_tof),
    ("tof_posture", "ToF 노드 자세 · 0x04", "tof_posture", None, lambda ss: {}),
    ("heartbeat", "ESP32 하트비트 · 0xF0", "heartbeat", "deskmate/health/esp32", lambda ss: {}),
    ("keystroke", "키 입력 · PC 수집기", None, "deskmate/sensor/keystroke", lambda ss: _map(ss.get("keystroke"))),
]
_HEAD_KEYS = ("have", "len", "seq", "ts_ms", "count", "age_ms")


def sensor_sections(raw: Any, topics: dict[str, dict[str, Any]], phase_env: Any, now: float) -> list[dict[str, Any]]:
    """센서마다 원값 출처를 하나 고른다: ESP32 프레임 그대로(/raw other) > MQTT 센서 토픽 > hub 요약(state/phase).

    보드 hub 는 UART 로 받은 센서를 MQTT 로 다시 내보내지 않는다 — 중계기 /raw 의 other 가 가장 원본에 가깝다.
    """
    other = _map(_map(raw).get("other"))
    ss = _map(_map(_map(phase_env).get("data")).get("sensor_summary"))
    out = []
    for key, title, other_key, prefix, from_summary in SECTIONS:
        sec: dict[str, Any] = {"key": key, "title": title, "source": DASH, "age_s": None, "meta": "", "rows": []}
        o = _map(other.get(other_key)) if other_key else {}
        hit = None
        if prefix:
            matches = [(t, v) for t, v in topics.items() if t == prefix or t.startswith(prefix + "/")]
            hit = max(matches, key=lambda tv: tv[1]["last"]) if matches else None
        if o.get("have"):
            sec["source"] = "/raw other · ESP32 프레임"
            sec["age_s"] = None if _num(o.get("age_ms")) is None else round(o["age_ms"] / 1000, 1)
            sec["meta"] = f"seq {o.get('seq', '-')} · {o.get('count', '-')}개"
            sec["rows"] = _flat({k: v for k, v in o.items() if k not in _HEAD_KEYS})
        elif hit:
            t, info = hit
            data = _map(_map(info["env"]).get("data")) or _map(info["env"])
            sec["source"] = f"MQTT {t}"
            sec["age_s"] = round(now - info["last"], 1)
            sec["meta"] = f"{info['count']}개"
            sec["rows"] = _flat({k: v for k, v in data.items() if k != "ts"})
        else:
            summary = from_summary(ss)
            if summary:
                sec["source"] = "state/phase 요약 · hub"
                sec["rows"] = _flat(summary)
        out.append(sec)
    return out


def topics_view(topics: dict[str, dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """받은 MQTT 토픽(이름순)의 개수·마지막 수신 — 실시간으로 들어오는지 보는 용도."""
    return [{"topic": t, "count": v["count"], "age_s": round(now - v["last"], 1)} for t, v in sorted(topics.items())]


FACE_DOWN = "face_down"
FACE_DOWN_GOAL = 0.99


class FaceDownTracker:
    """자세 서비스의 엎드림(face_down) 확률을 따라간다 — 실기에서 0.99 에 닿는지 보려는 것.

    smooth = /api/state probs(EMA, 서비스가 라벨로 쓰는 값), instant = instant.probs(이번 스텝 융합), A·B = 센서별 probs.
    """

    KEYS = ("smooth", "instant", "A", "B")

    def __init__(self, window_s: float = 60.0) -> None:
        self.window_s = window_s
        self.hist: deque[tuple[float, float, float]] = deque()
        self.max: dict[str, float | None] = {k: None for k in self.KEYS}
        self.current: dict[str, float | None] = {k: None for k in self.KEYS}
        self.hits = {"smooth": 0, "instant": 0}
        self.samples = 0
        self.last_hit: float | None = None

    def add(self, j: Any, now: float) -> None:
        j = _map(j)
        if not j:
            return
        cur: dict[str, float | None] = {
            "smooth": _num(_map(j.get("probs")).get(FACE_DOWN)),
            "instant": _num(_map(_map(j.get("instant")).get("probs")).get(FACE_DOWN)),
            "A": None, "B": None,
        }
        sensors = [s for s in j.get("sensors") or [] if isinstance(s, dict)]
        for name, s in zip(("A", "B"), sensors):
            if s.get("online", True):
                cur[name] = _num(_map(s.get("probs")).get(FACE_DOWN))
        self.current = cur
        self.samples += 1
        for k, v in cur.items():
            if v is not None and (self.max[k] is None or v > self.max[k]):
                self.max[k] = v
        for k in self.hits:
            if cur[k] is not None and cur[k] >= FACE_DOWN_GOAL:
                self.hits[k] += 1
                self.last_hit = now
        self.hist.append((now, cur["smooth"] or 0.0, cur["instant"] or 0.0))
        while self.hist and now - self.hist[0][0] > self.window_s:
            self.hist.popleft()

    def view(self, now: float) -> dict[str, Any]:
        recent = [s for _, s, _ in self.hist]
        return {
            "goal": FACE_DOWN_GOAL,
            "current": self.current,
            "max": self.max,
            "max_recent": max(recent) if recent else None,
            "hits": self.hits,
            "samples": self.samples,
            "last_hit": None if self.last_hit is None else time.strftime("%H:%M:%S", time.localtime(self.last_hit)),
            "spark": self.spark(now),
        }

    def spark(self, now: float, bins: int = 120) -> list[list[float]]:
        """최근 window_s 를 bins 칸으로 — 칸마다 [smooth 최대, instant 최대]."""
        out = [[0.0, 0.0] for _ in range(bins)]
        for t, s, i in self.hist:
            b = min(bins - 1, int((self.window_s - (now - t)) / self.window_s * bins))
            if 0 <= b < bins:
                out[b] = [max(out[b][0], round(s, 3)), max(out[b][1], round(i, 3))]
        return out


def parse_raw(j: Any) -> dict[str, Any]:
    """자세 서비스 `/raw` → 센서 A·B 격자. 센서가 없거나 have=false 면 grid=None."""
    j = _map(j)
    by_id = {s.get("id"): s for s in j.get("sensors") or [] if isinstance(s, dict)}
    sensors = []
    for sid in (0, 1):
        s = by_id.get(sid)
        out: dict[str, Any] = {"id": sid, "name": SENSOR_NAMES[sid], "have": False, "grid": None, "valid": None,
                               "valid_count": 0, "zone_count": 0, "min_mm": None, "age_ms": None}
        if s and s.get("have"):
            dist = [int(v) if isinstance(v, (int, float)) else -1 for v in s.get("dist_mm") or []]
            status = list(s.get("status") or [])
            status += [-1] * (len(dist) - len(status))
            valid = [d > 0 and (not isinstance(st, int) or st < 0 or st in VALID_STATUS)
                     for d, st in zip(dist, status)]
            good = [d for d, ok in zip(dist, valid) if ok]
            out.update(have=True, grid=dist, valid=valid, valid_count=len(good), zone_count=len(dist),
                       min_mm=min(good) if good else None, age_ms=s.get("age_ms"),
                       w=s.get("w", 8), h=s.get("h", 8))
        sensors.append(out)
    return {"frames": j.get("frames"), "bad": j.get("bad"), "sensors": sensors}


def posture_view(j: Any) -> dict[str, Any]:
    """자세 서비스 `/api/state` → 라벨·신뢰도·9 클래스 막대·장치 위치."""
    j = _map(j)
    probs = _map(_map(j.get("instant")).get("probs")) or _map(j.get("probs"))
    labels = _map(j.get("model")).get("labels") or LABELS
    p = str(j.get("posture") or "unknown")
    placement = j.get("placement")
    if isinstance(placement, dict):
        placement = placement.get("current") or placement.get("placement")
    return {
        "valid": bool(j.get("valid")),
        "posture": p,
        "label": str(j.get("posture_ko") or POSTURE_KO.get(p, p)),
        "conf": _num(j.get("conf")),
        "online_sensors": j.get("online_sensors"),
        "misplaced": bool(j.get("misplaced")),
        "placement": placement if placement in ("left", "center", "right") else None,
        "probs": [[l, POSTURE_KO.get(l, l), _num(probs.get(l)) or 0.0] for l in labels],
        "model": str(_map(j.get("model")).get("file") or DASH).replace("\\", "/").split("/")[-1],
    }


def _short(v: Any) -> str:
    t = DASH if v is None else str(v)
    return t[:6]


def _clip(t: str, n: int) -> str:
    return t if len(t) <= n else t[:n] + "…"


def _value_text(op: str, value: Any) -> str:
    if value is None:
        return ""
    if op == "adjust_brightness" and isinstance(value, dict) and _num(value.get("percent")) is not None:
        p = value["percent"]
        return f" {'+' if p >= 0 else ''}{round(p)}%"
    return f" {json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}"


def log_entry(topic: str, envelope: Any, at: float) -> dict[str, Any] | None:
    """명령 로그 한 줄(Pi 5 command_log.dart 와 같은 모양). 로그 대상이 아니면 None."""
    data = _map(_map(envelope).get("data"))
    if not data:
        return None
    node = _map(envelope).get("node")
    ok = None
    if topic == TOPIC_CMD:
        op = str(data.get("operation") or "?")
        text = (f"{data.get('target_id') or '?'} {op}{_value_text(op, data.get('value'))} "
                f"({data.get('gate') or '?'}, id {_short(data.get('command_id'))})")
        if data.get("cause"):
            text += f" {data['cause']}"
        kind = "CMD"
    elif topic == TOPIC_RESULT:
        status = str(data.get("status") or "?")
        if data.get("error_code") is not None:
            detail = f" {data['error_code']}"
        elif data.get("previous") is not None and data.get("actual") is not None:
            detail = f" {data['previous']}→{data['actual']}"
        elif data.get("actual_value") is not None:
            detail = f" {data['actual_value']}"
        else:
            detail = ""
        text = f"{_short(data.get('command_id'))} {status}{detail}" + (f" ({node})" if node else "")
        ok = True if status == "succeeded" else False if status in ("failed", "timeout", "cancelled") else None
        kind = "RESULT"
    elif topic == TOPIC_REQUEST:
        parts = [str(data.get("kind") or "?")]
        action = data.get("action") or data.get("prompt_code")
        if action:
            parts.append(str(action))
        expl = str(data.get("explanation") or "").strip()
        if expl:
            parts.append(f'"{_clip(expl, 60)}"')
        arm = data.get("arm_label") or data.get("arm")
        if arm:
            parts.append(f"arm={arm}")
        if data.get("ventilate_minutes") is not None:
            parts.append(f"{data['ventilate_minutes']} 분")
        if data.get("expires_in_s") is not None:
            parts.append(f"{data['expires_in_s']} s")
        text, kind = " ".join(parts), "REQUEST"
    elif topic == TOPIC_FEEDBACK:
        if data.get("kind") == "personalization":
            text = f"personalization {data.get('action') or '?'}"
        else:
            parts = [str(data.get("verdict") or "?")]
            if data.get("corrected_state"):
                parts.append(f"→ {data['corrected_state']}")
            note = str(data.get("note") or "").strip()
            if note:
                parts.append(f'"{_clip(note, 40)}"')
            if data.get("request_id"):
                parts.append(f"(req {_short(data['request_id'])})")
            text = " ".join(parts)
        kind = "FEEDBACK"
    else:
        return None
    return {"t": time.strftime("%H:%M:%S", time.localtime(at)), "kind": kind, "text": text, "ok": ok}


class DeviceBoard:
    """기기별 마지막 명령·결과(Pi 5 device_state_board.dart 와 같은 규칙). hub 가 원하는 상태이지 실측이 아니다."""

    def __init__(self) -> None:
        self.devices: dict[str, dict[str, Any]] = {t: self._blank(t) for t in KNOWN_DEVICES}
        self._cmd_target: dict[str, str] = {}

    @staticmethod
    def _blank(target: str) -> dict[str, Any]:
        return {"target": target, "values": {}, "last_id": None, "status": None, "error": None, "from": None}

    def on_message(self, topic: str, envelope: Any) -> None:
        data = _map(_map(envelope).get("data"))
        if topic == TOPIC_CMD:
            target, op = data.get("target_id"), data.get("operation")
            if not isinstance(target, str) or not target or not isinstance(op, str) or not op:
                return
            d = self.devices.setdefault(target, self._blank(target))
            d["from"] = None
            if op == "adjust_brightness":
                d["values"].pop("set_brightness", None)
                d["values"][op] = _value_text(op, data.get("value")).strip() or "?"
            else:
                d["values"].pop("adjust_brightness", None)
                d["values"][op] = data.get("value")
            d["status"] = d["error"] = None
            cid = data.get("command_id")
            if isinstance(cid, str) and cid:
                d["last_id"] = cid
                self._cmd_target[cid] = target
                while len(self._cmd_target) > 128:
                    self._cmd_target.pop(next(iter(self._cmd_target)))
        elif topic == TOPIC_RESULT:
            cid = data.get("command_id")
            d = self.devices.get(self._cmd_target.get(cid, "")) if isinstance(cid, str) else None
            if d is None or d["last_id"] != cid:  # 지난 명령의 늦은 결과는 무시
                return
            d["status"] = data.get("status")
            d["error"] = data.get("error_code")
            d["node"] = _map(envelope).get("node")
            actual = _num(data.get("actual"))
            if "adjust_brightness" in d["values"] and actual is not None:
                d["values"].pop("adjust_brightness")
                d["values"]["set_brightness"] = round(actual)
                prev = _num(data.get("previous"))
                d["from"] = None if prev is None else round(prev)

    @staticmethod
    def line(d: dict[str, Any]) -> dict[str, Any]:
        parts = []
        for op, v in d["values"].items():
            if op == "set_power":
                parts.append(str(v).upper())
            elif op == "set_level":
                parts.append(f"풍량 {v}")
            elif op == "set_brightness":
                parts.append(f"밝기 {d['from']}→{v}" if d["from"] is not None else f"밝기 {v}")
            elif op == "adjust_brightness":
                parts.append(f"밝기 {v}")
            else:
                parts.append(f"{op} {v}")
        status = d["status"]
        if not parts:
            mark, ok = "", None
        elif status == "succeeded":
            mark, ok = "✓", True
        elif status in ("failed", "timeout", "cancelled"):
            mark, ok = "✗ 제어 못함" + (f"({d['error']})" if d["error"] else ""), False
        elif status is None:
            mark, ok = "… 결과 없음", None
        else:
            mark, ok = str(status), None
        return {"name": DEVICE_LABELS.get(d["target"], d["target"]), "value": " · ".join(parts) or DASH,
                "mark": mark, "ok": ok, "node": d.get("node") if d["status"] is not None else None}

    def lines(self) -> list[dict[str, Any]]:
        return [self.line(d) for d in self.devices.values()]


# --------------------------------------------------------------------------- 키 입력 패널

TOPIC_KEYSTROKE = "deskmate/sensor/keystroke"
TOPIC_HEALTH_PC = "deskmate/health/pc-collector"
KEYSTROKE_STALE_S = 5.0   # Pi 5 keystroke_page.dart 와 같은 기준


class KeystrokeTracker:
    """PC 수집기 집계(키 값 없음, 타이밍 통계만)의 마지막 표본과 최근 60 표본 추이(Pi 5 keystroke_page.dart 규칙).

    추이에는 valid 이고 ts 가 앞 표본보다 새 것만 넣는다.
    """

    def __init__(self, keep: int = 60) -> None:
        self.last: dict[str, Any] | None = None
        self.last_ts: float | None = None
        self.received: float | None = None
        self.hist: deque[tuple[float, float, float]] = deque(maxlen=keep)
        self.health: dict[str, Any] | None = None
        self.health_at: float | None = None

    def on_sample(self, env: Any, at: float) -> None:
        data = _map(_map(env).get("data"))
        if not data:
            return
        self.last, self.received = data, at
        ts = _num(_map(env).get("ts")) or _num(data.get("ts")) or at
        self.last_ts = ts
        if data.get("valid") is False:
            return
        if self.hist and ts <= self.hist[-1][0]:
            return
        self.hist.append((ts, _num(data.get("dwell_mean_ms")) or 0.0, _num(data.get("flight_mean_ms")) or 0.0))

    def on_health(self, env: Any, at: float) -> None:
        # health/<node> 는 envelope 없이 {ts,node,status,reconnects} 로 온다(10-26 실기). data 로 감싸도 받는다.
        self.health = _map(_map(env).get("data")) or _map(env)
        self.health_at = at

    def view(self, now: float) -> dict[str, Any]:
        ks = self.last
        age = None if self.received is None else round(now - self.received, 1)
        stale = age is None or age > KEYSTROKE_STALE_S
        if ks is None:
            status = "PC 수집기 대기"
        elif stale:
            status = "수신 끊김"
        elif ks.get("valid") is False:
            status = "유효하지 않은 표본"
        elif ks.get("typing_active"):
            status = "타이핑 중"
        else:
            status = "입력 없음 · 수신 중"
        ks = ks or {}
        window = _num(ks.get("window_s"))
        count = _num(ks.get("event_count"))
        rate = None if count is None or not window else round(count * 60.0 / window, 1)
        h = self.health or {}
        return {
            "status": status,
            "ok": ks.get("valid") is not False and not stale and bool(self.last),
            "node": ks.get("node") or DASH,
            "age_s": age,
            "window_s": window,
            "dwell_mean_ms": _num(ks.get("dwell_mean_ms")), "dwell_std_ms": _num(ks.get("dwell_std_ms")),
            "flight_mean_ms": _num(ks.get("flight_mean_ms")), "flight_std_ms": _num(ks.get("flight_std_ms")),
            "flight_cv": _num(ks.get("flight_cv")), "idle_ratio": _num(ks.get("idle_ratio")),
            "correction_rate": _num(ks.get("correction_rate")), "mouse_event_rate": _num(ks.get("mouse_event_rate")),
            "event_count": count, "rate_per_min": rate,
            "valid": ks.get("valid"), "typing_active": ks.get("typing_active"), "mouse_active": ks.get("mouse_active"),
            "link": {"status": h.get("status") or DASH, "reconnects": h.get("reconnects"),
                     "age_s": None if self.health_at is None else round(now - self.health_at, 1)},
            "spark": [[round(d, 1), round(f, 1)] for _, d, f in self.hist],
        }


# --------------------------------------------------------------------------- 제어 결론

# target_id → 실제 기기(control.yaml 주석·Pi 5 실행기 기준)
DEVICE_REAL = {"desk_lamp": "iLink 무드등 (BLE)", "vent_fan": "Pico 2 W 선풍기 (BLE)",
               "air_purifier": "LG 공기청정기 (ThinQ)", "plug_1": "스마트 플러그"}
CAUSE_KO = {"posture": "자세", "environment": "환경", "cognitive": "인지", "respiration": "호흡(자세성)",
            "elapsed": "경과 시간(인지성)", "keystroke": "키 입력(인지성)"}
GATE_KO = {"auto": "자동 실행", "suggest": "제안 카드 — 수락하면 실행", "none": "무동작 — 기록만"}
ORIGIN_KO = {"ACTION_ENV": "환경 개입", "ACTION_POSTURE": "자세 개입", "ACTION_BREAK": "휴식 개입"}
# 원인 → hub 가 내보낼 명령(hub/deskmate_hub/config/control.yaml actions, 10-25 기준). 화면 예고 문구에만 쓴다 —
# 실제로 무엇을 보냈는지는 control/cmd 가 정본이다. cognitive 는 기기 명령 없이 휴식 카드.
ACTION_PLAN = {
    "posture": [("desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153})],
    "environment": [("vent_fan", "set_power", "on"), ("desk_lamp", "adjust_brightness", {"percent": 30})],
    "cognitive": [],
}
FSM_KO = {"IDLE": "대기", "START": "시작", "CONTEXT_DETECT": "맥락 판정", "FOCUS_PC": "집중", "FOCUS_MIXED": "집중",
          "FOCUS_NPC": "집중", "FOCUS_BREAK": "집중 저하", "FATIGUE_SUSPECT": "피로 의심", "FATIGUE": "피로",
          "CAUSE_ANALYSIS": "원인 분석", "MONITOR": "효과 확인", "ESCALATE": "재시도", "RECOVERY": "회복",
          "ACTION_ENV": "환경 개입", "ACTION_POSTURE": "자세 개입", "ACTION_BREAK": "휴식 개입", "REST": "휴식",
          "END": "종료"}
CONTEXT_KO = {"pc": "PC 작업", "mixed": "PC·책 혼합", "npc": "책·필기"}
RECENT_CONTROL_S = 180.0  # 이 안의 명령·제안이면 '지금 일'로 보여 준다


def op_text(op: str, value: Any) -> str:
    """명령을 사람 말로: 밝기 +60 % (최소 153) · 켬 · 풍량 2 …"""
    v = _map(value)
    if op == "adjust_brightness" and _num(v.get("percent")) is not None:
        p = v["percent"]
        text = f"밝기 {'+' if p >= 0 else ''}{round(p)} %"
        if v.get("min_raw") is not None:
            text += f" (최소 {v['min_raw']})"
        return text
    if op == "set_power":
        return {"on": "켬", "off": "끔"}.get(str(value).lower(), f"전원 {value}")
    if op == "set_brightness":
        return f"밝기 {value}"
    if op == "set_level":
        return f"풍량 {value}"
    if op == "set_mode":
        return f"모드 {value}"
    return f"{op} {raw_value(value)}".strip()


def result_text(r: dict[str, Any] | None) -> tuple[str, bool | None]:
    if not r:
        return "… 결과 대기", None
    status = r.get("status")
    if status == "succeeded":
        if r.get("previous") is not None and r.get("actual") is not None:
            return f"✓ 성공 {r['previous']}→{r['actual']}", True
        return "✓ 성공" + (f" {r['actual_value']}" if r.get("actual_value") is not None else ""), True
    if status in ("failed", "timeout", "cancelled"):
        return f"✗ {status}" + (f" {r['error_code']}" if r.get("error_code") else ""), False
    return str(status or "?"), None


class ControlTracker:
    """'어느 블루투스 기기를 왜 제어하나' — interaction/request·control/cmd·control/result·feedback/user 를 묶는다."""

    def __init__(self) -> None:
        self.cmds: deque[dict[str, Any]] = deque(maxlen=16)
        self.request: dict[str, Any] | None = None
        self.phase_reasons: list[str] = []
        self.explanation: str | None = None

    def on_phase(self, env: Any) -> None:
        data = _map(_map(env).get("data"))
        reasons = data.get("reasons")
        if isinstance(reasons, list) and reasons:
            self.phase_reasons = [str(r) for r in reasons]
        pers = _map(_map(data.get("sensor_summary")).get("personalize"))
        if pers.get("explanation"):
            self.explanation = str(pers["explanation"])

    def on_message(self, topic: str, env: Any, at: float) -> None:
        data = _map(_map(env).get("data"))
        if topic == TOPIC_CMD and data.get("target_id"):
            self.cmds.append({**data, "at": at, "result": None, "reasons": list(self.phase_reasons)})
        elif topic == TOPIC_RESULT:
            for c in reversed(self.cmds):
                if c.get("command_id") == data.get("command_id"):
                    c["result"] = {**data, "node": _map(env).get("node"), "at": at}
                    break
        elif topic == TOPIC_REQUEST:
            self.request = {**data, "at": at, "verdict": None}
        elif topic == TOPIC_FEEDBACK and self.request is not None:
            rid = data.get("request_id")
            if not rid or rid == self.request.get("request_id"):
                self.request["verdict"] = data.get("verdict")

    @staticmethod
    def _cmd_line(c: dict[str, Any]) -> dict[str, Any]:
        res, ok = result_text(c.get("result"))
        r = c.get("result") or {}
        return {"device": DEVICE_REAL.get(c.get("target_id"), str(c.get("target_id"))),
                "target": c.get("target_id"), "action": op_text(str(c.get("operation")), c.get("value")),
                "result": res, "ok": ok, "by": r.get("node"),
                "time": time.strftime("%H:%M:%S", time.localtime(c["at"])),
                "result_time": time.strftime("%H:%M:%S", time.localtime(r["at"])) if r.get("at") else None}

    def view(self, phase_env: Any, now: float) -> dict[str, Any]:
        data = _map(_map(phase_env).get("data"))
        state = str(data.get("fsm_state") or "")
        last_cmd = self.cmds[-1] if self.cmds else None
        req = self.request
        recent_cmd = last_cmd is not None and now - last_cmd["at"] <= RECENT_CONTROL_S
        recent_req = req is not None and now - req["at"] <= RECENT_CONTROL_S
        if recent_req and (not recent_cmd or req["at"] >= last_cmd["at"]):
            cause = req.get("cause")
            devices = _plan_text(cause) or "기기 제어 없음"
            verdict = {"accept": "수락 → 실행", "reject": "거절", "timeout": "응답 없음"}.get(req.get("verdict") or "",
                                                                                   "응답 대기")
            return {
                "kind": "request",
                "headline": f"제안 카드 · {CAUSE_KO.get(cause, cause or '-')} → {devices}",
                "gate": GATE_KO["suggest"], "cause": CAUSE_KO.get(cause, cause or DASH),
                "reason": str(req.get("explanation") or ", ".join(map(str, req.get("evidence") or [])) or DASH),
                "status": f"{verdict} · {req.get('expires_in_s', '-')} s 시한",
                "time": time.strftime("%H:%M:%S", time.localtime(req["at"])), "commands": [],
            }
        if recent_cmd:
            # 같은 개입(같은 origin·cause, 3 s 안)에 함께 나간 명령을 묶는다
            group = [c for c in self.cmds if c.get("origin_state") == last_cmd.get("origin_state")
                     and c.get("cause") == last_cmd.get("cause") and last_cmd["at"] - c["at"] <= 3.0]
            lines = [self._cmd_line(c) for c in group]
            cause = last_cmd.get("cause")
            gate = str(last_cmd.get("gate") or "")
            head = " + ".join(f"{l['device']} {l['action']}" for l in lines)
            reason = ", ".join(last_cmd.get("reasons") or []) or DASH
            if self.explanation:
                reason = f"{self.explanation} · {reason}" if reason != DASH else self.explanation
            oks = [l["ok"] for l in lines]
            status = ("✓ 실행됨" if all(o is True for o in oks) else "✗ 실행 안 됨" if any(o is False for o in oks)
                      else "… 결과 대기")
            return {
                "kind": "command",
                "headline": f"{'자동' if gate == 'auto' else '제안' if gate == 'suggest' else gate} · "
                            f"{CAUSE_KO.get(cause, cause or '-')} → {head}",
                "gate": GATE_KO.get(gate, gate or DASH), "cause": CAUSE_KO.get(cause, cause or DASH),
                "origin": ORIGIN_KO.get(str(last_cmd.get("origin_state")), str(last_cmd.get("origin_state") or DASH)),
                "reason": reason, "status": status,
                "time": lines[-1]["time"], "commands": lines,
            }
        return {"kind": "idle", "headline": recommendation(state, data.get("cause")), "gate": DASH,
                "cause": CAUSE_KO.get(data.get("cause"), data.get("cause") or DASH),
                "reason": ", ".join(map(str, data.get("reasons") or [])) or DASH, "status": DASH,
                "time": None, "commands": [self._cmd_line(last_cmd)] if last_cmd else []}


def _plan_text(cause: str | None) -> str:
    plan = ACTION_PLAN.get(cause or "")
    if plan is None:
        return ""
    if not plan:
        return "휴식 카드(기기 제어 없음)"
    return " + ".join(f"{DEVICE_REAL.get(t, t)} {op_text(o, v)}" for t, o, v in plan)


def recommendation(state: str, cause: str | None = None) -> str:
    """지금 명령·제안이 없을 때의 한 줄 — state/phase 와 원인별 라우팅(control.yaml)으로 다음에 무엇을 할지."""
    ko = FSM_KO.get(state, state or "-")
    if not state:
        return "hub 판정 대기"
    if state in ("IDLE", "START", "CONTEXT_DETECT", "FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC", "RECOVERY", "REST", "END"):
        return f"지금 개입 없음 — {ko} ({state})"
    if state == "FOCUS_BREAK":
        return "집중 저하 — 기기 제어 없이 화면 안내만"
    if state == "FATIGUE_SUSPECT":
        return ("피로 의심: 확정되면 원인별로 — 자세 → " + _plan_text("posture") + " · 환경 → "
                + _plan_text("environment") + " · 인지 → " + _plan_text("cognitive"))
    if state in ("FATIGUE", "CAUSE_ANALYSIS"):
        if cause in ACTION_PLAN:
            return f"피로 확정 · 원인 {CAUSE_KO.get(cause, cause)} → {_plan_text(cause)}"
        return "피로 확정 — 원인 분석 중"
    if state in ORIGIN_KO:
        c = cause or {"ACTION_ENV": "environment", "ACTION_POSTURE": "posture", "ACTION_BREAK": "cognitive"}[state]
        return f"{ORIGIN_KO[state]} 중 → {_plan_text(c)} (명령 수신 대기)"
    if state == "MONITOR":
        return "효과 확인 중(MONITOR) — 새 명령 없음"
    if state == "ESCALATE":
        return "효과 없음 — 다른 원인으로 다시 시도(제안)"
    return f"{ko} ({state})"


# --------------------------------------------------------------------------- 영상 모드

VIDEO_POSTURE_KO = {**POSTURE_KO, "drowsy": "졸음(꾸벅)", "face_down": "엎드림", "lean_forward": "앞으로 숙임"}


def video_view(phase_env: Any, posture: Any, control: dict[str, Any]) -> dict[str, Any]:
    """시뮬 영상 하단 띠와 같은 칸: 센서가 본 자세 · 허브·상태·맥락 칩 · 피로 막대 · 환경 한 줄 · 허브 결정."""
    data = _map(_map(phase_env).get("data"))
    ss = _map(data.get("sensor_summary"))
    p = _map(posture)
    label = DASH
    if p:
        key = str(p.get("posture") or "unknown")
        label = VIDEO_POSTURE_KO.get(key, key) if p.get("valid", True) else "센서 입력 없음"
    env = []
    if _num(ss.get("co2_ppm")) is not None:
        env.append(f"CO₂ {ss['co2_ppm']:.0f} ppm")
    if _num(ss.get("temp_c")) is not None:
        env.append(f"{ss['temp_c']:.1f} ℃")
    if _num(ss.get("humidity_pct")) is not None:
        env.append(f"{ss['humidity_pct']:.0f} %")
    if _num(ss.get("lux")) is not None:
        env.append(f"{ss['lux']:.0f} lx")
    state = str(data.get("fsm_state") or "")
    fatigue = _num(data.get("c_fatigue"))
    return {
        "posture": label,
        "posture_conf": _num(p.get("conf")) if p else None,
        "chips": ["허브", FSM_KO.get(state, state or DASH), CONTEXT_KO.get(str(data.get("context")), DASH)],
        "phase": str(data.get("phase") or ""),
        "fatigue": fatigue,
        "env": " · ".join(env) or DASH,
        "decision": control.get("headline") or DASH,
        "decision_status": " · ".join(x for x in (control.get("status"), control.get("time")) if x and x != DASH) or DASH,
    }


# --------------------------------------------------------------------------- 상태 저장소

class Store:
    """받은 최신값을 메모리에만 둔다. 모든 쓰기는 lock 안에서."""

    def __init__(self, log_max: int = 200) -> None:
        self.lock = threading.Lock()
        self.phase: Any = None
        self.posture: Any = None
        self.raw: Any = None
        self.health_hub: Any = None
        self.profile_user: Any = None
        self.log: deque[dict[str, Any]] = deque(maxlen=log_max)
        self.board = DeviceBoard()
        # 받은 모든 MQTT 토픽의 마지막 envelope·개수·시각(센서 원값 패널·토픽 목록). 토픽 수는 상한을 둔다.
        self.topics: dict[str, dict[str, Any]] = {}
        self.topics_max = 128
        self.face_down = FaceDownTracker()
        self.keystroke = KeystrokeTracker()
        self.control = ControlTracker()
        # 연결별 상태: ok, reason, last(마지막 수신 epoch)
        self.links: dict[str, dict[str, Any]] = {
            k: {"ok": False, "reason": "아직 연결 시도 전", "last": None} for k in ("mqtt", "posture", "raw")}
        self.mode = "board"
        self.target = ""
        self.hub_profile = ""  # --hub-profile: 보드가 싣지 않는 시연 프로파일을 노트북에서 적은 값

    def link(self, name: str, ok: bool, reason: str = "") -> None:
        with self.lock:
            l = self.links[name]
            l["ok"], l["reason"] = ok, reason
            if ok:
                l["last"] = time.time()

    def on_mqtt(self, topic: str, payload: bytes, at: float | None = None) -> None:
        env = decode_payload(payload)
        at = time.time() if at is None else at
        with self.lock:
            self.links["mqtt"]["last"] = at
            info = self.topics.get(topic)
            if info is None and len(self.topics) < self.topics_max:
                info = self.topics[topic] = {"env": None, "count": 0, "last": at}
            if info is not None:
                info["count"] += 1
                info["last"] = at
                info["env"] = env if env is not None else {"text": payload[:200].decode("utf-8", "replace")}
            if env is None:
                return
            if topic == TOPIC_PHASE:
                self.phase = env
                self.control.on_phase(env)
            elif topic == TOPIC_HEALTH_HUB:
                self.health_hub = env
            elif topic == TOPIC_PROFILE:
                self.profile_user = env
            elif topic == TOPIC_KEYSTROKE:
                self.keystroke.on_sample(env, at)
            elif topic == TOPIC_HEALTH_PC:
                self.keystroke.on_health(env, at)
            if topic in (TOPIC_CMD, TOPIC_RESULT, TOPIC_REQUEST, TOPIC_FEEDBACK):
                self.control.on_message(topic, env, at)
            entry = log_entry(topic, env, at)
            if entry is not None:
                self.log.append(entry)
            if topic in (TOPIC_CMD, TOPIC_RESULT):
                self.board.on_message(topic, env)

    def set_posture(self, j: Any, at: float | None = None) -> None:
        with self.lock:
            self.posture = j
            self.face_down.add(j, time.time() if at is None else at)

    def set_raw(self, j: Any) -> None:
        with self.lock:
            self.raw = j

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = time.time()
            links = {k: {**v, "age_s": None if v["last"] is None else round(now - v["last"], 1)}
                     for k, v in self.links.items()}
            control = self.control.view(self.phase, now)
            return {
                "control": control,
                "keystroke": self.keystroke.view(now),
                "video": video_view(self.phase, self.posture, control),
                "mode": self.mode,
                "target": self.target,
                "now": time.strftime("%H:%M:%S"),
                "links": links,
                "phase": phase_view(self.phase),
                "profile": profile_text(self.phase, self.profile_user, self.health_hub, self.hub_profile),
                "posture": posture_view(self.posture) if self.posture is not None else None,
                "face_down": self.face_down.view(now),
                "raw": parse_raw(self.raw),
                "sections": sensor_sections(self.raw, self.topics, self.phase, now),
                "topics": topics_view(self.topics, now),
                "devices": self.board.lines(),
                "log": list(self.log),
            }


# --------------------------------------------------------------------------- MQTT 3.1.1 최소 구독자

def _mqtt_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def _remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        n, digit = divmod(n, 128)
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _packet(header: int, body: bytes) -> bytes:
    return bytes([header]) + _remaining_length(len(body)) + body


def connect_packet(client_id: str, keepalive: int = 30) -> bytes:
    # 프로토콜 이름 MQTT, 레벨 4(3.1.1), 플래그 clean session(0x02)
    return _packet(0x10, _mqtt_str("MQTT") + bytes([4, 0x02]) + struct.pack("!H", keepalive) + _mqtt_str(client_id))


def subscribe_packet(topic: str, packet_id: int = 1) -> bytes:
    return _packet(0x82, struct.pack("!H", packet_id) + _mqtt_str(topic) + bytes([0]))


def parse_publish(flags: int, body: bytes) -> tuple[str, bytes, int | None]:
    """PUBLISH 본문 → (토픽, 페이로드, QoS≥1 이면 packet id). 토픽 이름도 UTF-8."""
    tlen = struct.unpack("!H", body[:2])[0]
    topic = body[2:2 + tlen].decode("utf-8", "replace")
    pos = 2 + tlen
    pid = None
    if (flags >> 1) & 0x03:
        pid = struct.unpack("!H", body[pos:pos + 2])[0]
        pos += 2
    return topic, body[pos:], pid


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("브로커가 연결을 끊음")
        buf += chunk
    return bytes(buf)


def read_packet(sock: socket.socket) -> tuple[int, bytes]:
    header = _recv_exact(sock, 1)[0]
    mult, length = 1, 0
    while True:
        b = _recv_exact(sock, 1)[0]
        length += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    return header, _recv_exact(sock, length) if length else b""


def mqtt_loop(host: str, port: int, store: Store, stop: threading.Event, topic: str = "deskmate/#") -> None:
    """구독만 하는 루프. 끊기면 2 s 뒤 다시 붙는다."""
    keepalive = 30
    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=3) as sock:
                sock.sendall(connect_packet(f"devview-{os.getpid()}", keepalive))
                h, body = read_packet(sock)
                if h >> 4 != 2 or len(body) < 2 or body[1] != 0:
                    raise ConnectionError(f"CONNACK 거부(code {body[1] if len(body) > 1 else '?'})")
                sock.sendall(subscribe_packet(topic))
                store.link("mqtt", True, f"{host}:{port} 구독 {topic}")
                sock.settimeout(1.0)
                last_ping = time.time()
                while not stop.is_set():
                    if time.time() - last_ping > keepalive / 2:
                        sock.sendall(b"\xc0\x00")  # PINGREQ
                        last_ping = time.time()
                    try:
                        h, body = read_packet(sock)
                    except socket.timeout:
                        continue
                    if h >> 4 == 3:
                        t, payload, pid = parse_publish(h & 0x0F, body)
                        if pid is not None:
                            sock.sendall(_packet(0x40, struct.pack("!H", pid)))  # PUBACK
                        store.on_mqtt(t, payload)
        except (OSError, ConnectionError, struct.error) as e:
            store.link("mqtt", False, f"{host}:{port} — {_reason(e)}")
        stop.wait(2.0)


def _reason(e: BaseException) -> str:
    if isinstance(e, socket.timeout) or "timed out" in str(e):
        return "시간 초과(핫스팟 So 에 붙어 있는지 확인)"
    if isinstance(e, ConnectionRefusedError):
        return "연결 거부(브로커·서비스가 안 떠 있음)"
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return _reason(e.reason) if isinstance(e.reason, BaseException) else str(e.reason)
    return str(e) or type(e).__name__


# --------------------------------------------------------------------------- HTTP 폴링

# 시스템 프록시를 쓰지 않는다 — 노트북 프록시 설정이 172.20.x(핫스팟) 요청을 프록시로 보내 시간 초과가 났다.
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_json(url: str, timeout: float = 1.5) -> Any:
    with _NO_PROXY.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def poll_loop(name: str, urls: list[str], period: float, setter: Callable[[Any], None], store: Store,
              stop: threading.Event, fetch: Callable[[str], Any] = fetch_json) -> None:
    """urls 를 앞에서부터 시도한다(8772/raw 가 없으면 8771/raw). 되던 주소를 계속 쓴다."""
    order = list(urls)
    while not stop.is_set():
        t0 = time.time()
        err = None
        for i, url in enumerate(order):
            try:
                setter(fetch(url))
                store.link(name, True, url)
                if i:
                    order.insert(0, order.pop(i))
                err = None
                break
            except Exception as e:  # noqa: BLE001 — 어떤 실패든 화면에 이유로 띄우고 계속 재시도
                err = f"{url} — {_reason(e)}"
        if err:
            store.link(name, False, err)
            stop.wait(1.0)
        stop.wait(max(0.0, period - (time.time() - t0)))


# --------------------------------------------------------------------------- 가짜 각본

FAKE_PERIOD = 40.0


def _fake_env(seq: int, data: dict[str, Any], node: str = "hub") -> bytes:
    env = {"schema_version": "1.0", "ts": time.time(), "node": node, "boot_id": "fake0000", "seq": seq, "data": data}
    return json.dumps(env, ensure_ascii=False).encode("utf-8")


class FakeScenario:
    """보드 없이 화면을 채우는 각본(40 s 반복). 실제 수신 경로(Store.on_mqtt·set_raw·set_posture)를 그대로 탄다.

    0–6 s START · 6–16 FOCUS_PC · 16–22 FATIGUE_SUSPECT · 22–27 FATIGUE · 27–40 ACTION_POSTURE
    27 s 에 무드등 adjust_brightness +60% 명령, 28 s 에 결과(110→176), 32 s 부터 엎드림(face_down 0.90→0.995),
    33 s 제안 카드, 35 s 피드백. /raw other(0x20·0x10·0x03·0x04·0xF0)·키 입력 토픽·프로필도 낸다.
    """

    STAGES = [(0, "START", "start"), (6, "FOCUS_PC", "focus"), (16, "FATIGUE_SUSPECT", "fatigue"),
              (22, "FATIGUE", "fatigue"), (27, "ACTION_POSTURE", "action")]

    def __init__(self, store: Store) -> None:
        self.store = store
        self.seq = 0
        self.cycle = -1
        self.fired: set[str] = set()

    def stage(self, t: float) -> str:
        name = self.STAGES[0][1]
        for start, n, _ in self.STAGES:
            if t % FAKE_PERIOD >= start:
                name = n
        return name

    def _posture(self, t: float) -> str:
        if t % FAKE_PERIOD >= 32:
            return "face_down"
        s = self.stage(t)
        return {"START": "upright", "FOCUS_PC": "upright", "FATIGUE_SUSPECT": "lean_forward"}.get(s, "drowsy")

    def probs(self, t: float, sensor: int | None = None) -> dict[str, float]:
        """지금 자세에 몰린 확률. 엎드림은 32 s 0.90 → 37 s 0.995 로 올라 0.99 선을 넘는다(센서 B 는 조금 낮게)."""
        posture, u = self._posture(t), t % FAKE_PERIOD
        if posture == "face_down":
            top = min(0.995, 0.90 + 0.019 * (u - 32)) - (0.006 if sensor == 1 else 0.0)
        else:
            top = 0.64 if posture == "lean_forward" else 0.82
        rest = (1.0 - top) / (len(LABELS) - 1)
        p = {l: rest for l in LABELS}
        p[posture] = top
        return p

    def grid(self, sid: int, t: float) -> list[int]:
        """머리가 천천히 좌우로 움직이고, 졸음 구간엔 앞으로 숙이고, 엎드리면 아주 가까워진다."""
        lean = {"drowsy": 120, "lean_forward": 60, "face_down": 420}.get(self._posture(t), 0)
        cx = 3.5 + 1.6 * math.sin(t * 0.7 + sid * 0.8) + (1 if sid == 0 else -1)
        cy = 3.0 + 0.6 * math.sin(t * 0.4) + lean / 120
        out = []
        for r in range(8):
            for c in range(8):
                d2 = (c - cx) ** 2 + (r - cy) ** 2
                mm = 1400 - 850 * math.exp(-d2 / 6.0) - lean * math.exp(-d2 / 6.0) + 25 * math.sin(t * 3 + r + c)
                out.append(int(mm))
        if sid == 1:
            out[63] = -1  # 무효 칸 하나(회색 확인용)
        return out

    def step(self, t: float) -> None:
        """시각 t(초, 각본 기준)의 상태를 store 에 넣는다. 0.2 s 마다 불러도, 띄엄띄엄 불러도 된다."""
        cycle, u = int(t // FAKE_PERIOD), t % FAKE_PERIOD
        if cycle != self.cycle:
            self.cycle, self.fired = cycle, set()
        st = self.store
        posture = self._posture(t)
        drowsy = posture in ("drowsy", "face_down")
        n = int(t * 5)
        st.set_raw({"dev": "fake", "frames": n, "type5": n * 2, "sent": 0, "bad": 0, "sensors": [
            {"id": i, "have": True, "age_ms": 40, "w": 8, "h": 8, "dist_mm": self.grid(i, t),
             "status": [5] * 63 + [255 if i else 5], "flip_rows": False, "flip_cols": False} for i in (0, 1)],
            # 중계기 /raw 의 other(relay/other_sensors.h 와 같은 키)
            "other": {
                "mmwave": {"have": True, "age_ms": 300, "seq": n // 5, "ts_ms": int(t * 1000), "count": n // 5, "len": 11,
                           "present": True, "motion_state": "still" if drowsy else "active",
                           "motion_level": 12 if drowsy else 48, "distance_cm": 62, "resp_bpm": 14,
                           "resp_valid": u % 20 > 3, "heart_bpm": 71 + int(3 * math.sin(t)), "heart_valid": True,
                           "drowsy_state": "DROWSY" if drowsy else "AWAKE", "valid": True},
                "env": {"have": True, "age_ms": 2100, "seq": n // 25, "ts_ms": int(t * 1000), "count": n // 25, "len": 9,
                        "co2_ppm": 820 + int(u * 6), "temp_c": 24.6, "humidity_pct": 41.0, "lux": 180 if u < 27 else 310,
                        "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True},
                "tof_feature": {"have": True, "age_ms": 120, "seq": n, "ts_ms": int(t * 1000), "count": n, "len": 26,
                                "present": True, "posture": "drowsy" if drowsy else "upright",
                                "confidence_pct": 70, "motion_pct": 8 if drowsy else 35,
                                "head_delta_mm": -40 if drowsy else -5, "nod_rate_hz": 0.31 if drowsy else 0.0,
                                "valid_zones": 127, "grid_w": 16, "grid_h": 8, "coverage_pct": 58,
                                "presence_count": 74, "centroid_mm": 640, "head_row_pct": 38, "shoulder_tilt_mm": 6,
                                "motion_mm_s": 14, "change_per_min": 3, "baseline_dev_pct": 21 if drowsy else 4,
                                "baseline_ready": True, "drowsy": drowsy, "geometry_valid": True,
                                "sensor_a": True, "sensor_b": True, "nods": 4 if drowsy else 0},
                "tof_posture": {"have": True, "age_ms": 120, "seq": n, "ts_ms": int(t * 1000), "count": n, "len": 4,
                                "present": True, "posture": "drowsy" if drowsy else "upright",
                                "confidence_pct": 70, "drowsy": drowsy},
                "heartbeat": {"have": True, "age_ms": 600, "seq": n // 10, "ts_ms": int(t * 1000), "count": n // 10,
                              "len": 8, "uptime_ms": int(t * 1000) + 3_600_000, "fw": "0x0125", "mmwave_ready": True,
                              "tof_ok_mask": 3, "tof_a": True, "tof_b": True},
            }})
        probs = self.probs(t)
        st.set_posture({"posture": posture, "posture_ko": POSTURE_KO[posture], "conf": probs[posture], "valid": True,
                        "present": True, "misplaced": False, "online_sensors": 2, "probs": probs,
                        "instant": {"posture": posture, "conf": probs[posture], "probs": probs}, "placement": "center",
                        "sensors": [{"name": n_, "online": True, "posture": posture, "probs": self.probs(t, i)}
                                    for i, n_ in enumerate(("A", "B"))],
                        "model": {"file": "models/opt_l8_c256.bin", "labels": LABELS}})
        st.link("posture", True, "가짜 각본")
        st.link("raw", True, "가짜 각본")

        # 바퀴마다 한 번: hub 생존(시연 프로파일)·Pi 5 첫 설정 프로필. 키 입력은 1 s 마다(PC 수집기 토픽)
        if "once" not in self.fired:
            self.fired.add("once")
            st.on_mqtt(TOPIC_HEALTH_HUB, _fake_env(self.seq, {"status": "online", "profile": "demo-b"}))
            st.on_mqtt(TOPIC_PROFILE, _fake_env(self.seq, {"profile_id": "u-7f3a", "age_band": "20s"}, "pi5-display"))
            st.on_mqtt(TOPIC_HEALTH_PC, json.dumps({"ts": time.time(), "node": "pc-collector", "status": "online",
                                                    "reconnects": 1}).encode("utf-8"))
        ks_slot = f"ks{int(u)}"
        if ks_slot not in self.fired:
            self.fired.add(ks_slot)
            typing = self.stage(t) == "FOCUS_PC"
            wob = math.sin(u * 1.3)
            st.on_mqtt(TOPIC_KEYSTROKE, _fake_env(self.seq, {
                "node": "pc-collector", "window_s": 60,
                "dwell_mean_ms": round(96.0 + 8 * wob, 1) if typing else 0.0, "dwell_std_ms": 21.0 if typing else 0.0,
                "flight_mean_ms": round(182.0 + 30 * wob, 1) if typing else 0.0, "flight_std_ms": 74.0 if typing else 0.0,
                "flight_cv": 0.41 if typing else 0.0, "idle_ratio": 0.2 if typing else 1.0,
                "correction_rate": 0.04 if typing else 0.0, "typing_active": typing, "mouse_active": typing,
                "input_active": typing, "mouse_event_rate": 30.0 if typing else 0.0,
                "event_count": 212 if typing else 0, "valid": True}, "pc-collector"))

        # state/phase 는 2 s 마다(실제는 10 s)
        slot = f"phase{int(u // 2)}"
        if slot not in self.fired:
            self.fired.add(slot)
            self.seq += 1
            state = self.stage(t)
            fatigue = min(0.95, max(0.08, (u - 6) / 20))  # FATIGUE(22 s) 에서 0.80 → 게이트 auto 구간
            focus = 0.15 if state == "FOCUS_PC" else 0.3
            cause = "posture" if state in ("FATIGUE", "ACTION_POSTURE") else None
            gate = "auto" if state == "ACTION_POSTURE" else "none"
            summary = {
                "present": True, "presence_source": "both", "in_room": True,
                "tof": {"posture": posture, "posture_confidence": round(probs[posture], 2),
                        "posture_source": "service"},
                "control": {"active": True, "state": "ACTION_POSTURE", "cause": "posture", "gate": "auto",
                            "outcome": "pending", "awaiting_user": False} if state == "ACTION_POSTURE" else {"active": False},
                "mmwave": {"present": True, "motion_state": "still" if drowsy else "active",
                           "motion_level": 12 if drowsy else 48, "distance_cm": 62,
                           "heart_bpm": 71 + int(3 * math.sin(t)), "heart_valid": True,
                           "resp_bpm": 14, "resp_valid": u % 20 > 3, "drowsy_state": 1 if drowsy else 0},
                "co2_ppm": 820 + int(u * 6), "temp_c": 24.6, "humidity_pct": 41, "lux": 180 if u < 27 else 310,
                "keystroke": {"typing_active": state == "FOCUS_PC", "idle_ratio": 0.2 if state == "FOCUS_PC" else 0.9,
                              "correction_rate": 0.04, "flight_mean_ms": 180},
                "personalize": {"arm": "lamp_up", "arm_label": "무드등 밝게",
                                "explanation": "지난 졸음 때 무드등 밝기 올림이 가장 잘 깨움(성공 4/5) → 같은 방법"}
                if state == "ACTION_POSTURE" else {},
            }
            if u >= 22:
                summary["env_flags"] = ["co2_rising"]
            st.on_mqtt(TOPIC_PHASE, _fake_env(self.seq, {
                "fsm_state": state, "phase": dict((n, p) for _, n, p in self.STAGES)[state], "context": "pc",
                "c_fatigue": round(fatigue, 3), "c_focus": focus, "gate": gate, "cause": cause,
                "reasons": ["tof_drowsy", "mmwave_still"] if drowsy else [],
                "sensor_summary": summary}))
        cid = f"fk{cycle:04d}lamp"
        events = [
            (27.0, "cmd", TOPIC_CMD, {"command_id": cid, "target_id": "desk_lamp", "operation": "adjust_brightness",
                                     "value": {"percent": 60}, "gate": "auto", "cause": "posture",
                                     "origin_state": "ACTION_POSTURE"}, "hub"),
            (28.0, "res", TOPIC_RESULT, {"command_id": cid, "status": "succeeded", "previous": 110, "actual": 176,
                                        "actual_value": 176}, "display-control"),
            (33.0, "req", TOPIC_REQUEST, {"request_id": f"fk{cycle:04d}req", "kind": "env_suggest",
                                         "prompt_code": "adjust_environment", "cause": "environment",
                                         "evidence": ["co2_rising"], "options": ["accept", "reject"],
                                         "explanation": "CO₂ 가 계속 오름 — 선풍기를 켜 환기할까요?",
                                         "arm_label": "선풍기 켜기", "expires_in_s": 30}, "hub"),
            (35.0, "fb", TOPIC_FEEDBACK, {"request_id": f"fk{cycle:04d}req", "verdict": "accept"}, "display"),
            (35.5, "cmd2", TOPIC_CMD, {"command_id": f"fk{cycle:04d}fan", "target_id": "vent_fan",
                                      "operation": "set_power", "value": "on", "gate": "suggest",
                                      "cause": "environment", "origin_state": "ACTION_ENV"}, "hub"),
            (36.0, "res2", TOPIC_RESULT, {"command_id": f"fk{cycle:04d}fan", "status": "succeeded",
                                         "actual_value": "on"}, "display-control"),
        ]
        for at, key, topic, data, node in events:
            if u >= at and key not in self.fired:
                self.fired.add(key)
                self.seq += 1
                st.on_mqtt(topic, _fake_env(self.seq, data, node))


def fake_loop(store: Store, stop: threading.Event, start: float = 0.0) -> None:
    sc = FakeScenario(store)
    store.link("mqtt", True, "가짜 각본")
    t0 = time.time() - start
    while not stop.is_set():
        sc.step(time.time() - t0)
        store.link("mqtt", True, "가짜 각본")
        stop.wait(0.2)


# --------------------------------------------------------------------------- 로컬 웹 서버

def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    page_path = os.path.join(HERE, "index.html")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(page_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/data":
                body = json.dumps(store.snapshot(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="DESKMATE 노트북 개발자 화면(보기 전용)")
    ap.add_argument("--pi4", default="172.20.10.3", help="Pi 4 주소(핫스팟 So 기준 172.20.10.3)")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--port", type=int, default=8790, help="이 화면의 로컬 포트")
    ap.add_argument("--hub-profile", default="", metavar="NAME",
                    help="hub 시연 프로파일 이름(예: demo-b). 보드가 발행하지 않아서 화면에 '(노트북 지정)' 으로 표시")
    ap.add_argument("--fake", action="store_true", help="보드 없이 가짜 각본으로")
    ap.add_argument("--fake-start", type=float, default=0.0, metavar="S",
                    help=f"가짜 각본을 S 초부터(0–{FAKE_PERIOD:.0f}, 예: 26 = 졸음→무드등 직전)")
    args = ap.parse_args(argv)

    store = Store()
    stop = threading.Event()
    threads: list[threading.Thread] = []
    if args.fake:
        store.mode, store.target = "fake", "가짜 데이터"
        threads.append(threading.Thread(target=fake_loop, args=(store, stop, args.fake_start), daemon=True))
    else:
        h = args.pi4
        store.target = h
        store.hub_profile = args.hub_profile
        threads += [
            threading.Thread(target=mqtt_loop, args=(h, args.mqtt_port, store, stop), daemon=True),
            threading.Thread(target=poll_loop, args=("posture", [f"http://{h}:8771/api/state"], 0.3,
                                                     store.set_posture, store, stop), daemon=True),
            threading.Thread(target=poll_loop, args=("raw", [f"http://{h}:8772/raw", f"http://{h}:8771/raw"], 0.2,
                                                     store.set_raw, store, stop), daemon=True),
        ]
    for t in threads:
        t.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    print(f"개발자 화면: http://127.0.0.1:{args.port}/  ({'가짜 각본' if args.fake else 'Pi 4 ' + args.pi4})  Ctrl+C 로 끝")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
