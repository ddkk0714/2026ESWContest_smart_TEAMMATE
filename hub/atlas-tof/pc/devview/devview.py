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


def _vital(value: Any, valid: Any, unit: str) -> str:
    """심박·호흡. *_valid 가 false 면 숫자를 그대로 믿지 않게 '(무효)' 를 붙인다(락온 풀림 ≠ 사람 없음)."""
    if value is None:
        return DASH
    text = _txt(value, "{:.0f}", unit)
    return text if valid is not False else f"{text} (무효)"


def phase_view(envelope: Any) -> dict[str, Any]:
    """`deskmate/state/phase` envelope → 화면 데이터. 키가 없으면 '-' (숫자 막대는 None)."""
    data = _map(_map(envelope).get("data"))
    ss = _map(data.get("sensor_summary"))
    tof = _map(ss.get("tof"))
    mm = _map(ss.get("mmwave"))
    ks = _map(ss.get("keystroke"))
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
    keystroke = DASH
    if ks:
        keystroke = " · ".join([
            "타이핑 중" if ks.get("typing_active") else "타이핑 없음",
            f"공백 {_txt(_num(ks.get('idle_ratio')), '{:.2f}')}",
            f"수정 {_txt(_num(ks.get('correction_rate')), '{:.2f}')}",
            f"간격 {_txt(_num(ks.get('flight_mean_ms')), '{:.0f}', ' ms')}",
        ])
    env_flags = ss.get("env_flags")

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
        "mmwave": [
            ["재실", _txt(mm.get("present"))],
            ["움직임", DASH if not mm else f"{_txt(mm.get('motion_state'))} · 세기 {_txt(mm.get('motion_level'))}"],
            ["거리", _txt(_num(mm.get("distance_cm")), "{:.0f}", " cm")],
            ["심박", _vital(_num(mm.get("heart_bpm")), mm.get("heart_valid"), " bpm")],
            ["호흡", _vital(_num(mm.get("resp_bpm")), mm.get("resp_valid"), " 회/분")],
            ["졸음 상태", _txt(mm.get("drowsy_state"))],
        ],
        "env": [
            ["CO₂", _txt(_num(ss.get("co2_ppm")), "{:.0f}", " ppm")],
            ["온도", _txt(_num(ss.get("temp_c")), "{:.1f}", " ℃")],
            ["습도", _txt(_num(ss.get("humidity_pct")), "{:.0f}", " %")],
            ["조도", _txt(_num(ss.get("lux")), "{:.0f}", " lx")],
            ["환경 경고", ", ".join(env_flags) if isinstance(env_flags, list) and env_flags else DASH],
        ],
        "keystroke": keystroke,
    }


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
                "mark": mark, "ok": ok}

    def lines(self) -> list[dict[str, Any]]:
        return [self.line(d) for d in self.devices.values()]


# --------------------------------------------------------------------------- 상태 저장소

class Store:
    """받은 최신값을 메모리에만 둔다. 모든 쓰기는 lock 안에서."""

    def __init__(self, log_max: int = 200) -> None:
        self.lock = threading.Lock()
        self.phase: Any = None
        self.posture: Any = None
        self.raw: Any = None
        self.health_hub: Any = None
        self.log: deque[dict[str, Any]] = deque(maxlen=log_max)
        self.board = DeviceBoard()
        # 연결별 상태: ok, reason, last(마지막 수신 epoch)
        self.links: dict[str, dict[str, Any]] = {
            k: {"ok": False, "reason": "아직 연결 시도 전", "last": None} for k in ("mqtt", "posture", "raw")}
        self.mode = "board"
        self.target = ""

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
            if env is None:
                return
            if topic == TOPIC_PHASE:
                self.phase = env
            elif topic == TOPIC_HEALTH_HUB:
                self.health_hub = env
            entry = log_entry(topic, env, at)
            if entry is not None:
                self.log.append(entry)
            if topic in (TOPIC_CMD, TOPIC_RESULT):
                self.board.on_message(topic, env)

    def set_posture(self, j: Any) -> None:
        with self.lock:
            self.posture = j

    def set_raw(self, j: Any) -> None:
        with self.lock:
            self.raw = j

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = time.time()
            links = {k: {**v, "age_s": None if v["last"] is None else round(now - v["last"], 1)}
                     for k, v in self.links.items()}
            return {
                "mode": self.mode,
                "target": self.target,
                "now": time.strftime("%H:%M:%S"),
                "links": links,
                "phase": phase_view(self.phase),
                "posture": posture_view(self.posture) if self.posture is not None else None,
                "raw": parse_raw(self.raw),
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

def fetch_json(url: str, timeout: float = 1.5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
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
    27 s 에 무드등 adjust_brightness +60% 명령, 28 s 에 결과(110→176), 33 s 제안 카드, 35 s 피드백.
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
        s = self.stage(t)
        return {"START": "upright", "FOCUS_PC": "upright", "FATIGUE_SUSPECT": "lean_forward"}.get(s, "drowsy")

    def grid(self, sid: int, t: float) -> list[int]:
        """머리가 천천히 좌우로 움직이고, 졸음 구간엔 앞으로 숙인다(가까워짐)."""
        lean = 120 if self._posture(t) == "drowsy" else 60 if self._posture(t) == "lean_forward" else 0
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
        st.set_raw({"dev": "fake", "frames": int(t * 5), "type5": int(t * 10), "sent": 0, "bad": 0, "sensors": [
            {"id": i, "have": True, "age_ms": 40, "w": 8, "h": 8, "dist_mm": self.grid(i, t),
             "status": [5] * 63 + [255 if i else 5], "flip_rows": False, "flip_cols": False} for i in (0, 1)]})
        probs = {l: 0.02 for l in LABELS}
        probs[posture] = 0.82 if posture != "lean_forward" else 0.64
        if posture == "lean_forward":
            probs["upright"] = 0.2
        total = sum(probs.values())
        probs = {k: v / total for k, v in probs.items()}
        st.set_posture({"posture": posture, "posture_ko": POSTURE_KO[posture], "conf": probs[posture], "valid": True,
                        "present": True, "misplaced": False, "online_sensors": 2, "probs": probs,
                        "instant": {"probs": probs}, "placement": "center",
                        "model": {"file": "models/opt_l8_c256.bin", "labels": LABELS}})
        st.link("posture", True, "가짜 각본")
        st.link("raw", True, "가짜 각본")

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
                "mmwave": {"present": True, "motion_state": "still" if posture == "drowsy" else "active",
                           "motion_level": 12 if posture == "drowsy" else 48, "distance_cm": 62,
                           "heart_bpm": 71 + int(3 * math.sin(t)), "heart_valid": True,
                           "resp_bpm": 14, "resp_valid": u % 20 > 3, "drowsy_state": 1 if posture == "drowsy" else 0},
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
                "reasons": ["tof_drowsy", "mmwave_still"] if posture == "drowsy" else [],
                "sensor_summary": summary}))
        cid = f"fk{cycle:04d}lamp"
        events = [
            (27.0, "cmd", TOPIC_CMD, {"command_id": cid, "target_id": "desk_lamp", "operation": "adjust_brightness",
                                     "value": {"percent": 60}, "gate": "auto", "cause": "posture",
                                     "origin_state": "ACTION_POSTURE"}, "hub"),
            (28.0, "res", TOPIC_RESULT, {"command_id": cid, "status": "succeeded", "previous": 110, "actual": 176,
                                        "actual_value": 176}, "display-control"),
            (33.0, "req", TOPIC_REQUEST, {"request_id": f"fk{cycle:04d}req", "kind": "suggestion",
                                         "action": "break", "explanation": "졸음이 계속됨 — 5분 쉬고 환기할까요?",
                                         "arm_label": "휴식 제안", "expires_in_s": 30}, "hub"),
            (35.0, "fb", TOPIC_FEEDBACK, {"request_id": f"fk{cycle:04d}req", "verdict": "accept"}, "display"),
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
        threads += [
            threading.Thread(target=mqtt_loop, args=(h, args.mqtt_port, store, stop), daemon=True),
            threading.Thread(target=poll_loop, args=("posture", [f"http://{h}:8771/api/state"], 1.0,
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
