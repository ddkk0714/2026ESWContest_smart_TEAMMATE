"""노트북 개발자 화면 — Pi 4 의 판단·제어 상태를 녹화용으로 한 페이지에 보여 준다(+ 가상 기기 /?mode=virtual).

    python devview.py --pi4 172.20.10.3     # 핫스팟에서 Pi 4 에 붙는다
    python devview.py --fake                # 보드 없이 가짜 각본(녹화 연습)

브라우저로 http://127.0.0.1:8790/ 를 연다. 이 프로그램은 **보기 전용**이다:
  - Pi 4 브로커(1883)의 `deskmate/#` 를 구독한다. 예외: 가상 기기 화면에서 사람이 확인을 누를 때만 첫 설정 프로필
    (`deskmate/profile/user`)과 카드 응답(`deskmate/feedback/user`)을 보낸다(10-26 사용자 요청, `--read-only` 면 끔).
    제어 명령은 보내지 않는다.
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
import sys
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


AWAY_TEXT = "자리 비움 — 점수 없음"


def scores_away(data: dict[str, Any]) -> bool:
    """hub 가 사람이 없다고 한 tick: scores_valid false 또는 sensor_summary.present false. 이때 c_fatigue·c_focus 는
    점수가 아니다(전에는 환경 신호만으로 재정규화돼 1.0, 이제 0.0) — 숫자를 보이지도, 유지 판정에 세지도 않는다."""
    return data.get("scores_valid") is False or _map(data.get("sensor_summary")).get("present") is False


def phase_view(envelope: Any) -> dict[str, Any]:
    """`deskmate/state/phase` envelope → 판정 화면 데이터. 키가 없으면 '-' (숫자 막대는 None).

    센서 값은 여기서 다루지 않는다 — sensor_sections 가 /raw·MQTT 원값과 함께 고른다.
    """
    data = _map(_map(envelope).get("data"))
    ss = _map(data.get("sensor_summary"))
    tof = _map(ss.get("tof"))
    mm = _map(ss.get("mmwave"))
    # 둘은 다른 것: personalize = 개입 선택 개인화(밴딧 — 어떤 기기·카드를 고를지), personalization = 2단계 피로 관찰
    # (CNN, observe 전용 — 판정에 안 들어감. demo-b 는 linear 정규화라 unavailable 이 정상)
    pers = _map(ss.get("personalize"))
    stage2 = _map(ss.get("personalization"))
    away = scores_away(data)
    fatigue = None if away else _num(data.get("c_fatigue", data.get("C_fatigue")))
    focus = None if away else _num(data.get("c_focus", data.get("C_focus")))

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
    if explanation and (pers.get("source") or pers.get("delivered_gate")):
        explanation = f"{explanation} ({' · '.join(str(pers[k]) for k in ('source', 'delivered_gate') if pers.get(k))})"
    prior = prior_text(pers)
    if not explanation:
        # 결정 tick 이 아니면 arm 이 없다 — 그때는 첫 설정 기준(prior) 문구를
        explanation = f"개입 결정 없음 · 기준: {prior}" if prior else ""
    if not pers and _map(ss.get("personalization")).get("explanation"):
        explanation = str(ss["personalization"]["explanation"])   # 옛 hub: 설명을 personalization 에 실었다
    stage2_text = " · ".join(f"{k} {stage2[k]}" for k in ("mode", "status", "decision_source") if stage2.get(k)) or DASH
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
        "away": away,
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
        "stage2": stage2_text,
        "prior": prior or DASH,
    }


def prior_text(pers: dict[str, Any]) -> str | None:
    """personalize.signal(kind profile) 의 첫 설정 반영 문구 — '밝은 조명 선호 → 밝은 조명 먼저 · …'."""
    sig = _map(pers.get("signal"))
    if sig.get("kind") == "profile" and sig.get("text_ko"):
        t = str(sig["text_ko"])
        return t.split(":", 1)[1].strip() if t.startswith("첫 설정 반영:") else t
    return None


def profile_text(phase_env: Any, profile_env: Any, health_env: Any, hint: str = "") -> str:
    """프로필 한 줄: hub 시연 프로파일(demo-a|b) · 개인화가 쓰는 사람 id · Pi 5 첫 설정 프로필 id.

    보드 hub(c3441f4)는 시연 프로파일을 어느 토픽·HTTP 에도 싣지 않는다 — 그땐 노트북에서 --hub-profile 로 적은
    값을 '(노트북 지정)' 표시와 함께 보여 준다(보드가 말한 값이 아니라는 뜻).
    """
    data = _map(_map(phase_env).get("data"))
    ss = _map(data.get("sensor_summary"))
    pers = _map(ss.get("personalize"))
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


# hub 가 자세 라벨을 셀 때의 신뢰도 하한(hub config/profiles/demo-b.yaml ingest.tof.min_confidence_by_label, bd47052).
# 서비스가 화면에 내는 라벨은 하한 없는 EMA argmax 라, 이 아래면 화면에 떠도 hub 는 그 자세를 세지 않는다. --hub-floor 로 바꾼다.
HUB_FLOORS_DEFAULT = {"face_down": 0.99, "drowsy": 0.80}


def parse_floors(text: str) -> dict[str, float]:
    """'face_down=0.99,drowsy=0.80' → dict. 빈 문자열이면 하한 없음."""
    out: dict[str, float] = {}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = float(v)
    return out


def _rule_view(j: dict[str, Any]) -> dict[str, Any] | None:
    """e415d23 서비스의 보정 상태: 엎드림 규칙(d)·앉음→upright 치환·radial·fill·misplaced 게이트. 옛 서비스면 None."""
    r, m = _map(j.get("rule")), _map(j.get("misplaced_rule"))
    if not r and not m and "radial" not in j:
        return None
    parts = []
    if r:
        if r.get("enabled"):
            parts.append(("엎드림 규칙 " + ("켜짐 → face_down " + raw_value(r.get("conf")) if r.get("face_down") else "꺼짐"))
                         + f" (share {raw_value(r.get('share'))} · up {raw_value(r.get('up'))} · lo {raw_value(r.get('lo'))}"
                         + f" · dlo {raw_value(r.get('dlo'))} · dup {raw_value(r.get('dup'))})")
        if r.get("seated_upright"):
            parts.append("앉음→바른자세 " + ("적용 중" if r.get("seated_applied") else "대기") +
                         (" (앉음 감지)" if r.get("seated") else ""))
    if m:
        mode = m.get("mode")
        if mode in (None, "off"):
            parts.append("위치 재조정 게이트 꺼짐")
        else:
            parts.append(f"위치 재조정 게이트 {mode} · 베이스라인 {'있음(' + str(m.get('baseline_source')) + ')' if m.get('baseline') else '없음'}"
                         + f" · 장치 움직임 {'예' if m.get('device_moved') else '아니오'} · 모델 misplaced {'예' if m.get('model_misplaced') else '아니오'}"
                         + (" → 막음" if m.get("gated") else ""))
    if "radial" in j or "fill_notarget_mm" in j:
        parts.append(f"입력 radial {'켜짐' if j.get('radial') else '꺼짐'} · fill {raw_value(j.get('fill_notarget_mm'))} mm")
    mo = _map(j.get("model_output"))
    raw = None
    if mo:
        mp = _map(mo.get("probs"))
        top = sorted(mp.items(), key=lambda kv: -(_num(kv[1]) or 0))[:2]
        raw = f"{POSTURE_KO.get(str(mo.get('posture')), mo.get('posture'))} {raw_value(_num(mo.get('conf')))}" + "".join(
            f" · {POSTURE_KO.get(k, k)} {raw_value(_num(v))}" for k, v in top if k != mo.get("posture"))[:80]
    return {"lines": parts, "model_raw": raw, "face_down_rule": bool(r.get("face_down")),
            "seated_applied": bool(r.get("seated_applied")), "gated": bool(m.get("gated"))}


def hub_floor_note(posture: str, conf: float | None, floors: dict[str, float]) -> str | None:
    """화면 라벨이 hub 하한 아래면 'hub 무시 (0.54 < 0.80)'."""
    f = floors.get(posture)
    if f is None or conf is None or conf >= f:
        return None
    return f"hub 무시 ({conf:.2f} < {f:.2f})"


def posture_view(j: Any, floors: dict[str, float] | None = None) -> dict[str, Any]:
    """자세 서비스 `/api/state` → 라벨·신뢰도·9 클래스 막대·장치 위치·보정 상태·hub 하한 표시.

    막대는 서비스가 라벨로 쓰는 평활 확률(probs — 게이트·규칙을 거친 값). 모델 원 출력은 model_output 으로 따로 보인다.
    """
    j = _map(j)
    floors = HUB_FLOORS_DEFAULT if floors is None else floors
    probs = _map(j.get("probs")) or _map(_map(j.get("instant")).get("probs"))
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
        "hub_note": hub_floor_note(p, _num(j.get("conf")), floors) if j.get("valid") else None,
        "floors": floors,
        "rules": _rule_view(j),
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
DEVICE_REAL = {"desk_lamp": "iLink 무드등 (BLE)", "vent_fan": "Pico 2 W 선풍기 (BLE)", "speaker": "블루투스 스피커",
               "air_purifier": "LG 공기청정기 (ThinQ)", "plug_1": "스마트 플러그"}
CAUSE_KO = {"posture": "자세", "environment": "환경", "cognitive": "인지", "respiration": "호흡(자세성)",
            "elapsed": "경과 시간(인지성)", "keystroke": "키 입력(인지성)"}
GATE_KO = {"auto": "자동 실행", "suggest": "제안 카드 — 수락하면 실행", "none": "무동작 — 기록만", "notice": "알림(휴식·안내)"}
ORIGIN_KO = {"ACTION_ENV": "환경 개입", "ACTION_POSTURE": "자세 개입", "ACTION_BREAK": "휴식 개입"}
# 원인 → hub 가 내보낼 명령(hub/deskmate_hub/config/control.yaml actions, 10-25 기준). 화면 예고 문구에만 쓴다 —
# 실제로 무엇을 보냈는지는 control/cmd 가 정본이다. cognitive 는 기기 명령 없이 휴식 카드.
ACTION_PLAN = {
    "posture": [("desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153})],
    "environment": [("vent_fan", "set_power", "on"), ("desk_lamp", "adjust_brightness", {"percent": 30})],
    "cognitive": [],
}
# 개인화 arm → 하는 일(hub config/personalize.yaml arms 의 device·label_ko). 카드 머리줄을 원인 전체가 아니라 고른 arm 으로.
# arm id → 화면 이름(hub config/personalize.yaml arms label_ko)
ARM_LABEL = {"light_cool_bright": "조명 밝고 차갑게", "light_warm_dim": "조명 따뜻하고 어둡게", "air_fan_up": "공기청정기 풍량 올리기",
             "fan_on": "환기팬(선풍기) 켜기", "ventilate_suggest": "창문 환기", "posture_card": "자세 안내 카드",
             "wake_light": "무드등 밝혀 깨우기", "posture_voice": "자세 음성 안내", "sound_cue": "알림음",
             "break_short": "5분 휴식", "break_long": "긴 휴식", "sleep_suggest": "취침 권유"}
POSTURE_ARMS = ("wake_light", "posture_card", "posture_voice", "sound_cue")   # personalize.yaml causes 에 posture 가 있는 arm
DELIVERED_KO = {"card": "카드", "speak": "음성", "chime": "알림음", "lamp": "무드등", "desk_lamp": "무드등", "fan": "선풍기",
                "vent_fan": "선풍기", "air": "공기청정기", "cmd": "기기 명령", "display": "화면"}
ARM_ACTION = {
    "light_cool_bright": [("desk_lamp", "adjust_brightness", {"percent": 50})],
    "light_warm_dim": [("desk_lamp", "adjust_brightness", {"percent": -50})],
    "air_fan_up": [("air_purifier", "set_mode", "high")],
    "fan_on": [("vent_fan", "set_power", "on")],
    "wake_light": [("desk_lamp", "adjust_brightness", {"percent": 60, "min_raw": 153})],
    "ventilate_suggest": "창문 환기", "posture_card": "자세 안내 카드", "posture_voice": "자세 음성 안내",
    "sound_cue": "알림음", "break_short": "5분 휴식", "break_long": "긴 휴식", "sleep_suggest": "취침 권유",
}


def arm_text(arm: Any) -> str | None:
    a = ARM_ACTION.get(str(arm))
    if a is None:
        return None
    if isinstance(a, str):
        return a
    return " + ".join(f"{DEVICE_REAL.get(t, t)} {op_text(o, v)}" for t, o, v in a)


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
    if op == "speak":
        t = value.get("text") if isinstance(value, dict) else value
        return f"말하기 “{_clip(str(t or ''), 40)}”"
    if op == "chime":
        v = value if isinstance(value, dict) else {"pattern": value}
        return "알림음" + "".join(f" {v[k]}" for k in ("pattern", "volume") if v.get(k))
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
        elif topic == TOPIC_REQUEST and data.get("kind") != "rest_notice" and data.get("auto") is not True:
            # 휴식 알림·자동 안내는 답하는 카드가 아니다
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
            devices = arm_text(req.get("arm")) or _plan_text(cause) or "기기 제어 없음"
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
            gate = str(last_cmd.get("gate") or "")   # speak·chime 은 "notice"
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
    away = scores_away(data)
    fatigue = None if away else _num(data.get("c_fatigue"))
    return {
        "posture": label,
        "posture_conf": _num(p.get("conf")) if p else None,
        "chips": ["허브", FSM_KO.get(state, state or DASH), CONTEXT_KO.get(str(data.get("context")), DASH)],
        "phase": str(data.get("phase") or ""),
        "fatigue": fatigue,
        "away": away,
        "env": " · ".join(env) or DASH,
        "decision": control.get("headline") or DASH,
        "decision_status": " · ".join(x for x in (control.get("status"), control.get("time")) if x and x != DASH) or DASH,
    }


# --------------------------------------------------------------------------- 가상 기기 ("기기가 연결돼 있었다면")

# Pi 5 ui_env_app 의 값 그대로(lamp_defaults.dart · feedback_policy.dart ilinkFramesForPhase · fan_control.dart fanFrame).
LAMP_FLOOR = 30          # kLampBrightnessFloorDefault (설정 lamp_brightness_floor 가 없을 때)
LAMP_MIN_STEP = 10       # kLampAdjustMinStep
PHASE_LIGHT = {"idle": None, "end": None, "start": 48, "focus": 110, "fatigue": 180, "recovery": 90}  # None = 끔
UNDO_WINDOW_S = 60       # hub control.yaml undo_window_sec
# 개인화 arm → 채널(hub config/personalize.yaml arms). 기기 명령이 없는 voice·sound·display arm 을 화면 기기로 보여 줄 때 쓴다.
ARM_CHANNEL = {"light_cool_bright": "lamp", "light_warm_dim": "lamp", "air_fan_up": "air", "fan_on": "fan",
               "ventilate_suggest": "display", "posture_card": "display", "wake_light": "lamp",
               "posture_voice": "voice", "sound_cue": "sound", "break_short": "display", "break_long": "display",
               "sleep_suggest": "display"}
AIR_MODES = ("off", "auto", "low", "mid", "high")   # control.yaml: air_purifier set_mode off|auto|low|mid|high


def dart_round(x: float) -> int:
    """Dart num.round(): .5 는 0 에서 먼 쪽으로(파이썬 round 는 짝수 쪽 — 252.5 가 252 가 된다)."""
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def adjust_brightness(current: int | None, percent: int, floor: int = LAMP_FLOOR, min_step: int = LAMP_MIN_STEP,
                      min_raw: int | None = None) -> tuple[int, int]:
    """feedback_policy.dart adjustBrightness 와 같은 계산 → (계산 기준 base, 새 값 actual)."""
    lo = max(0, min(255, floor))
    base = max(lo, min(255, lo if current is None else current))
    nxt = max(lo, min(255, dart_round(base * (1 + percent / 100))))
    if percent > 0 and nxt <= base and base < 255:
        nxt = min(255, base + min_step)
    if min_raw is not None and nxt < min_raw:
        nxt = max(lo, min(255, min_raw))
    return base, nxt


def fan_valid(op: Any, value: Any) -> bool:
    """fan_control.dart fanFrame 이 프레임을 만드는 조합만 실행된다."""
    def up_to(m: int) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= m
    return ((op == "set_power" and value in ("on", "off")) or (op == "set_level" and up_to(3))
            or (op == "set_speed" and up_to(100)) or (op == "set_mode" and value in ("normal", "natural"))
            or (op == "set_timer" and up_to(65535)))


# ---- Pi 5 앱이 화면에 띄우는 문구 그대로(ui_env_app/lib/intervention.dart · dashboard_view.dart)

def auto_action_title(fsm: str) -> str:
    """intervention.dart autoActionTitle — 자동 실행 뒤 알림 제목."""
    return {"ACTION_ENV": "환경을 조정했어요", "ACTION_BREAK": "휴식을 권하는 알림을 켰어요",
            "ACTION_POSTURE": "자세를 바꿀 때라고 알렸어요"}.get(fsm, "상태에 맞춰 조정했어요")


def _env_phrase(flag: str, ph: dict[str, Any]) -> str | None:
    co2, t, h, lux = ph.get("co2_ppm"), ph.get("temp_c"), ph.get("humidity_pct"), ph.get("lux")
    return {
        "co2_high": f"CO₂가 높아요 ({co2:.0f} ppm)" if co2 is not None else "CO₂가 높아요",
        "co2_rising": "CO₂가 빠르게 오르고 있어요",
        "too_hot": f"방이 더워요 ({t:.1f}°C)" if t is not None else "방이 더워요",
        "too_cold": f"방이 추워요 ({t:.1f}°C)" if t is not None else "방이 추워요",
        "too_humid": f"습도가 높아요 ({round(h)}%)" if h is not None else "습도가 높아요",
        "too_dry": f"공기가 건조해요 ({round(h)}%)" if h is not None else "공기가 건조해요",
        "too_dark": f"조명이 어두워요 ({lux:.0f} lx)" if lux is not None else "조명이 어두워요",
    }.get(flag)


def intervention_reason(ph: dict[str, Any], cause: str | None = None) -> str:
    """intervention.dart interventionReason — 원인·환경 플래그로 사람이 읽는 이유 한 줄."""
    cause = cause or ph.get("cause")
    if cause == "environment":
        parts = [x for x in (_env_phrase(f, ph) for f in ph.get("env_flags") or []) if x]
        if parts:
            return " · ".join(parts)
        co2 = ph.get("co2_ppm")
        return f"CO₂ {co2:.0f} ppm — 환경이 쾌적 범위를 벗어났어요" if co2 is not None else "환경이 쾌적 범위를 벗어났어요"
    if cause == "cognitive":
        return "작업 시간이 길어지고 입력 리듬이 느려졌어요"
    if cause == "posture":
        return "움직임이 줄고 졸음 신호가 보여요"
    return "지금 상태를 보고 판단했어요"


def _environment_title(flags: list[str]) -> str:
    if "co2_high" in flags or "co2_rising" in flags:
        return "잠깐 환기해 볼까요?"
    if "too_dark" in flags:
        return "조명을 조금 밝혀 볼까요?"
    if "too_hot" in flags or "too_cold" in flags:
        return "실내 온도를 맞춰 볼까요?"
    return "환경을 잠시 조정해볼까요?"


def suggestion_title(req: dict[str, Any], ph: dict[str, Any]) -> str:
    """dashboard_view.dart suggestionTitle."""
    kind = req.get("kind")
    if kind == "checkin_suggest":
        return "지금 피곤하세요?"
    if kind == "vent_suggest":
        m = req.get("ventilate_minutes")
        return "창문을 잠깐 열어 주세요" if m is None else f"창문을 {m}분 열어 주세요"
    cause = req.get("cause") or ph.get("cause")
    if cause == "environment":
        return _environment_title(list(ph.get("env_flags") or []))
    return {"posture": "자세를 가볍게 바로잡아볼까요?", "cognitive": "잠깐 쉬어가는 건 어떨까요?"}.get(
        str(cause), "환경을 잠시 조정해볼까요?")


def suggestion_body(req: dict[str, Any], ph: dict[str, Any]) -> str:
    """dashboard_view.dart suggestionBody + V18 규칙(hub 의 explanation 이 있으면 그것)."""
    if req.get("kind") == "checkin_suggest":
        return str(req.get("explanation") or "예/아니오로만 답해 주세요. 판정을 다듬는 데 써요.")
    if req.get("explanation"):
        return str(req["explanation"])
    return "이유: " + intervention_reason(ph, req.get("cause"))


def suggestion_buttons(req: dict[str, Any]) -> dict[str, str]:
    """dashboard_view.dart suggestionButtons."""
    return {"checkin_suggest": {"accept": "예", "reject": "아니오"},
            "vent_suggest": {"accept": "열게요", "reject": "괜찮아요"}}.get(
        str(req.get("kind")), {"accept": "적용할게요", "reject": "괜찮아요"})


PHASE_LABEL = {"idle": "대기 중", "start": "집중 준비 중", "focus": "집중 유지 중", "fatigue": "잠시 조정이 필요해요",
               "recovery": "회복 중", "end": "세션 완료"}   # dashboard_view.dart 국면 문구
DEVICE_ICON = {"lamp": "💡", "fan": "🌀", "air": "🍃", "window": "🪟", "speaker": "🔊", "laptop": "💻"}
AUTO_NOTICE_S = 60   # 자동 실행 알림을 남겨 두는 시간(되돌리기 창 undo_window_sec 과 같게)


class VirtualHome:
    """주변기기가 전부 연결·'상태 기반 조명·음악 피드백' 스위치가 켜져 있다고 보고, hub 가 보낸 것을 Pi 5 앱과 같은 규칙으로
    가상 기기에 적용한다. 실제 Pi 5 가 돌려준 결과(feedback_disabled 등)는 따로 붙여 둔다 — 화면은 가상임을 밝힌다.
    """

    def __init__(self) -> None:
        # 무드등(iLink): 아직 한 번도 안 썼으면 밝기·전원 모름(None) — 앱과 같음
        self.lamp: dict[str, Any] = {"power": None, "brightness": None, "last": None, "at": None}
        self.fan: dict[str, Any] = {"power": False, "level": None, "speed": None, "mode": None, "timer": None,
                                    "last": None, "at": None}
        self.air: dict[str, Any] = {"mode": None, "last": None, "at": None}
        self.window: dict[str, Any] = {"open": False, "minutes": None, "last": None, "at": None}
        self.speaker: dict[str, Any] = {"music": False, "voice": None, "last": None, "at": None}
        self.timeline: deque[dict[str, Any]] = deque(maxlen=60)
        self._phase_key: str | None = None
        self._accepted_action: str | None = None
        self._latest: dict[str, Any] = {}
        self._by_cmd: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        # 앱 화면 알림(가상): 기기가 바뀔 때마다 한 줄("스피커 켜짐 · 음악 재생" 같은) — 화면이 위에 잠깐 띄운다
        self.notices: deque[dict[str, Any]] = deque(maxlen=40)
        self._notice_seq = 0
        self.auto_notice: dict[str, Any] | None = None
        # 기기 상태가 바뀔 때 부르는 함수들(key, 기기 dict, 시각, 문구) — --act-laptop 이 화면 밝기·볼륨으로 옮긴다
        self.listeners: list[Callable[[str, dict[str, Any], float, str], None]] = []
        # 휴식(REST) 배너: hub 의 rest_notice 카드가 있으면 그 문구·시간, 없으면(구버전 hub) state/phase REST 진입으로
        self.rest: dict[str, Any] | None = None
        self.rest_min_s: float | None = None    # hub fsm timers.rest_min_sec (load_hub_rules 가 넣는다)
        self.rest_counts: str | None = None     # state/phase sensor_summary.rest_notice.counts 요약
        self.info: deque[dict[str, Any]] = deque(maxlen=8)   # 자동 안내 카드(auto: true, options [dismiss]) — 답하지 않는 배너
        self.current_cmd: dict[str, Any] | None = None       # 실행 중인 control/cmd data — listeners 가 '깨우기'인지 본다

    # ---------------------------------------------------------------- 공통
    def _notify(self, key: str, text: str, at: float, tone: str = "info") -> None:
        self._notice_seq += 1
        self.notices.append({"id": self._notice_seq, "t": time.strftime("%H:%M:%S", time.localtime(at)), "at": at,
                             "icon": DEVICE_ICON.get(key, "🔔"), "text": text, "tone": tone})

    def _stamp(self, dev: dict[str, Any], text: str, at: float) -> None:
        dev["last"], dev["at"] = text, at
        key = next((k for k, d in (("lamp", self.lamp), ("fan", self.fan), ("air", self.air), ("window", self.window),
                                   ("speaker", self.speaker)) if d is dev), "")
        name = {"lamp": "무드등", "fan": "선풍기", "air": "공기청정기", "window": "창문", "speaker": "스피커"}.get(key, "")
        self._notify(key, f"{name} {text}".strip(), at, "on" if key else "info")
        for fn in self.listeners:
            fn(key, dev, at, text)

    def log_laptop(self, text: str, at: float, ok: bool = True) -> None:
        """노트북 대역 기기가 실제로 한 일(또는 실패)을 타임라인·알림에."""
        self._event(at, kind="laptop", text=text, virtual={"status": "succeeded" if ok else "failed"})
        self._notify("laptop", text, at, "on" if ok else "info")

    def _event(self, at: float, **kw: Any) -> dict[str, Any]:
        ph = self._latest
        e = {"t": time.strftime("%H:%M:%S", time.localtime(at)), "at": at, "fsm_state": ph.get("fsm_state"),
             "cause": ph.get("cause"), **kw}
        self.timeline.append(e)
        return e

    def _music(self, on: bool, at: float, why: str) -> None:
        if self.speaker["music"] != on:
            self.speaker["music"] = on
            self._stamp(self.speaker, ("켜짐 · 음악 재생 · " if on else "꺼짐 · 음악 정지 · ") + why, at)

    # ---------------------------------------------------------------- state/phase → 단계 조명·음악 (FeedbackCoordinator.apply)
    def on_phase(self, env: Any, at: float) -> None:
        data = _map(_map(env).get("data"))
        if not data:
            return
        ss = _map(data.get("sensor_summary"))
        pers = _map(ss.get("personalize")) or _map(ss.get("personalization"))
        self._latest = {"fsm_state": str(data.get("fsm_state") or ""), "phase": str(data.get("phase") or ""),
                        "gate": data.get("gate"), "cause": data.get("cause"),
                        "env_flags": ss.get("env_flags") if isinstance(ss.get("env_flags"), list) else [],
                        "co2_ppm": _num(ss.get("co2_ppm")), "temp_c": _num(ss.get("temp_c")),
                        "humidity_pct": _num(ss.get("humidity_pct")), "lux": _num(ss.get("lux")),
                        "decision_source": pers.get("decision_source") or pers.get("source"),
                        "explanation": pers.get("explanation"), "arm": pers.get("arm"), "arm_label": pers.get("arm_label")}
        fsm, phase, gate = self._latest["fsm_state"], self._latest["phase"], self._latest["gate"]
        rn = _map(ss.get("rest_notice"))
        if rn:
            c = _map(rn.get("counts"))
            self.rest_counts = " · ".join(f"{lab} {c[k]}" for k, lab in (("notices", "알림"), ("speaks", "말함"),
                                                                       ("reminders", "다시 알림"), ("suppressed", "생략"),
                                                                       ("dismissed", "닫음")) if c.get(k) is not None) or None
            if not rn.get("enabled", True):
                self.rest_counts = "hub 휴식 알림 꺼짐" + (f" · {self.rest_counts}" if self.rest_counts else "")
        self._rest_on_phase(fsm, at)
        if fsm != self._accepted_action:
            self._accepted_action = None
        # 피로 국면인데 ACTION_ENV(명령이 맡음)·ACTION_* 아님·자동 아닌데 수락 전이면 조명·소리를 바꾸지 않는다
        if phase == "fatigue" and (fsm == "ACTION_ENV" or not fsm.startswith("ACTION_")
                                   or (gate != "auto" and fsm != self._accepted_action)):
            return
        self._apply_outputs(phase, at, why=f"단계 {phase}")

    # ---------------------------------------------------------------- 휴식
    def _rest_on_phase(self, fsm: str, at: float) -> None:
        r = self.rest
        if fsm == "REST":
            if r is None or r.get("ended_at") is not None:
                self.rest = {"source": "phase", "message": "휴식 중이에요", "duration_s": self.rest_min_s, "start": at,
                             "reason": None, "request_id": None, "ended_at": None}
                self._event(at, kind="rest", text="휴식 시작 — hub 알림 없음 (구버전): state/phase REST 로만 앎")
        elif r is not None and r.get("ended_at") is None:
            r["ended_at"] = at
            self._event(at, kind="rest", text=f"휴식 끝 — {FSM_KO.get(fsm, fsm)}")

    REST_REASON_KO = {"accepted": "휴식 제안을 수락해서", "asleep": "졸고 있어서 바로 휴식", "escalated": "개입 효과가 없어 휴식으로",
                      "reminder": "아직 쉬는 중 — 다시 알림"}

    def on_rest_notice(self, d: dict[str, Any], at: float) -> None:
        """hub interaction/request kind rest_notice(f91afd8: text·minutes·rest_min_sec·reason·speak·variant).
        예전 이름(message·rest_sec·duration_s…)도 받는다."""
        msg = next((str(d[k]) for k in ("text", "message", "message_ko", "explanation", "prompt") if d.get(k)), "쉬어 가요")
        dur = None
        if _num(d.get("minutes")) is not None:
            dur = float(d["minutes"]) * 60
        if dur is None:
            dur = next((float(d[k]) for k in ("rest_min_sec", "rest_sec", "duration_s", "rest_s", "seconds")
                        if _num(d.get(k)) is not None), None)
        if dur is None:
            dur = self.rest_min_s if self.rest_min_s is not None else _num(d.get("expires_in_s"))
        code = d.get("reason")
        reason = self.REST_REASON_KO.get(str(code), str(code)) if code else None
        if isinstance(d.get("why"), list):
            reason = ", ".join(map(str, d["why"]))
        elif not reason and d.get("why"):
            reason = str(d["why"])
        if d.get("variant") and d["variant"] != "default":
            reason = (reason + " · " if reason else "") + f"{d['variant']} 문구"
        start = self.rest["start"] if self.rest and self.rest.get("ended_at") is None else at
        self.rest = {"source": "notice", "message": msg, "duration_s": dur, "start": start, "reason": reason,
                     "request_id": d.get("request_id"), "ended_at": None, "reason_code": code, "speak": d.get("speak")}
        self._notify("card", f"휴식 — {msg}", at, "card")
        self._event(at, kind="rest", text=f"휴식 알림(hub rest_notice) — {msg}" + (f" · {round(dur)} s" if dur else ""),
                    reason=reason, request_id=d.get("request_id"))

    def dismissable(self, request_id: str, now: float) -> dict[str, Any] | None:
        """지금 떠 있는 휴식 알림·자동 안내 중 이 request_id — 닫기(dismiss)를 보낼 수 있나."""
        r = self.rest
        if r and r.get("request_id") == request_id and r.get("ended_at") is None and not r.get("dismissed"):
            return {"at": r["start"], "what": "휴식 알림"}
        for x in self.info:
            if x.get("request_id") == request_id and not x.get("dismissed") and now - x["at"] <= (x["expires_in_s"] or 60):
                return {"at": x["at"], "what": "자동 안내"}
        return None

    def mark_dismissed(self, request_id: str, now: float) -> None:
        if self.rest and self.rest.get("request_id") == request_id:
            self.rest["dismissed"] = True
        for x in self.info:
            if x.get("request_id") == request_id:
                x["dismissed"] = True
        self._event(now, kind="feedback", text="닫기(dismiss) 보냄 — hub 는 닫은 횟수만 셈", request_id=request_id, verdict="dismiss")

    def info_view(self, now: float) -> list[dict[str, Any]]:
        """떠 있는 자동 안내(시한 전, 시한이 없으면 60 s). 닫은 것은 뺀다."""
        out = []
        for x in self.info:
            ttl = x["expires_in_s"] or 60
            if now - x["at"] <= ttl and not x.get("dismissed"):
                out.append({k: x[k] for k in ("t", "text", "arm_label", "channel", "explanation", "request_id")} |
                           {"left_s": max(0, round(x["at"] + ttl - now))})
        return out[::-1]

    def rest_view(self, now: float) -> dict[str, Any] | None:
        r = self.rest
        if r is None or (r.get("ended_at") is not None and now - r["ended_at"] > 8):
            return None
        left = None if not r.get("duration_s") else max(0, round(r["start"] + r["duration_s"] - now))
        return {"source": r["source"], "message": r["message"], "reason": r.get("reason"), "duration_s": r.get("duration_s"),
                "left_s": left, "ended": r.get("ended_at") is not None, "counts": self.rest_counts,
                "request_id": r.get("request_id") if r["source"] == "notice" else None, "dismissed": bool(r.get("dismissed")),
                "label": "hub 휴식 알림" if r["source"] == "notice" else "hub 알림 없음 (구버전)"}

    def _apply_outputs(self, phase: str, at: float, why: str) -> None:
        if phase != self._phase_key and phase in PHASE_LIGHT:
            self._phase_key = phase
            before = self._lamp_text()
            level = PHASE_LIGHT[phase]
            if level is None:
                self.lamp["power"] = False
            else:
                self.lamp["power"], self.lamp["brightness"] = True, level
            after = self._lamp_text()
            if before != after:
                self._stamp(self.lamp, f"단계 조명({phase}) {before}→{after}", at)
                self._event(at, kind="phase_light", text=f"단계 조명 {phase} → 무드등 {after}", source="Pi 5 앱 단계 조명 규칙")
        if phase == "fatigue":
            self._music(True, at, why)
        elif phase in ("recovery", "idle", "end"):
            self._music(False, at, why)

    def _lamp_text(self) -> str:
        if self.lamp["power"] is False:
            return "꺼짐"
        return "모름" if self.lamp["brightness"] is None else str(self.lamp["brightness"])

    # ---------------------------------------------------------------- control/cmd → 가상 실행 (handleControlCommand)
    def on_cmd(self, env: Any, at: float) -> dict[str, Any] | None:
        c = _map(_map(env).get("data"))
        target, op, value, cid = c.get("target_id"), c.get("operation"), c.get("value"), c.get("command_id")
        if not target or not op:
            return None
        if isinstance(cid, str) and cid in self._by_cmd:   # QoS 1 중복 — 두 번 실행하지 않는다
            return self._by_cmd[cid]
        self.current_cmd = c
        try:
            res = self._execute(c, at)
        finally:
            self.current_cmd = None
        line = self._command_line(c, res)
        arm, arm_label, reason = self._arm_for(str(target), at)
        e = self._event(at, kind="command", target=target, operation=op, gate=c.get("gate"),
                        origin_state=c.get("origin_state"), cause=c.get("cause") or self._latest.get("cause"),
                        arm=arm, arm_label=arm_label,
                        decision_source=self._latest.get("decision_source"), reason=reason,
                        virtual=res, real=None, text=line, command_id=cid)
        if isinstance(cid, str):
            self._by_cmd[cid] = e
            while len(self._by_cmd) > 64:
                self._by_cmd.pop(next(iter(self._by_cmd)))
        if c.get("gate") == "auto" and res.get("status") == "succeeded":
            origin = str(c.get("origin_state") or self._latest.get("fsm_state") or "")
            effect = line.split(" → ", 1)[-1]
            a = self.auto_notice
            if a is not None and at - a["at"] <= 3.0 and a["origin"] == origin:   # 같은 개입의 명령은 한 알림에
                a["effects"].append(effect)
            else:
                self.auto_notice = {"at": at, "origin": origin, "title": auto_action_title(origin),
                                    "reason": intervention_reason(self._latest, c.get("cause")), "effects": [effect]}
        return e

    _CHANNEL_TARGET = {"lamp": "desk_lamp", "fan": "vent_fan", "air": "air_purifier"}

    def _arm_for(self, target: str, at: float) -> tuple[Any, Any, Any]:
        """이 명령을 고른 개인화 arm 과 그 이유: 최근(200 s) 수락한 카드의 arm·설명, 아니면 지금 state/phase 의 arm·설명
        — 대상 기기가 맞을 때만(다른 기기를 고른 설명을 붙이지 않는다)."""
        for req in reversed(list(self._requests.values())):
            if at - req["at"] > 200:
                break
            if req.get("verdict") == "accept" and self._CHANNEL_TARGET.get(ARM_CHANNEL.get(str(req.get("arm")), "")) == target:
                return req.get("arm"), req.get("arm_label"), req.get("explanation")
        arm = self._latest.get("arm")
        if self._CHANNEL_TARGET.get(ARM_CHANNEL.get(str(arm), "")) == target:
            return arm, self._latest.get("arm_label"), self._latest.get("explanation")
        return None, None, None

    def _reject(self, c: dict[str, Any], at: float) -> str | None:
        """FeedbackCoordinator._rejection — 스위치는 켜졌다고 본다(가상)."""
        exp = c.get("expires_ts_ms")
        # hub 가 스피커 말하기·알림음은 gate "notice" 로 보낸다(f91afd8) — 램프·선풍기 규칙은 앱 그대로
        gates = ("auto", "suggest", "undo", "notice") if c.get("operation") in ("speak", "chime") else ("auto", "suggest", "undo")
        if (not isinstance(exp, (int, float)) or c.get("gate") not in gates
                or c.get("requires_confirmation") is not False):
            return "invalid_command"
        if exp <= at * 1000:
            return "expired"
        return None

    def _execute(self, c: dict[str, Any], at: float) -> dict[str, Any]:
        target, op, value = c.get("target_id"), c.get("operation"), c.get("value")
        err = self._reject(c, at)
        if err:
            return {"status": "failed", "error_code": err}
        if target == "desk_lamp":
            previous = 0 if self.lamp["power"] is False else self.lamp["brightness"]
            if op == "set_brightness" and isinstance(value, int) and 0 <= value <= 255:
                self.lamp["power"], self.lamp["brightness"] = True, value
                out = {"status": "succeeded", "actual_value": value}
            elif op == "adjust_brightness" and _num(_map(value).get("percent")) is not None:
                v = _map(value)
                mr = v.get("min_raw")
                _, actual = adjust_brightness(None if self.lamp["power"] is False else self.lamp["brightness"],
                                              dart_round(v["percent"]),
                                              min_raw=mr if isinstance(mr, int) and 0 <= mr <= 255 else None)
                self.lamp["power"], self.lamp["brightness"] = True, actual
                out = {"status": "succeeded", "actual_value": actual, "actual": actual}
                if previous is not None:
                    out["previous"] = previous
            elif op == "set_power" and value in ("on", "off"):
                self.lamp["power"] = value == "on"
                out = {"status": "succeeded", "actual_value": value}
            else:
                return {"status": "failed", "error_code": "unsupported_operation_or_value"}
            self._stamp(self.lamp, op_text(str(op), value) + (f" ({previous}→{out.get('actual', out['actual_value'])})"
                                                                if op == "adjust_brightness" else ""), at)
            if c.get("gate") != "undo" and self._latest.get("phase") == "fatigue":
                self._music(True, at, "무드등 명령(피로 국면)")
            return out
        if target == "vent_fan":
            if not fan_valid(op, value):
                return {"status": "failed", "error_code": "unsupported_operation_or_value"}
            key = {"set_power": "power", "set_level": "level", "set_speed": "speed", "set_mode": "mode",
                   "set_timer": "timer"}[str(op)]
            self.fan[key] = (value == "on") if op == "set_power" else value
            self._stamp(self.fan, op_text(str(op), value), at)
            return {"status": "succeeded", "actual_value": value}
        if op == "speak":
            v = value if isinstance(value, dict) else {"text": value}
            text = str(v.get("text") or v.get("message") or c.get("text") or "").strip()
            if not text:
                return {"status": "failed", "error_code": "unsupported_operation_or_value"}
            self.speaker["voice"] = "음성 안내: " + text
            self._stamp(self.speaker, self.speaker["voice"], at)
            return {"status": "succeeded", "actual_value": "spoken"}
        if op == "chime":
            v = value if isinstance(value, dict) else {"pattern": value}
            parts = [str(x) for x in (v.get("pattern"), v.get("volume")) if x]
            self.speaker["voice"] = "알림음" + (f" ({' · '.join(parts)})" if parts else "")
            self._stamp(self.speaker, self.speaker["voice"], at)
            return {"status": "succeeded", "actual_value": "chimed"}
        if target == "air_purifier":
            if op != "set_mode" or value not in AIR_MODES:
                return {"status": "failed", "error_code": "unsupported_operation_or_value"}
            self.air["mode"] = value
            self._stamp(self.air, f"모드 {value}", at)
            return {"status": "succeeded", "actual_value": value}
        return {"status": "failed", "error_code": "unknown_target"}

    def _command_line(self, c: dict[str, Any], res: dict[str, Any]) -> str:
        origin = c.get("origin_state") or self._latest.get("fsm_state") or "-"
        cause = c.get("cause") or self._latest.get("cause")
        head = f"{FSM_KO.get(str(origin), origin)}({CAUSE_KO.get(cause, cause or '-')} 원인)"
        dev = "스피커" if c.get("operation") in ("speak", "chime") else DEVICE_REAL.get(str(c.get("target_id")), str(c.get("target_id"))).split(" (")[0]
        act = op_text(str(c.get("operation")), c.get("value"))
        if res.get("status") == "succeeded":
            tail = (f" ({res['previous']}→{res['actual']})" if res.get("previous") is not None and "actual" in res
                    else f" (모름→{res['actual']})" if "actual" in res else "")
        else:
            tail = f" ✗ {res.get('error_code')}"
        return f"{head} → {dev} {act}{tail}"

    def on_result(self, env: Any) -> None:
        d = _map(_map(env).get("data"))
        e = self._by_cmd.get(d.get("command_id"))
        if e is not None:
            e["real"] = {"status": d.get("status"), "error_code": d.get("error_code"), "previous": d.get("previous"),
                         "actual": d.get("actual"), "node": _map(env).get("node")}

    # ---------------------------------------------------------------- 카드·응답
    def on_request(self, env: Any, at: float) -> None:
        d = _map(_map(env).get("data"))
        rid = d.get("request_id")
        if d.get("kind") == "rest_notice":
            self.on_rest_notice(d, at)
            return
        if d.get("auto") is True:
            # 기기 없는 arm 이 AUTO 로 고른 안내(hub 예정 모양: auto·options[dismiss]·arm·arm_label·channel·explanation·text)
            text = str(d.get("text") or d.get("explanation") or d.get("arm_label") or d.get("prompt_code") or "")
            self.info.append({"at": at, "t": time.strftime("%H:%M:%S", time.localtime(at)), "text": text,
                              "arm_label": d.get("arm_label") or d.get("arm"), "channel": d.get("channel"),
                              "explanation": d.get("explanation"), "expires_in_s": _num(d.get("expires_in_s")),
                              "request_id": d.get("request_id")})
            self._notify("card", f"자동 안내 — {text}", at, "card")
            self._event(at, kind="card", text=f"자동 안내({d.get('arm_label') or d.get('arm') or '-'}) — {text}",
                        request_id=d.get("request_id"), gate="auto", arm=d.get("arm"), arm_label=d.get("arm_label"),
                        reason=d.get("explanation"))
            return
        if not rid:
            return
        arm = d.get("arm")
        ch = ARM_CHANNEL.get(str(arm))
        expl = d.get("explanation")
        req = {**d, "at": at, "verdict": None}
        self._requests[str(rid)] = req
        while len(self._requests) > 32:
            self._requests.pop(next(iter(self._requests)))
        if ch in ("voice", "sound"):   # 음성 안내·알림음 arm 은 카드와 함께 소리로(가상 스피커)
            self.speaker["voice"] = ("음성 안내: " + str(expl or d.get("prompt_code") or "")) if ch == "voice" else "알림음"
            self._stamp(self.speaker, self.speaker["voice"], at)
        self._notify("card", f"제안 카드 — {suggestion_title(d, self._latest)}", at, "card")
        self._event(at, kind="card", text=f"카드 {d.get('kind')} — {expl or d.get('prompt_code') or ''}".strip(),
                    request_id=rid, gate="suggest", cause=d.get("cause") or self._latest.get("cause"), arm=arm,
                    arm_label=d.get("arm_label"), decision_source=self._latest.get("decision_source"), reason=expl)

    def on_feedback(self, env: Any, at: float) -> None:
        d = _map(_map(env).get("data"))
        verdict, rid = d.get("verdict"), d.get("request_id")
        if verdict not in ("accept", "reject", "timeout"):
            return
        req = self._requests.get(str(rid)) if rid else None
        if req is not None:
            req["verdict"] = verdict
        who = _map(env).get("node") or "?"
        if verdict == "accept" and req is not None:
            kind = req.get("kind")
            if kind == "vent_suggest" or req.get("arm") == "ventilate_suggest":
                self.window.update(open=True, minutes=req.get("ventilate_minutes"))
                self._stamp(self.window, f"창문 열기{'' if req.get('ventilate_minutes') is None else ' ' + str(req['ventilate_minutes']) + '분'}", at)
            # FeedbackCoordinator.accept: 제안(ACTION_ENV 제외) 수락이면 그 국면의 조명·소리를 바로 적용
            ph = self._latest
            if ph.get("gate") == "suggest" and str(ph.get("fsm_state", "")).startswith("ACTION_") and \
                    ph.get("fsm_state") != "ACTION_ENV":
                self._accepted_action = ph["fsm_state"]
                self._phase_key = None
                self._apply_outputs(str(ph.get("phase")), at, why="제안 수락")
        self._event(at, kind="feedback", text=f"응답 {verdict} ({who})" + (f" · 카드 {req.get('kind')}" if req else ""),
                    request_id=rid, verdict=verdict)

    def pending_card(self, now: float) -> dict[str, Any] | None:
        """응답 안 했고 시한 전인 마지막 카드."""
        for req in reversed(list(self._requests.values())):
            exp = _num(req.get("expires_in_s"))
            if req.get("verdict") is None and (exp is None or now <= req["at"] + exp):
                return {"request_id": req.get("request_id"), "kind": req.get("kind"),
                        "title": suggestion_title(req, self._latest), "body": suggestion_body(req, self._latest),
                        "buttons": suggestion_buttons(req),
                        "text": req.get("explanation") or req.get("prompt_code") or "-",
                        "arm_label": req.get("arm_label"), "cause": req.get("cause"),
                        "left_s": None if exp is None else max(0, round(req["at"] + exp - now)),
                        "options": req.get("options") or ["accept", "reject"], "shown_at": req["at"]}
            if req.get("verdict") is None:
                continue
            break
        return None

    def undoable(self, now: float) -> dict[str, Any] | None:
        """되돌리기 창(60 s) 안의 마지막 자동 실행 명령 — Pi 5 앱은 이때 reject 를 보내 hub 가 undo 명령을 낸다."""
        for e in reversed(self.timeline):
            if e.get("kind") == "command" and e.get("gate") == "auto" and now - e["at"] <= UNDO_WINDOW_S:
                return {"text": e["text"], "command_id": e.get("command_id"), "left_s": round(UNDO_WINDOW_S - (now - e["at"]))}
        return None

    def view(self, now: float) -> dict[str, Any]:
        def age(d: dict[str, Any]) -> str | None:
            return None if d["at"] is None else time.strftime("%H:%M:%S", time.localtime(d["at"]))
        lamp = self.lamp
        fan = self.fan
        devices = [
            {"key": "lamp", "icon": "💡", "name": "iLink 무드등", "on": lamp["power"] is True,
             "state": "꺼짐" if lamp["power"] is False else "모름(아직 안 켬)" if lamp["brightness"] is None
             else f"켜짐 · 밝기 {lamp['brightness']}/255 ({round(lamp['brightness'] / 255 * 100)} %)",
             "level": None if lamp["power"] is not True or lamp["brightness"] is None else lamp["brightness"] / 255,
             "last": lamp["last"], "time": age(lamp)},
            {"key": "fan", "icon": "🌀", "name": "Pico 2 W 선풍기", "on": bool(fan["power"]),
             "state": ("켜짐" if fan["power"] else "꺼짐") + "".join(
                 f" · {lab} {fan[k]}" for k, lab in (("level", "풍량"), ("speed", "속도"), ("mode", "모드"), ("timer", "타이머 s"))
                 if fan[k] is not None),
             "level": None, "last": fan["last"], "time": age(fan)},
            {"key": "air", "icon": "🍃", "name": "LG 공기청정기", "on": self.air["mode"] not in (None, "off"),
             "state": "모름" if self.air["mode"] is None else f"모드 {self.air['mode']}", "level": None,
             "last": self.air["last"], "time": age(self.air)},
            {"key": "window", "icon": "🪟", "name": "창문 환기(카드)", "on": self.window["open"],
             "state": ("열기" + ("" if self.window["minutes"] is None else f" {self.window['minutes']}분")) if self.window["open"]
             else "닫힘", "level": None, "last": self.window["last"], "time": age(self.window)},
            {"key": "speaker", "icon": "🔊", "name": "스피커·음성", "on": self.speaker["music"],
             "state": ("음악 재생 중" if self.speaker["music"] else "조용함") + (f" · {self.speaker['voice']}" if self.speaker["voice"] else ""),
             "level": None, "last": self.speaker["last"], "time": age(self.speaker)},
        ]
        tl = []
        for e in list(self.timeline)[-30:]:
            real = e.get("real")
            tl.append({k: e.get(k) for k in ("t", "kind", "text", "fsm_state", "cause", "gate", "arm", "arm_label",
                                              "decision_source", "reason", "verdict", "request_id")} |
                      {"real": None if real is None else (f"{real['status']}" + (f" {real['error_code']}" if real.get("error_code") else "")
                                                         + (f" ({real['node']})" if real.get("node") else "")),
                       "real_ok": None if real is None else real.get("status") == "succeeded",
                       "virtual_ok": None if e.get("virtual") is None else e["virtual"].get("status") == "succeeded"})
        a = self.auto_notice
        auto = None if a is None or now - a["at"] > AUTO_NOTICE_S else {
            "title": a["title"], "reason": a["reason"], "effects": a["effects"],
            "t": time.strftime("%H:%M:%S", time.localtime(a["at"])), "left_s": round(AUTO_NOTICE_S - (now - a["at"])),
            "key": round(a["at"], 3)}
        fan_cmd = next((e for e in reversed(self.timeline) if e.get("kind") == "command" and e.get("target") == "vent_fan"), None)
        fan_view = {"power": bool(fan["power"]), "level": fan["level"], "speed": fan["speed"], "mode": fan["mode"],
                    "timer": fan["timer"], "last": fan["last"], "time": age(fan),
                    "why": None if fan_cmd is None else {
                        "text": fan_cmd["text"], "cause": CAUSE_KO.get(fan_cmd.get("cause"), fan_cmd.get("cause")),
                        "gate": {"auto": "자동", "suggest": "제안 수락", "undo": "되돌리기"}.get(str(fan_cmd.get("gate")), fan_cmd.get("gate")),
                        "arm": fan_cmd.get("arm_label") or fan_cmd.get("arm"), "reason": fan_cmd.get("reason"), "t": fan_cmd["t"]}}
        return {"devices": devices, "timeline": tl[::-1], "card": self.pending_card(now), "undo": self.undoable(now),
                "fan": fan_view, "rest": self.rest_view(now), "info": self.info_view(now),
                "auto": auto, "notices": list(self.notices)[::-1][:12],
                "home": PHASE_LABEL.get(str(self._latest.get("phase")), self._latest.get("fsm_state") or "-")}


# --------------------------------------------------------------------------- 사용자 프로필(페르소나) — V18 profileData 와 같은 모양

PROFILE_ENUMS = {"age_band": ("10s", "20s", "30s", "40s", "50s+"), "light_pref": ("low", "mid", "high"),
                 "sound_pref": ("off", "low", "mid", "high"), "wind_pref": ("off", "low", "mid", "high"),
                 "placement": ("left", "center", "right")}   # hub personalize/onboarding.py ENUMS
FOCUS_HELP = ("light", "sound", "wind", "break", "voice")    # onboarding.py FOCUS_HELP
# V18(ui-live display/atlas/ui_demo_app) 기본 페르소나를 profileData 규칙으로 옮긴 값:
#  수험생 = 기본 프로필 '지우'(19세 · 수면 7 h · 23:30 · 백색 소음 · 조명/선풍기/알림 · 수험생 밝기 0.85 → light high ·
#           선풍기 끔 → wind off · 볼륨 0.3 → sound low). 대학생 = V18 에 직업이 없어, 같은 기본값에 20대 · 수험생 아님 밝기 0.45 → light mid.
PERSONAS = {
    "student_exam": {"label": "수험생", "data": {"age_band": "10s", "sleep_hours": 7, "bedtime": "23:30", "wake_time": "06:30",
                                              "light_pref": "high", "sound_pref": "low", "wind_pref": "off",
                                              "focus_help": ["light", "wind", "sound"], "placement": "center"}},
    "student_univ": {"label": "대학생", "data": {"age_band": "20s", "sleep_hours": 7, "bedtime": "23:30", "wake_time": "06:30",
                                              "light_pref": "mid", "sound_pref": "low", "wind_pref": "off",
                                              "focus_help": ["light", "wind", "sound"], "placement": "center"}},
}
_HHMM = __import__("re").compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_PID = __import__("re").compile(r"^[A-Za-z0-9_.-]{1,64}$")


def validate_profile(data: Any) -> tuple[dict[str, Any] | None, str]:
    """화면이 보낸 프로필 → hub 가 받는 키만(onboarding.parse_profile 과 같은 범위). (data, 오류)"""
    d = _map(data)
    pid = d.get("profile_id")
    if not isinstance(pid, str) or not _PID.match(pid):
        return None, "profile_id 형식 오류"
    out: dict[str, Any] = {"profile_id": pid}
    for k, allowed in PROFILE_ENUMS.items():
        if k in d:
            if d[k] not in allowed:
                return None, f"{k} 값 오류"
            out[k] = d[k]
    if "sleep_hours" in d:
        v = d["sleep_hours"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 3 <= v <= 12:
            return None, "sleep_hours 는 3~12"
        out["sleep_hours"] = v
    for k in ("bedtime", "wake_time"):
        if k in d:
            if not isinstance(d[k], str) or not _HHMM.match(d[k]):
                return None, f"{k} 는 HH:MM"
            out[k] = d[k]
    if "focus_help" in d:
        helps = d["focus_help"]
        if not isinstance(helps, list) or any(h not in FOCUS_HELP for h in helps):
            return None, "focus_help 값 오류"
        out["focus_help"] = list(dict.fromkeys(helps))
    return out, ""


def new_profile_id() -> str:
    """V18 newProfileId 와 같은 모양: 'p' + 16 hex (이름과 무관한 난수)."""
    return "p" + os.urandom(8).hex()


def _page_version() -> float | None:
    """index.html 수정 시각 — 바뀌면 열린 화면이 스스로 다시 불러온다."""
    try:
        return os.path.getmtime(os.path.join(HERE, "index.html"))
    except OSError:
        return None


# --------------------------------------------------------------------------- 노트북 대역 기기(--act-laptop)

# V18 profileData 의 soundPref 구간(볼륨 < 0.34 low · < 0.67 mid · 나머지 high)의 가운데 값. off 면 소리를 올리지 않는다.
# 첫 설정이 없으면 V18 기본 볼륨 0.3.
SOUND_PREF_VOLUME = {"low": 0.2, "mid": 0.5, "high": 0.8}
DEFAULT_VOLUME = 0.3
LAMP_OFF_PERCENT = 10          # 무드등 '끔' = 화면이 까매지지 않는 가장 낮은 밝기
MIN_BRIGHTNESS_INTERVAL_S = 3.0  # 깜빡임 방지: 화면 밝기는 3 s 에 한 번까지(사이 값은 마지막 것만)
MIN_VOLUME_INTERVAL_S = 1.5
MANUAL_POLL_S = 5.0
# 깨우기(블루투스 대신 노트북에서 틀어 둔 음악으로 깨운다): 자세 개입 무드등·자세/휴식 말하기·알림음이 오면 노트북 음량을
# max(지금, WAKE_VOLUME) 으로 올리고, FSM 이 회복·집중으로 가거나 WAKE_MAX_S 가 지나면 올리기 전 음량으로 되돌린다.
WAKE_VOLUME = 0.8
WAKE_MAX_S = 300.0
WAKE_END_FSM = ("RECOVERY", "FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC", "IDLE", "END")


class LaptopActuator:
    """가상 무드등 → 노트북 화면 밝기(원시 0~255 → %), 가상 스피커(음악·음성 안내·알림음) → 노트북 볼륨·짧은 소리.

    - 켤 때 원래 밝기·볼륨을 읽어 두고, 끝날 때(정상 종료·Ctrl-C) 되돌린다.
    - 결과(control/result)는 보내지 않는다: Pi 5 앱이 먼저 같은 command_id 로 답하고(지금 failed feedback_disabled),
      hub 디스패처는 첫 결과로 끝낸 뒤 늦게 온 결과의 previous·actual 만 보충한다(dispatcher.py) — 노트북이 succeeded 를
      보내면 '실패한 명령에 노트북 숫자'가 섞여 되돌리기 기준이 틀어진다. 그래서 조용히 실행만 하고 화면에 적는다.
    - 사용자가 노트북 밝기를 직접 바꾼 것은 감지해 타임라인에만 적고 manual_adjust 로 보내지 않는다(자동 밝기·전원 설정이
      바꾼 것과 구별할 수 없어서).
    """

    def __init__(self, store: "Store", backend: Any, state_file: str | None = None,
                 wake_volume: float | None = WAKE_VOLUME, wake_max_s: float = WAKE_MAX_S) -> None:
        self.store, self.b = store, backend
        # 깨우기 음량(0~1, None 이면 깨우기 없음 = 예전처럼 첫 설정 소리 구간)·최대 유지 시간
        self.wake_volume, self.wake_max_s = wake_volume, wake_max_s
        self.wake: dict[str, Any] | None = None   # {before, at, until, why, fsm_seen}
        # 원래 밝기·볼륨을 적어 두는 파일 — 강제 종료로 되돌리지 못했으면 다음 실행이 이 값으로 되돌린다
        self.state_file = state_file
        self.lock = threading.Lock()
        self.orig: dict[str, Any] = {"brightness": None, "volume": None}
        self.cur: dict[str, Any] = {"brightness": None, "volume": None}
        self.want: dict[str, Any] = {"brightness": None, "volume": None}
        self.last_set = {"brightness": 0.0, "volume": 0.0}
        self.why: dict[str, str] = {"brightness": "", "volume": ""}
        self.error: str | None = None
        self.count = 0
        self.active = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_poll = 0.0

    # ---- 시작·끝
    def start(self, thread: bool = True) -> None:
        try:
            self.orig["brightness"] = self.cur["brightness"] = self.b.get_brightness()
        except Exception as e:  # noqa: BLE001
            self.error = f"화면 밝기 못 읽음: {_reason(e)}"
        try:
            self.orig["volume"] = self.cur["volume"] = self.b.get_volume()
        except Exception as e:  # noqa: BLE001
            self.error = (self.error + " · " if self.error else "") + f"볼륨 못 읽음: {_reason(e)}"
        note = ""
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    saved = json.load(f)
                for k in ("brightness", "volume"):
                    if saved.get(k) is not None:
                        self.orig[k] = saved[k]
                note = " · 지난 실행이 되돌리지 못한 원래 값을 씀"
            except (OSError, ValueError):
                pass
        if self.state_file:
            try:
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                with open(self.state_file, "w", encoding="utf-8") as f:
                    json.dump(self.orig, f)
            except OSError as e:
                self.error = f"원래 값 파일 못 씀: {e}"
        self.active = True
        with self.store.lock:
            self.store.virtual.listeners.append(self.on_change)
            self.store.virtual.log_laptop(f"노트북 대역 기기 켬 — 원래 화면 밝기 {self.orig['brightness']} % · 볼륨 "
                                          f"{_pct(self.orig['volume'])} (끝나면 되돌림){note}", time.time(), self.error is None)
        if thread:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def restore(self) -> None:
        """원래 밝기·볼륨으로. 여러 번 불러도 한 번만."""
        if not self.active:
            return
        self.active = False
        self._stop.set()
        ok = True
        for key, setter in (("brightness", self.b.set_brightness), ("volume", self.b.set_volume)):
            if self.orig[key] is not None:
                try:
                    setter(self.orig[key])
                    self.cur[key] = self.orig[key]
                except Exception as e:  # noqa: BLE001
                    ok = False
                    self.error = f"되돌리기 실패({key}): {_reason(e)}"
        if ok and self.state_file and os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except OSError:
                pass

    # ---- 깨우기
    def _wake_reason(self, c: dict[str, Any] | None) -> str | None:
        """이 명령이 '깨우기'인가: 자세 개입 무드등 밝기, 자세·휴식에서 온 말하기·알림음."""
        if not c or self.wake_volume is None:
            return None
        origin, cause, op = str(c.get("origin_state") or ""), c.get("cause"), c.get("operation")
        posture = origin == "ACTION_POSTURE" or cause == "posture"
        if c.get("target_id") == "desk_lamp" and op in ("adjust_brightness", "set_brightness") and posture:
            return "자세 개입 무드등"
        if op in ("speak", "chime") and (posture or origin == "REST"):
            return ("휴식 " if origin == "REST" else "자세 ") + ("말하기" if op == "speak" else "알림음")
        return None

    def _wake_start(self, why: str, at: float) -> None:
        """store.lock·self.lock 밖에서 불러도 되게 값만 적는다. 이미 깨우는 중이면 다시 올리지 않고 시한만 늘린다."""
        if self._pref_volume() is None:          # 첫 설정에서 소리 '끔'을 고른 사람은 소리로 깨우지 않는다('작게'는 깨운다)
            return
        with self.lock:
            if self.wake is not None:
                self.wake["until"] = at + self.wake_max_s
                return
            before = self.cur["volume"]          # 실제 지금 음량(아직 안 쓴 첫 설정 음량이 아니라)
            fsm = str(self.store.virtual._latest.get("fsm_state") or "")
            self.wake = {"before": before, "at": at, "until": at + self.wake_max_s, "why": why,
                         "fsm_seen": fsm not in WAKE_END_FSM}
            target = max(float(before or 0.0), float(self.wake_volume))
            self.want["volume"], self.why["volume"] = target, f"깨우기 · {why}"

    def _wake_check(self, now: float) -> None:
        """회복·집중으로 가면(깨우는 동안 다른 상태를 한 번은 본 뒤) 또는 시한이 지나면 올리기 전 음량으로."""
        with self.store.lock:
            fsm = str(self.store.virtual._latest.get("fsm_state") or "")
        with self.lock:
            w = self.wake
            if w is None:
                return
            if fsm not in WAKE_END_FSM:
                w["fsm_seen"] = True
                if now < w["until"]:
                    return
                end = f"{round(self.wake_max_s / 60)}분 지남"
            elif w["fsm_seen"]:
                end = FSM_KO.get(fsm, fsm)
            elif now < w["until"]:
                return
            else:
                end = f"{round(self.wake_max_s / 60)}분 지남"
            self.wake = None
            if w["before"] is not None:
                self.want["volume"], self.why["volume"] = w["before"], f"깨우기 끝 · {end}"

    # ---- 가상 기기가 바뀔 때(store.lock 안에서 불린다 — 여기서는 원하는 값만 적고 실제 쓰기는 스레드가)
    def on_change(self, key: str, dev: dict[str, Any], at: float, text: str) -> None:
        if not self.active:
            return
        wake = self._wake_reason(self.store.virtual.current_cmd)
        if (not wake and self.wake_volume is not None and key == "speaker" and dev.get("music")
                and self.store.virtual.current_cmd is None and self.store.virtual._latest.get("fsm_state") == "ACTION_POSTURE"):
            wake = "자세 개입"                     # 자동 자세 개입 국면의 음악 — 명령보다 state/phase 가 먼저 온다
        if wake:
            self._wake_start(wake, at)
        if key == "lamp":
            if dev.get("power") is False:
                pct, why = LAMP_OFF_PERCENT, "무드등 끔"
            elif dev.get("brightness") is not None:
                pct, why = max(LAMP_OFF_PERCENT, min(100, round(dev["brightness"] / 255 * 100))), f"무드등 {text}"
            else:
                return
            with self.lock:
                self.want["brightness"], self.why["brightness"] = pct, why
        elif key == "speaker":
            level = self._pref_volume()
            if dev.get("music"):
                target, why, sound = level, "스피커 음악 재생", "music"
            elif dev.get("voice") and text.startswith("알림음 ("):
                target, why, sound = None, text, "chime:" + text[text.index("(") + 1:-1]
            elif dev.get("voice") and text.startswith(("음성", "알림음")):
                target, why, sound = level, text, ("speak:" + text.split(":", 1)[1].strip()) if text.startswith("음성 안내:") \
                    else "voice" if text.startswith("음성") else \
                    ("chime:" + text[text.index("(") + 1:-1]) if text.startswith("알림음 (") else "alert"
            else:
                target, why, sound = self.orig["volume"], "스피커 음악 정지 → 원래 볼륨", None
            with self.lock:
                waking = self.wake is not None
                if target is not None and not waking:   # 깨우는 동안은 깨우기 음량을 지킨다(첫 설정 '작게'와 무관)
                    self.want["volume"], self.why["volume"] = target, why
            if sound and waking and not sound.startswith("speak:"):
                # 음악 위에 삑 소리를 얹지 않는다 — 틀어 둔 음악을 키워서 깨운다
                self.store.virtual.log_laptop(f"깨우는 중 — 알림음 생략, 음악 음량으로 깨움 ({text})", at)
                sound = None
            if sound:
                threading.Thread(target=self._chime, args=(sound,), daemon=True).start()

    def _pref_volume(self) -> float | None:
        """첫 설정 sound_pref → 볼륨(off 면 None = 올리지 않음)."""
        sent = self.store.profile_sent
        data = sent["data"] if sent else _map(_map(self.store.profile_user).get("data"))
        pref = data.get("sound_pref")
        if pref == "off":
            return None
        return SOUND_PREF_VOLUME.get(str(pref), DEFAULT_VOLUME)

    def _chime_with_volume(self, pattern: str, volume: str | None) -> None:
        """hub chime {pattern, volume}: 볼륨(low·mid·high → 첫 설정 소리 구간과 같은 값)으로 잠깐 바꿔 울리고 되돌린다."""
        level = SOUND_PREF_VOLUME.get(str(volume)) if volume else None
        before = self.cur["volume"]
        try:
            if level is not None:
                self.b.set_volume(level)
            self.b.chime(pattern)
            with self.store.lock:
                self.store.virtual.log_laptop(f"노트북 알림음 {pattern}" + (f" · 볼륨 {_pct(level)}" if level is not None else ""),
                                              time.time())
        except Exception as e:  # noqa: BLE001
            self.error = f"소리 실패: {_reason(e)}"
        finally:
            if level is not None and before is not None:
                try:
                    self.b.set_volume(before)
                except Exception as e:  # noqa: BLE001
                    self.error = f"볼륨 되돌리기 실패: {_reason(e)}"

    def _chime(self, kind: str) -> None:
        if kind.startswith("speak:"):
            text = kind[len("speak:"):]
            time.sleep(MIN_VOLUME_INTERVAL_S + 0.3)   # 볼륨을 먼저 올리고 말한다
            try:
                voice = self.b.speak(text)
                with self.store.lock:
                    self.store.virtual.log_laptop(f"노트북이 말함({voice}): “{_clip(text, 40)}”", time.time())
                return
            except Exception as e:  # noqa: BLE001 — 한국어 목소리 없음·TTS 실패 → 알림음(깨우는 중이면 생략)
                self.error = f"음성 실패 → 알림음: {_reason(e)}"
                if self.wake is not None:
                    return
                kind = "voice"
        if kind.startswith("chime:"):
            # hub 알림음 "pattern · volume"(ad76ff0: single|double|triple · low|mid|high). 모르는 패턴은 기본 한 음
            bits = [x.strip() for x in kind[len("chime:"):].split("·")]
            self._chime_with_volume(bits[0] or "single", bits[1] if len(bits) > 1 else None)
            return
        try:
            self.b.chime(kind)
            if kind not in ("music",):
                with self.store.lock:
                    self.store.virtual.log_laptop(f"노트북 알림음 ({kind})", time.time())
        except Exception as e:  # noqa: BLE001
            self.error = f"소리 실패: {_reason(e)}"

    # ---- 쓰기 스레드: 깜빡임 방지 간격, 사용자 직접 변경 감지
    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick(time.time())
            self._stop.wait(0.2)

    def tick(self, now: float) -> None:
        self._wake_check(now)
        for key, gap, setter, fmt in (("brightness", MIN_BRIGHTNESS_INTERVAL_S, self.b.set_brightness, lambda v: f"{v} %"),
                                      ("volume", MIN_VOLUME_INTERVAL_S, self.b.set_volume, _pct)):
            with self.lock:
                want, why = self.want[key], self.why[key]
                if want is None or want == self.cur[key] or now - self.last_set[key] < gap:
                    if want == self.cur[key]:
                        self.want[key] = None
                    continue
                self.want[key] = None
            before = self.cur[key]
            try:
                setter(want)
                self.cur[key], self.last_set[key] = want, now
                self.count += 1
                name = "노트북 화면 밝기" if key == "brightness" else "노트북 볼륨"
                if why.startswith("깨우기"):
                    head, _, detail = why.partition(" · ")
                    text = f"{head}: 노트북 음량 {fmt(before)} → {fmt(want)} ({detail})"
                else:
                    text = f"{name} {fmt(before)} → {fmt(want)} ({why})"
                with self.store.lock:
                    self.store.virtual.log_laptop(text, now)
            except Exception as e:  # noqa: BLE001
                self.error = f"{key} 쓰기 실패: {_reason(e)}"
                with self.store.lock:
                    self.store.virtual.log_laptop(f"{key} 쓰기 실패: {_reason(e)}", now, ok=False)
        if now - self._last_poll >= MANUAL_POLL_S and now - self.last_set["brightness"] >= MANUAL_POLL_S:
            self._last_poll = now
            try:
                real = self.b.get_brightness()
            except Exception:  # noqa: BLE001
                return
            if self.cur["brightness"] is not None and real != self.cur["brightness"]:
                with self.store.lock:
                    self.store.virtual.log_laptop(
                        f"사용자(또는 Windows)가 노트북 화면 밝기를 바꿈 {self.cur['brightness']} → {real} % — hub 에 보고하지 않음", now)
                self.cur["brightness"] = real

    def view(self) -> dict[str, Any]:
        w = self.wake
        return {"enabled": self.active, "orig": self.orig, "cur": self.cur, "error": self.error, "count": self.count,
                "wake_volume": self.wake_volume, "wake_max_s": self.wake_max_s,
                "wake": None if w is None else {"why": w["why"], "before": w["before"], "left_s": max(0, round(w["until"] - time.time()))},
                "result_policy": "결과 안 보냄(Pi 5 앱 결과와 섞이지 않게)", "manual_policy": "직접 바꾼 밝기는 화면에만(보고 안 함)"}


def _pct(v: Any) -> str:
    return "-" if v is None else f"{round(float(v) * 100)} %"


# --------------------------------------------------------------------------- 무드등 올릴 시점 (hub 규칙을 설정에서 읽어 따라가기)

def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """hub profile.deep_merge 와 같은 규칙: 둘 다 dict 인 키만 재귀, 나머지는 덮는다."""
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _load_cfg_file(path: str) -> dict[str, Any]:
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    import yaml   # PyYAML — 이 기능에만 필요(없으면 화면에 '설정 못 읽음')
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_hub_rules(config_dir: str, profile: str = "") -> dict[str, Any]:
    """hub config(fsm·ingest·control.yaml)와 시연 프로파일(profiles/<이름>.json|.yaml)을 hub 와 같은 방법으로 얹어
    무드등 판단에 쓰는 값만 꺼낸다. 코드에 숫자를 박지 않는다 — 읽지 못하면 error 를 채워 돌려준다."""
    out: dict[str, Any] = {"source": config_dir, "profile": profile or None, "error": None}
    try:
        fsm = _load_cfg_file(os.path.join(config_dir, "fsm.yaml"))
        ingest = _load_cfg_file(os.path.join(config_dir, "ingest.yaml"))
        control = _load_cfg_file(os.path.join(config_dir, "control.yaml"))
        if profile:
            base = os.path.join(config_dir, "profiles", profile)
            path = base + ".json" if os.path.exists(base + ".json") else base + ".yaml"
            prof = _load_cfg_file(path)
            fsm = _deep_merge(fsm, prof.get("fsm") or {})
            ingest = _deep_merge(ingest, prof.get("ingest") or {})
            out["profile_file"] = path
    except Exception as e:  # noqa: BLE001 — 파일·모듈·형식 어떤 문제든 화면에 이유로
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    th, tm, gate = fsm.get("thresholds") or {}, fsm.get("timers") or {}, fsm.get("gate") or {}
    tof = ingest.get("tof") or {}
    out.update(
        focus_low=th.get("fatigue_focus_low"), suspect=th.get("fatigue_suspect_low"), confirm=th.get("fatigue_confirm"),
        natural=th.get("natural_recovery"),
        elevated_hold=tm.get("fatigue_elevated_hold_sec"), confirm_hold=tm.get("fatigue_confirm_hold_sec"),
        hold_frac=tm.get("fatigue_hold_min_frac", 1.0), tick=tm.get("score_period_sec", 10),
        conf_auto=gate.get("conf_auto"), conf_suggest=gate.get("conf_suggest"), rest_min=tm.get("rest_min_sec"),
        weights=(fsm.get("weights") or {}).get("fatigue") or {}, routing=fsm.get("routing") or {},
        posture_delta=tof.get("posture_delta") or {}, min_conf=tof.get("min_confidence", 0.0),
        floors=tof.get("min_confidence_by_label") or {}, nod_max=tof.get("nod_delta_max"),
        nod_full=tof.get("nod_rate_full_hz"), rule_drowsy=tof.get("rule_drowsy_delta"),
        cooldown=control.get("cooldown_sec"), posture_actions=(control.get("actions") or {}).get("posture") or [],
    )
    return out


FOCUS_FSM = ("FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC")
PAST_SUSPECT = ("FATIGUE_SUSPECT", "FATIGUE", "CAUSE_ANALYSIS", "ACTION_POSTURE")
ACTION_FSM = ("ACTION_ENV", "ACTION_POSTURE", "ACTION_BREAK")


class TriggerTracker:
    """state/phase tick(10 s)으로 hub FSM 의 유지 판정을 따라간다. hub 가 유지 타이머를 싣지 않으므로 '추정'이다.

    engine.py 와 같은 규칙: FOCUS 에서 C ≥ fatigue_focus_low 가 elevated_hold 초(이 단계 진입 뒤, 지난 hold 초 tick 의
    hold_frac 이상) → FATIGUE_SUSPECT, 거기서 C ≥ fatigue_confirm 이 confirm_hold 초 → FATIGUE → CAUSE_ANALYSIS(원인 = 그룹 기여 최대)
    → ACTION_POSTURE(게이트 = C vs conf_auto/conf_suggest) → control.yaml actions.posture 명령(쿨다운 cooldown_sec).
    """

    def __init__(self, rules: dict[str, Any], keep_s: float = 900.0) -> None:
        self.rules = rules
        self.keep_s = keep_s
        self.ticks: deque[dict[str, Any]] = deque()
        self.marks: deque[dict[str, Any]] = deque(maxlen=60)   # 그래프 표시: 명령·카드·조건 충족
        self._state_since: dict[str, Any] = {"state": None, "at": None}
        self.away_since: float | None = None   # 자리 비움 tick 이 이어지는 동안(그래프·유지 판정에서 뺀다)
        self.away_ticks = 0
        self._last_lamp: dict[str, float] = {}
        self._prior: str | None = None
        self._arm_ts: Any = None
        self.last_arm: dict[str, Any] | None = None
        self.arm_counts: str | None = None

    def on_phase(self, env: Any, at: float) -> None:
        d = _map(_map(env).get("data"))
        if not d or _num(d.get("c_fatigue")) is None:
            return
        ss = _map(d.get("sensor_summary"))
        tof = _map(ss.get("tof"))
        state = str(d.get("fsm_state") or "")
        prev = self.ticks[-1] if self.ticks else None
        if state != self._state_since["state"]:
            self._state_since = {"state": state, "at": at}
            if state == "FATIGUE":
                self.marks.append({"at": at, "kind": "confirm", "text": "피로 확정"})
            if state in ACTION_FSM:
                self.marks.append({"at": at, "kind": "action_" + str(d.get("gate") or ""),
                                   "text": f"{FSM_KO.get(state, state)}({d.get('gate')})"})
        if scores_away(d):
            if self.away_since is None:
                self.away_since = at
            self.away_ticks += 1
            return
        self.away_since = None
        learn = _map(ss.get("learning"))
        ks = _map(ss.get("keystroke"))
        self.ticks.append({"at": at, "state": state, "c": float(d["c_fatigue"]), "gate": d.get("gate"),
                           "cause": d.get("cause"), "context": d.get("context"), "posture": tof.get("posture"),
                           "conf": _num(tof.get("posture_confidence")), "source": tof.get("posture_source"),
                           "nod_hz": _num(tof.get("nod_rate_hz")), "rule_drowsy": bool(tof.get("drowsy")),
                           "env_flags": list(ss.get("env_flags") or []), "typing": ks.get("typing_active") if ks else None,
                           "c_fsm": _num(learn.get("c_fsm")), "c_adj": _num(learn.get("c_adj")), "alpha": _num(learn.get("alpha")),
                           "since": self._state_since["at"]})
        an = _map(ss.get("arm_notice"))
        last = _map(an.get("last"))
        if last.get("ts") is not None and last.get("ts") != self._arm_ts:
            self._arm_ts = last["ts"]
            arm = str(last.get("arm") or "")
            got = ", ".join(DELIVERED_KO.get(str(x), str(x)) for x in (last.get("delivered") or [])) or "실행됨"
            text = f"개인화가 고른 것: {ARM_LABEL.get(arm, arm or '-')} ({last.get('channel') or '-'}) → {got}"
            self.marks.append({"at": at, "kind": "arm", "text": text})
            self.last_arm = {"arm": arm, "label": ARM_LABEL.get(arm, arm), "channel": last.get("channel"),
                             "gate": last.get("gate"), "delivered": got, "at": at, "text": text}
        if an:
            c = _map(an.get("counts"))
            self.arm_counts = ("꺼짐 · " if an.get("enabled") is False else "") + " · ".join(
                f"{lab} {c[k]}" for k, lab in (("cards", "안내"), ("speaks", "음성"), ("chimes", "알림음"), ("dismissed", "닫음"))
                if c.get(k) is not None)
        pers = _map(ss.get("personalize"))
        if pers.get("arm") and state in ACTION_FSM:
            self.last_arm = {**(self.last_arm or {}), "arm": pers["arm"], "label": pers.get("arm_label") or ARM_LABEL.get(pers["arm"]),
                             "source": pers.get("source"), "at": at}
        prior = prior_text(_map(ss.get("personalize")))
        if prior and prior != self._prior:
            self._prior = prior
            self.marks.append({"at": at, "kind": "prior", "text": "첫 설정 반영: " + prior})
        while self.ticks and at - self.ticks[0]["at"] > self.keep_s:
            self.ticks.popleft()
        _ = prev

    def on_cmd(self, env: Any, at: float) -> None:
        d = _map(_map(env).get("data"))
        if d.get("target_id") == "desk_lamp":
            self._last_lamp[str(d.get("operation"))] = at
            self.marks.append({"at": at, "kind": "lamp", "text": "무드등 " + op_text(str(d.get("operation")), d.get("value"))})

    def on_request(self, env: Any, at: float) -> None:
        d = _map(_map(env).get("data"))
        if d.get("kind") in ("posture_suggest",) or d.get("cause") == "posture":
            self.marks.append({"at": at, "kind": "card", "text": "자세 제안 카드"})

    # ---------------------------------------------------------------- 판단 따라가기
    def _hold(self, key_thr: str, key_hold: str, now: float) -> dict[str, Any]:
        """유지 판정까지 남은 시간(추정). engine.py 와 같은 규칙을 앞으로 tick 이 계속 임계 위라고 보고 굴려 본다.

        hold_frac < 1: 이 단계 첫 tick(t0)부터 hold 초가 지났고, 지난 hold 초(t > T−hold) tick 중 frac 이상이 임계 위(_hold_ok).
        hold_frac = 1: 임계 위가 끊기지 않고 hold 초 이어짐(_held).
        """
        r, t = self.rules, self.ticks[-1]
        thr, hold, frac = r.get(key_thr), r.get(key_hold), float(r.get("hold_frac") or 1.0)
        tick = float(r.get("tick") or 10)
        if thr is None or hold is None:
            return {"ok": False, "progress": 0.0, "left_s": None}
        # 단계에 들어온 tick 은 그 단계의 판정을 하지 않는다(engine: 다음 tick 부터 기록) — 그래서 첫 tick 을 뺀다
        hist = [(x["at"], x["c"] >= thr) for x in self.ticks if x["since"] == t["since"] and x["at"] > t["since"]]
        if t["c"] < thr:
            win = [a for x, a in hist if x > now - hold]
            return {"ok": False, "progress": 0.0, "left_s": None, "thr": thr, "hold": hold,
                    "ratio": round(sum(win) / len(win), 2) if win else 0.0}
        last = t["at"]
        t0 = hist[0][0] if hist else last + tick
        for m in range(0, int(hold / tick) + 4):
            T = last + m * tick
            h = hist + [(last + k * tick, True) for k in range(1, m + 1)]
            if not h:
                continue
            if frac < 1.0:
                win = [a for x, a in h if x > T - hold]
                ok = T - t0 >= hold and win and sum(win) / len(win) >= frac
            else:
                below = [x for x, a in h if not a]
                run0 = min(x for x, a in h if a and (not below or x > max(below)))
                ok = T - run0 >= hold
            if ok:
                left = max(0.0, T - now)
                return {"ok": m == 0, "progress": max(0.0, min(1.0, 1 - left / hold)), "left_s": round(left),
                        "thr": thr, "hold": hold, "frac": frac}
        return {"ok": False, "progress": 0.0, "left_s": None, "thr": thr, "hold": hold, "frac": frac}

    def _posture_part(self, t: dict[str, Any]) -> dict[str, Any]:
        """hub 가 센 자세와 그 피로 기여(w·δ, 추정). 하한·min_confidence 아래면 δ 0."""
        r = self.rules
        p, conf = t.get("posture"), t.get("conf")
        floor = (r.get("floors") or {}).get(p)
        counted = p is not None and conf is not None and conf >= (r.get("min_conf") or 0) and (floor is None or conf >= floor)
        label_d = float((r.get("posture_delta") or {}).get(p, 0.0)) if counted else 0.0
        # ingest: delta = max(posture_delta[라벨], min(nod_rate / nod_rate_full_hz, nod_delta_max), 졸음 플래그)
        nod_d = 0.0
        if t.get("nod_hz") is not None and r.get("nod_full"):
            nod_d = min(t["nod_hz"] / float(r["nod_full"]), float(r.get("nod_max") if r.get("nod_max") is not None else 1.0))
        flag_d = float(r.get("rule_drowsy") if r.get("rule_drowsy") is not None else 1.0) if t.get("rule_drowsy") else 0.0
        delta = max(label_d, nod_d, flag_d)
        why_d = "라벨" if delta == label_d and label_d > 0 else "끄덕임" if delta == nod_d and nod_d > 0 else \
            "규칙 졸음 플래그" if delta == flag_d and flag_d > 0 else "없음"
        ctx = {"pc": "pc", "npc": "npc"}.get(str(t.get("context")), "npc")
        w = (r.get("weights") or {}).get(ctx) or {}
        # 타이핑이 없으면 keystroke 가, 호흡은 respiration_enabled false(기본)라 빠지고 남은 가중치로 재정규화한다
        total = sum(float(v) for k, v in w.items() if k not in ("keystroke", "respiration")) or 1.0
        wp = float(w.get("posture", 0.0))
        share = wp * delta / total
        return {"posture": p, "conf": conf, "floor": floor, "counted": counted, "delta": round(delta, 3), "w": wp,
                "share": round(share, 3), "dominant": delta > 0 and share >= 0.5 * t["c"], "label_d": label_d,
                "nod_d": round(nod_d, 3), "flag_d": flag_d, "why_d": why_d, "weights": w, "ctx": ctx}

    def breakdown(self, t: dict[str, Any], pp: dict[str, Any]) -> dict[str, Any]:
        """C_fatigue 신호별 몫(w × δ / Σw). hub 는 자세 말고는 δ 를 싣지 않아서, 자세 몫만 계산하고(추정) 나머지는
        c_fsm − 자세 몫으로 묶어 보이고 단서(env_flags·타이핑·경과)를 붙인다. 학습기는 c_fsm → c_adj(= 화면의 C_fatigue)."""
        w = pp["weights"]
        typing = t.get("typing")
        avail = {"posture": True, "environment": True, "elapsed": True, "keystroke": bool(typing), "respiration": False}
        total = sum(float(w.get(k, 0)) for k, a in avail.items() if a) or 1.0
        c_fsm = t.get("c_fsm") if t.get("c_fsm") is not None else t["c"]
        post = float(w.get("posture", 0)) * pp["delta"] / total
        rows = [{"key": "posture", "name": "자세", "w": w.get("posture"), "delta": pp["delta"], "share": round(post, 3),
                 "note": f"{pp['why_d']} (라벨 {pp['label_d']} · 끄덕임 {pp['nod_d']} · 플래그 {pp['flag_d']})", "est": True}]
        hints = {"keystroke": "타이핑 중 — 공백·리듬·정정에서" if typing else "타이핑 없음 → 빠짐(재정규화)",
                 "environment": ("환경 경고 " + ", ".join(t["env_flags"])) if t.get("env_flags") else "환경 경고 없음",
                 "elapsed": "작업 경과 시간에서", "respiration": "호흡 꺼짐(respiration_enabled false) → 빠짐"}
        for k, nm in (("keystroke", "키 입력"), ("environment", "환경"), ("elapsed", "경과"), ("respiration", "호흡")):
            rows.append({"key": k, "name": nm, "w": w.get(k), "delta": None,
                         "share_max": round(float(w.get(k, 0)) / total, 3) if avail[k] else 0.0, "note": hints[k], "est": True})
        rest = max(0.0, c_fsm - post)
        # 자세 몫 추정이 c_fsm 보다 크면 hub 가 이 tick 에 다른 자세 값(신선도·서비스 낡음 → fallback 등)을 쓴 것 — 숨기지 않고 적는다
        mismatch = post > c_fsm + 0.02
        return {"mismatch": mismatch, "rows": rows, "sum_w": round(total, 3), "c_fsm": round(c_fsm, 3), "posture_share": round(post, 3),
                "rest": round(rest, 3), "c_adj": t.get("c_adj"), "alpha": t.get("alpha"), "c_shown": t["c"], "ctx": pp["ctx"]}

    def view(self, now: float, control_cooldown_from: dict[str, float] | None = None) -> dict[str, Any]:
        r = self.rules
        if r.get("error"):
            return {"error": r["error"], "source": r.get("source"), "profile": r.get("profile")}
        if not self.ticks:
            return {"error": None, "headline": f"{AWAY_TEXT} (유지 판정·그래프에서 뺌)" if self.away_since is not None
                    else "hub state/phase 대기", "checks": [], "series": [], "marks": [], "away": self.away_since is not None,
                    "rules": self._rules_brief(), "source": r.get("source"), "profile": r.get("profile")}
        t = self.ticks[-1]
        c, state = t["c"], t["state"]
        pp = self._posture_part(t)
        gate = "auto" if r.get("conf_auto") is not None and c >= r["conf_auto"] else \
            "suggest" if r.get("conf_suggest") is not None and c >= r["conf_suggest"] else "none"
        gate_ko = {"auto": "자동", "suggest": "제안 카드", "none": "무동작"}[gate]
        acts = r.get("posture_actions") or []
        act_text = " + ".join(f"{'무드등' if a.get('target_id') == 'desk_lamp' else a.get('target_id')} "
                              f"{op_text(str(a.get('operation')), a.get('value'))}" for a in acts) or "기기 명령 없음(카드만)"
        act_text = act_text.replace("무드등 밝기", "무드등")   # "무드등 +60 % (최소 153)"
        # hub ad76ff0: 자세 개입 때 개인화(밴딧)가 고른 arm 이 실행된다 — wake_light 일 때만 무드등. 정책 결정이 없으면 기본(무드등).
        la = self.last_arm if self.last_arm and now - self.last_arm.get("at", 0) <= 600 and \
            self.last_arm.get("arm") in POSTURE_ARMS else None
        if la:
            choice = act_text if la["arm"] == "wake_light" else f"{la['label']}(무드등 아님)"
            act_text = f"자세 개입 — 개인화가 고른 것: {choice}"
        else:
            act_text = f"자세 개입 — 개인화가 고름(무드등·음성 안내·알림음·자세 카드 중, 결정 없으면 {act_text})"
        last = max(self._last_lamp.values()) if self._last_lamp else None
        if control_cooldown_from:
            last = max([x for x in [last, *control_cooldown_from.values()] if x is not None], default=None)
        cool_left = None if last is None or r.get("cooldown") is None else max(0, round(r["cooldown"] - (now - last)))
        tick = float(r.get("tick") or 10)
        elev = self._hold("focus_low", "elevated_hold", now) if state in FOCUS_FSM else None
        conf = self._hold("confirm", "confirm_hold", now) if state == "FATIGUE_SUSPECT" else None
        # 한 줄 결론
        eta = None
        if state in FOCUS_FSM:
            if elev and elev["left_s"] is not None:
                # 의심 진입 tick 은 확정 판정을 안 하므로 + tick, 확정 뒤 원인 분석·자세 개입 2 tick
                eta = elev["left_s"] + tick + (r.get("confirm_hold") or 0) + 2 * tick
                head = f"지금 상태가 약 {round(eta)}초 더 이어지면 {act_text} ({gate_ko}) — 추정" if pp["dominant"] \
                    else f"피로 {c:.2f} ≥ {elev['thr']:.2f} — 의심까지 {elev['left_s']}초(추정), 다만 자세 원인이 아님 → 무드등 아닐 수 있음"
            else:
                head = f"올리지 않음: 피로 {c:.2f} < {r.get('focus_low')}"
        elif state == "FATIGUE_SUSPECT":
            if conf and conf["left_s"] is not None:
                eta = conf["left_s"] + 2 * tick
                head = (f"지금 상태가 약 {round(eta)}초 더 이어지면 {act_text} ({gate_ko}) — 추정" if pp["dominant"]
                        else f"피로 {c:.2f} ≥ {conf['thr']:.2f} — 확정까지 {conf['left_s']}초(추정), 자세 원인이 아니면 무드등 아님")
            else:
                head = f"올리지 않음: 피로 {c:.2f} < {r.get('confirm')} (의심 유지)"
        elif state in ("FATIGUE", "CAUSE_ANALYSIS"):
            head = f"피로 확정 — 다음 tick 원인 분석{' → 자세 → ' + act_text + ' (' + gate_ko + ')' if pp['dominant'] else ''}"
        elif state == "ACTION_POSTURE":
            sent = bool(self._last_lamp) and now - max(self._last_lamp.values()) <= 2 * tick
            head = (f"무드등 올림 — hub 가 무드등 명령을 보냄 ({gate_ko})" if sent
                    else f"{act_text} ({gate_ko})" + (" · 쿨다운이라 생략될 수 있음" if cool_left else ""))
        elif state == "FOCUS_BREAK":
            head = "집중 저하(FOCUS_BREAK) — 관찰이 끝난 뒤 피로가 오르면 피로 의심으로, 아직 무드등 판단 단계 아님"
        else:
            head = f"{FSM_KO.get(state, state)} ({state}) — 무드등 판단 단계 아님"
        if cool_left and state != "ACTION_POSTURE":
            head += f" · 쿨다운 {cool_left}초 남음"
        checks = [
            {"name": "FSM 상태", "ok": state in ("FATIGUE_SUSPECT", "FATIGUE", "CAUSE_ANALYSIS", "ACTION_POSTURE"),
             "value": f"{FSM_KO.get(state, state)} ({state})"},
            {"name": f"피로도 ≥ 의심 {r.get('suspect')}", "ok": c >= (r.get("suspect") or 1), "value": f"{c:.2f}"},
            {"name": f"피로도 ≥ 확정 {r.get('confirm')}", "ok": c >= (r.get("confirm") or 1), "value": f"{c:.2f}"},
            {"name": f"의심 유지 {r.get('elevated_hold')} s (비율 ≥ {r.get('hold_frac')})", "ok": state in PAST_SUSPECT,
             "value": "통과" if state in PAST_SUSPECT else
             (f"{elev['left_s']} s 남음(추정)" if elev and elev["left_s"] is not None else "임계 아래") if state in FOCUS_FSM
             else "해당 없음(이 상태에선 판정 안 함)",
             "progress": 1.0 if state in PAST_SUSPECT else (elev or {}).get("progress", 0.0)},
            {"name": f"확정 유지 {r.get('confirm_hold')} s", "ok": state in ("FATIGUE", "CAUSE_ANALYSIS", "ACTION_POSTURE"),
             "value": (f"{conf['left_s']} s 남음(추정)" if conf and conf["left_s"] is not None else "임계 아래") if state == "FATIGUE_SUSPECT"
             else ("통과" if state in ("FATIGUE", "CAUSE_ANALYSIS", "ACTION_POSTURE") else "의심 전"),
             "progress": (conf or {}).get("progress", 0.0) if state == "FATIGUE_SUSPECT"
             else (1.0 if state in ("FATIGUE", "CAUSE_ANALYSIS", "ACTION_POSTURE") else 0.0)},
            {"name": "hub 가 센 자세", "ok": pp["counted"] and pp["delta"] > 0,
             "value": f"{POSTURE_KO.get(str(pp['posture']), pp['posture'] or '-')} {pp['conf'] if pp['conf'] is None else round(pp['conf'], 2)}"
                      + (f" (하한 {pp['floor']})" if pp["floor"] is not None else "") + f" → δ {pp['delta']}"},
            {"name": "주원인 = 자세 (추정)", "ok": pp["dominant"],
             "value": f"자세 기여 {pp['w']}×{pp['delta']} → {pp['share']:.2f} / 피로 {c:.2f}"},
            {"name": f"게이트 (자동 ≥ {r.get('conf_auto')}, 카드 ≥ {r.get('conf_suggest')})", "ok": gate != "none",
             "value": f"{gate_ko} ({c:.2f})"},
            {"name": f"쿨다운 {r.get('cooldown')} s", "ok": not cool_left,
             "value": "없음" if not cool_left else f"{cool_left} s 남음"},
        ]
        series = [{"at": x["at"], "c": round(x["c"], 3), "state": x["state"]} for x in self.ticks]
        marks = [m for m in self.marks if now - m["at"] <= self.keep_s]
        away = self.away_since is not None
        if away:
            head, eta = f"{AWAY_TEXT} — {round(now - self.away_since)} 초째, 유지 판정·그래프에서 뺌 (자리 비움 tick {self.away_ticks})", None
        return {"error": None, "headline": head, "eta_s": None if eta is None else round(eta), "checks": checks,
                "away": away,
                "breakdown": self.breakdown(t, pp), "prior": self._prior,
                "last_arm": self.last_arm, "arm_counts": self.arm_counts,
                "series": series, "marks": marks, "now": now, "window_s": self.keep_s, "rules": self._rules_brief(),
                "source": r.get("source"), "profile": r.get("profile")}

    def _rules_brief(self) -> dict[str, Any]:
        r = self.rules
        return {k: r.get(k) for k in ("suspect", "confirm", "natural", "elevated_hold", "confirm_hold", "hold_frac",
                                       "conf_auto", "conf_suggest", "cooldown", "floors", "tick")}


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
        self.virtual = VirtualHome()
        self.trigger = TriggerTracker({"error": "hub 설정을 아직 안 읽음(--hub-config)"})
        self.laptop: LaptopActuator | None = None
        # 화면에서 보내기(프로필·카드 응답). None 이면 보내기 끔
        self.publisher: Publisher | None = None
        self.sent: deque[dict[str, Any]] = deque(maxlen=20)
        self.profile_sent: dict[str, Any] | None = None
        # 연결별 상태: ok, reason, last(마지막 수신 epoch)
        self.links: dict[str, dict[str, Any]] = {
            k: {"ok": False, "reason": "아직 연결 시도 전", "last": None} for k in ("mqtt", "posture", "raw")}
        self.mode = "board"
        self.target = ""
        self.hub_profile = ""  # --hub-profile: 보드가 싣지 않는 시연 프로파일을 노트북에서 적은 값
        self.hub_floors: dict[str, float] = dict(HUB_FLOORS_DEFAULT)   # --hub-floor

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
                self.virtual.on_phase(env, at)
                self.trigger.on_phase(env, at)
            elif topic == TOPIC_CMD:
                self.virtual.on_cmd(env, at)
                self.trigger.on_cmd(env, at)
            elif topic == TOPIC_RESULT:
                self.virtual.on_result(env)
            elif topic == TOPIC_REQUEST:
                self.virtual.on_request(env, at)
                self.trigger.on_request(env, at)
            elif topic == TOPIC_FEEDBACK:
                self.virtual.on_feedback(env, at)
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

    # ---------------------------------------------------------------- 화면에서 보내기(사용자 요청: 프로필·카드 응답만)
    def _send(self, topic: str, data: dict[str, Any], what: str) -> dict[str, Any]:
        pub = self.publisher
        if pub is None:
            return {"ok": False, "error": "보내기 꺼짐(--read-only)"}
        try:
            pub.publish(topic, data)     # 네트워크는 lock 밖에서
            res = {"ok": True, "error": None}
        except (OSError, ConnectionError, ValueError) as e:
            res = {"ok": False, "error": _reason(e)}
        with self.lock:
            self.sent.append({"t": time.strftime("%H:%M:%S"), "topic": topic, "what": what, **res})
        return res

    def send_profile(self, data: Any, label: str = "") -> dict[str, Any]:
        prof, err = validate_profile(data)
        if prof is None:
            return {"ok": False, "error": err}
        res = self._send(TOPIC_PROFILE, prof, f"프로필 {label or prof['profile_id']}")
        if res["ok"]:
            with self.lock:
                self.profile_sent = {"profile_id": prof["profile_id"], "label": label, "at": time.time(), "data": prof}
        return res

    def send_feedback(self, request_id: Any, verdict: Any) -> dict[str, Any]:
        """Pi 5 앱과 같은 data: {request_id, verdict, response_ms(, kind)}. 되돌리기 = 자동 실행 뒤 reject.
        dismiss = 휴식 알림·자동 안내 닫기(hub 는 counts.dismissed 만 센다 — FSM·제어·ESM 에 안 들어감)."""
        if verdict not in ("accept", "reject", "undo", "dismiss"):
            return {"ok": False, "error": "verdict 는 accept|reject|undo|dismiss"}
        now = time.time()
        if verdict == "dismiss":
            with self.lock:
                target = self.virtual.dismissable(str(request_id), now)
            if target is None:
                return {"ok": False, "error": "닫을 휴식 알림·자동 안내가 아님(시한 지남·이미 닫음)"}
            res = self._send(TOPIC_FEEDBACK, {"request_id": request_id, "verdict": "dismiss",
                                              "response_ms": max(0, int((now - target["at"]) * 1000))},
                             f"닫기 {target['what']}")
            if res["ok"]:
                with self.lock:
                    self.virtual.mark_dismissed(str(request_id), now)
            return res
        with self.lock:
            card = self.virtual.pending_card(now)
            undo = self.virtual.undoable(now)
        if verdict == "undo":
            if undo is None:
                return {"ok": False, "error": "되돌릴 자동 실행이 없음(60 s 창)"}
            # 앱은 지금 걸린 질문이 없으면 'atlas-display' 를 싣는다 — hub 는 자동 실행 뒤 reject 를 auto_undo 로 받는다
            rid = card["request_id"] if card else "atlas-display"
            return self._send(TOPIC_FEEDBACK, {"request_id": rid, "verdict": "reject", "response_ms": 0},
                              f"되돌리기 — {undo['text']}")
        if card is None or card["request_id"] != request_id:
            return {"ok": False, "error": "지금 떠 있는 카드가 아님(만료·응답됨)"}
        data = {"request_id": card["request_id"], "verdict": verdict,
                "response_ms": max(0, int((now - card["shown_at"]) * 1000))}
        if card.get("kind"):
            data["kind"] = card["kind"]
        res = self._send(TOPIC_FEEDBACK, data, f"카드 {card.get('kind')} {verdict}")
        if res["ok"]:
            # 브로커를 돌아 내 응답이 다시 들어오기 전에도 이 카드는 답한 것 — 바로 이은 되돌리기가 이 카드를 겨누지 않게
            with self.lock:
                req = self.virtual._requests.get(str(card["request_id"]))
                if req is not None and req.get("verdict") is None:
                    req["verdict"] = verdict
        return res

    def persona_view(self) -> dict[str, Any]:
        """프로필 보내기 상태와 hub 확인(state/phase 개인화의 active_profile_id 가 보낸 id 와 같으면 적용됨)."""
        data = _map(_map(self.phase).get("data"))
        pers = _map(_map(data.get("sensor_summary")).get("personalize"))
        hub_id = pers.get("active_profile_id")
        sent = self.profile_sent
        return {"enabled": self.publisher is not None, "presets": PERSONAS,
                "sent": None if sent is None else {k: sent[k] for k in ("profile_id", "label", "data")} |
                {"t": time.strftime("%H:%M:%S", time.localtime(sent["at"]))},
                "hub_active": hub_id or DASH,
                "hub_fields": {k: pers[k] for k in ("active_profile_id", "arm", "source", "delivered_gate") if pers.get(k)},
                "prior": prior_text(pers),
                "ack": bool(sent and hub_id == sent["profile_id"]),
                "hub_profile": self.hub_profile or DASH, "sent_log": list(self.sent)[::-1][:8]}

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
                "page_version": _page_version(),
                "virtual": self.virtual.view(now),
                "trigger": self.trigger.view(now),
                "laptop": None if self.laptop is None else self.laptop.view(),
                "persona": self.persona_view(),
                "control": control,
                "keystroke": self.keystroke.view(now),
                "video": video_view(self.phase, self.posture, control),
                "mode": self.mode,
                "target": self.target,
                "now": time.strftime("%H:%M:%S"),
                "links": links,
                "phase": phase_view(self.phase),
                "profile": profile_text(self.phase, self.profile_user, self.health_hub, self.hub_profile),
                "posture": posture_view(self.posture, self.hub_floors) if self.posture is not None else None,
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


PUBLISH_NODE = "laptop-devview"
# 화면에서 보낼 수 있는 토픽은 이 둘뿐이다(사용자 요청 10-26: 프로필 고르기·카드 응답). 나머지는 계속 읽기만.
PUBLISH_TOPICS = {TOPIC_PROFILE: True, TOPIC_FEEDBACK: False}   # topic → retain


class Publisher:
    """짧은 연결로 하나 보내고 끊는다: CONNECT → PUBLISH(QoS 1) → PUBACK 기다림 → DISCONNECT.

    envelope 는 Pi 5 앱·V18 과 같은 공통 모양(schema_version·ts·node·boot_id·seq·data), node = laptop-devview.
    """

    def __init__(self, host: str, port: int = 1883, timeout: float = 3.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.boot_id = f"devview-{int(time.time() * 1000)}"
        self.seq = 0
        self.lock = threading.Lock()

    def envelope(self, data: dict[str, Any]) -> bytes:
        self.seq += 1
        env = {"schema_version": "1.0", "ts": round(time.time(), 3), "node": PUBLISH_NODE, "boot_id": self.boot_id,
               "seq": self.seq, "data": data}
        return json.dumps(env, ensure_ascii=False).encode("utf-8")

    def publish(self, topic: str, data: dict[str, Any]) -> bytes:
        if topic not in PUBLISH_TOPICS:
            raise ValueError(f"보낼 수 없는 토픽: {topic}")
        with self.lock:
            payload = self.envelope(data)
            pid = self.seq % 65535 + 1
            header = 0x32 | (0x01 if PUBLISH_TOPICS[topic] else 0)   # PUBLISH, QoS 1, retain
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(connect_packet(f"devview-pub-{os.getpid()}", 10))
                h, body = read_packet(sock)
                if h >> 4 != 2 or len(body) < 2 or body[1] != 0:
                    raise ConnectionError("CONNACK 거부")
                sock.sendall(_packet(header, _mqtt_str(topic) + struct.pack("!H", pid) + payload))
                h, body = read_packet(sock)
                if h >> 4 != 4 or body[:2] != struct.pack("!H", pid):
                    raise ConnectionError("PUBACK 없음")
                sock.sendall(b"\xe0\x00")   # DISCONNECT
            return payload


class LoopbackPublisher(Publisher):
    """--fake: 브로커 대신 store 로 바로 넣는다(화면·각본이 같은 길을 탄다)."""

    def __init__(self, store: "Store") -> None:
        super().__init__("loopback")
        self.store = store

    def publish(self, topic: str, data: dict[str, Any]) -> bytes:
        if topic not in PUBLISH_TOPICS:
            raise ValueError(f"보낼 수 없는 토픽: {topic}")
        payload = self.envelope(data)
        self.store.on_mqtt(topic, payload)
        return payload


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
    27 s 에 무드등 adjust_brightness +60 %(min_raw 153) 자동 명령과 앱 규칙 결과(110→176), 31 s 선풍기 제안 카드
    (화면에서 답하거나 39 s 에 가짜 앱이 수락 → 선풍기 켬), 32 s 부터 엎드림(face_down 0.90→0.995). /raw other(0x20·0x10·0x03·0x04·0xF0)·키 입력 토픽·프로필도 낸다.
    """

    STAGES = [(0, "START", "start"), (6, "FOCUS_PC", "focus"), (16, "FATIGUE_SUSPECT", "fatigue"),
              (22, "FATIGUE", "fatigue"), (27, "ACTION_POSTURE", "fatigue")]   # hub presentation._PHASE_BY_STATE: ACTION_* = fatigue

    def __init__(self, store: Store) -> None:
        self.store = store
        self.seq = 0
        self.cycle = -1
        self.fired: set[str] = set()

    def _active_profile(self) -> str:
        """가짜 hub: 마지막으로 받은 profile/user 의 profile_id(없으면 default) — 보드 hub 의 active_profile_id 와 같은 뜻."""
        prof = _map(_map(self.store.profile_user).get("data"))
        return str(prof.get("profile_id") or "default")

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
                "personalize": {"active_profile_id": self._active_profile(), "decision_source": "policy",
                                **({"arm": "wake_light", "arm_label": "무드등 밝혀 깨우기",
                                    "explanation": "지난 졸음 때 무드등 밝기 올림이 가장 잘 깨움(성공 4/5) → 같은 방법"}
                                   if state == "ACTION_POSTURE" else {})},
            }
            if u >= 22:
                summary["env_flags"] = ["co2_rising"]
            st.on_mqtt(TOPIC_PHASE, _fake_env(self.seq, {
                "fsm_state": state, "phase": dict((n, p) for _, n, p in self.STAGES)[state], "context": "pc",
                "c_fatigue": round(fatigue, 3), "c_focus": focus, "gate": gate, "cause": cause,
                "reasons": ["tof_drowsy", "mmwave_still"] if drowsy else [],
                "sensor_summary": summary}))
        cid, rid, fid = f"fk{cycle:04d}lamp", f"fk{cycle:04d}req", f"fk{cycle:04d}fan"
        exp = int((time.time() + 15) * 1000)   # dispatcher: expires_ts_ms = 보낸 시각 + result_timeout_sec(15)
        events = [
            # control.yaml actions.posture 와 같은 명령
            (27.0, "cmd", TOPIC_CMD, {"command_id": cid, "target_id": "desk_lamp", "operation": "adjust_brightness",
                                     "value": {"percent": 60, "min_raw": 153}, "gate": "auto", "cause": "posture",
                                     "origin_state": "ACTION_POSTURE", "requires_confirmation": False,
                                     "expires_ts_ms": exp}, "hub"),
            (27.3, "res", TOPIC_RESULT, None, "display-control"),
            (31.0, "req", TOPIC_REQUEST, {"request_id": rid, "kind": "env_suggest",
                                         "prompt_code": "adjust_environment", "cause": "environment",
                                         "evidence": ["co2_rising"], "options": ["accept", "reject"],
                                         "explanation": "CO₂ 가 계속 오름 — 선풍기를 켜 환기할까요?",
                                         "arm": "fan_on", "arm_label": "환기팬(선풍기) 켜기", "expires_in_s": 30}, "hub"),
            # 아무도 답하지 않으면 39 s 에 가짜 Pi 5 가 수락한다(화면에서 먼저 누르면 그 답을 따른다)
            (39.0, "fb", TOPIC_FEEDBACK, {"request_id": rid, "verdict": "accept", "response_ms": 8000,
                                         "kind": "env_suggest"}, "display"),
        ]
        for at, key, topic, data, node in events:
            if u >= at and key not in self.fired:
                if key == "fb" and self._answered(rid) is not None:
                    self.fired.add(key)
                    continue
                self.fired.add(key)
                self.seq += 1
                if topic == TOPIC_RESULT:
                    data = self._result_like_app(cid)
                st.on_mqtt(topic, _fake_env(self.seq, data, node))
        # 제안을 수락하면(화면·가짜 앱 누구든) hub 가 선풍기를 켠다 — suggest 게이트는 수락 뒤 실행
        if self._answered(rid) == "accept" and "cmd2" not in self.fired:
            self.fired.add("cmd2")
            self.seq += 1
            st.on_mqtt(TOPIC_CMD, _fake_env(self.seq, {
                "command_id": fid, "target_id": "vent_fan", "operation": "set_power", "value": "on", "gate": "suggest",
                "cause": "environment", "origin_state": "ACTION_ENV", "requires_confirmation": False,
                "expires_ts_ms": exp}, "hub"))
            self.seq += 1
            st.on_mqtt(TOPIC_RESULT, _fake_env(self.seq, self._result_like_app(fid), "display-control"))

    def _answered(self, rid: str) -> str | None:
        with self.store.lock:
            req = self.store.virtual._requests.get(rid)
            return None if req is None else req.get("verdict")

    def _result_like_app(self, cid: str) -> dict[str, Any]:
        """가짜 Pi 5 의 결과 = 앱 규칙으로 돌린 가상 실행 결과(VirtualHome 이 이미 같은 규칙으로 계산했다)."""
        with self.store.lock:
            e = self.store.virtual._by_cmd.get(cid) or {}
        return {"command_id": cid, **(e.get("virtual") or {"status": "failed", "error_code": "unknown"})}


class FakeHubScenario(FakeScenario):
    """--fake-scenario facedown|recline: hub 규칙(load_hub_rules)대로 도는 가짜 FSM 으로 '무드등 올릴 시점'을 시험한다.

    facedown: 15 s 부터 90 s 동안 엎드림(서비스 0.995, hub 가 셈) → 의심 → 확정 → 자세 개입(자동) → 무드등 명령.
    recline:  15 s 부터 계속 뒤로 젖힘(δ 0.6) → 의심까지만, 확정(0.60)에 못 미쳐 무드등 없음.
    피로도 = Σ w·δ / Σ w (keystroke·respiration 빠짐, 경과 δ 0.1, 환경 0) — 설정에서 읽은 가중치로.
    """

    PERIOD = 240.0

    def __init__(self, store: Store, kind: str, rules: dict[str, Any]) -> None:
        super().__init__(store)
        self.kind, self.rules = kind, rules
        self.state, self.since, self.hold_t0, self.hist = "FOCUS_NPC", 0.0, None, []
        self.next_tick = 0.0
        self.cause, self.gate = None, "none"

    def _posture(self, t: float) -> str:
        u = t % self.PERIOD
        if self.kind in ("rest", "rest_old"):
            return "upright"
        if self.kind == "facedown":
            return "face_down" if 15 <= u < 105 else "upright"
        return "recline" if u >= 15 else "upright"

    def probs(self, t: float, sensor: int | None = None) -> dict[str, float]:
        pst = self._posture(t)
        top = 0.995 if pst == "face_down" else 0.86 if pst == "recline" else 0.9
        rest = (1.0 - top) / (len(LABELS) - 1)
        out = {l: rest for l in LABELS}
        out[pst] = top
        return out

    def _c(self, t: float) -> float:
        r = self.rules
        w = (r.get("weights") or {}).get("npc") or {}
        total = sum(float(v) for k, v in w.items() if k not in ("keystroke", "respiration")) or 1.0
        pst = self._posture(t)
        conf = self.probs(t)[pst]
        floor = (r.get("floors") or {}).get(pst)
        delta = float((r.get("posture_delta") or {}).get(pst, 0.0)) if floor is None or conf >= floor else 0.0
        return (float(w.get("posture", 0)) * delta + float(w.get("elapsed", 0)) * 0.1) / total

    def _goto(self, state: str, t: float) -> None:
        self.state, self.since, self.hold_t0, self.hist = state, t, None, []

    def _held(self, thr: float, hold: float, c: float, t: float) -> bool:
        """engine._hold_note·_hold_ok: 이 단계 첫 기록(t0)부터 hold 초, 지난 hold 초 기록 중 frac 이상이 임계 위."""
        if self.hold_t0 is None:
            self.hold_t0 = t
        self.hist.append((t, c >= thr))
        self.hist = [(x, a) for x, a in self.hist if x > t - hold]
        frac = self.rules.get("hold_frac") or 1.0
        return c >= thr and t - self.hold_t0 >= hold and sum(1 for _, a in self.hist if a) / len(self.hist) >= frac

    def _rest_fsm(self, t: float) -> None:
        """휴식 각본: 10 s 휴식 제안(ACTION_BREAK) → 20 s 수락돼 REST(rest_min_sec) → RECOVERY → 집중.
        rest 는 hub 새 동작(rest_notice 카드 + speak 명령), rest_old 는 구버전(state/phase REST 만)."""
        u = t % self.PERIOD
        rest_s = float(self.rules.get("rest_min") or 60)
        new = ("FOCUS_NPC" if u < 10 else "ACTION_BREAK" if u < 20 else "REST" if u < 20 + rest_s
               else "RECOVERY" if u < 30 + rest_s else "FOCUS_NPC")
        if new != self.state:
            self._goto(new, t)
            self.gate = "suggest" if new == "ACTION_BREAK" else "none"
            self.cause = "cognitive" if new in ("ACTION_BREAK", "REST") else None
            if new == "REST" and self.kind == "rest":
                msg = "잠깐 쉬어 가요. 눈을 감고 어깨를 천천히 풀어 보세요."
                self.seq += 1
                self.store.on_mqtt(TOPIC_REQUEST, _fake_env(self.seq, {
                    "request_id": f"fk-{int(t)}-rest", "kind": "rest_notice", "prompt_code": "rest_now", "options": ["dismiss"],
                    "expires_in_s": 60, "reason": "accepted", "cause": "cognitive", "text": msg,
                    "minutes": round(rest_s / 60, 2), "rest_min_sec": rest_s, "speak": True, "variant": "default",
                    "evidence": ["elapsed", "keystroke"], "c_fatigue": 0.5}))
                self.seq += 1
                self.store.on_mqtt(TOPIC_CMD, _fake_env(self.seq, {
                    "command_id": f"fk-{int(t)}-speak", "target_id": "speaker", "operation": "speak",
                    "value": {"text": msg, "lang": "ko"}, "gate": "notice", "origin_state": "REST",
                    "requires_confirmation": False, "expires_ts_ms": int((time.time() + 15) * 1000)}))
        self.c_now = 0.2 if new in ("FOCUS_NPC", "RECOVERY") else 0.5

    def _fsm(self, t: float) -> None:
        if self.kind in ("rest", "rest_old"):
            self._rest_fsm(t)
            return
        r, c = self.rules, self._c(t)
        st = self.state
        if st == "FOCUS_NPC":
            if self._held(r["focus_low"], r["elevated_hold"], c, t):   # 아래인 tick 도 기록된다(hold_frac < 1)
                self._goto("FATIGUE_SUSPECT", t)
        elif st == "FATIGUE_SUSPECT":
            if c < r["natural"]:
                self._goto("FOCUS_NPC", t)
            elif self._held(r["confirm"], r["confirm_hold"], c, t):
                self._goto("FATIGUE", t)
        elif st == "FATIGUE":
            self._goto("CAUSE_ANALYSIS", t)
        elif st == "CAUSE_ANALYSIS":
            self.cause = "posture"
            self.gate = "auto" if c >= r["conf_auto"] else "suggest" if c >= r["conf_suggest"] else "none"
            self._goto("ACTION_POSTURE", t)
            if self.gate == "auto":
                self._send_posture_cmd(t)
        elif st == "ACTION_POSTURE":
            self._goto("MONITOR", t)
        elif st == "MONITOR":
            if self._posture(t) not in ("face_down", "drowsy"):
                self._goto("RECOVERY", t)
        elif st == "RECOVERY":
            self._goto("FOCUS_NPC", t)
        self.c_now = c

    def _send_posture_cmd(self, t: float) -> None:
        for i, a in enumerate(self.rules.get("posture_actions") or []):
            cid = f"fkh{int(t)}{i}"
            self.seq += 1
            self.store.on_mqtt(TOPIC_CMD, _fake_env(self.seq, {
                "command_id": cid, "target_id": a.get("target_id"), "operation": a.get("operation"), "value": a.get("value"),
                "gate": "auto", "cause": "posture", "origin_state": "ACTION_POSTURE", "requires_confirmation": False,
                "expires_ts_ms": int((time.time() + 15) * 1000)}))
            self.seq += 1
            self.store.on_mqtt(TOPIC_RESULT, _fake_env(self.seq, self._result_like_app(cid), "display-control"))

    def step(self, t: float) -> None:
        st = self.store
        n = int(t * 5)
        st.set_raw({"dev": "fake", "frames": n, "type5": n * 2, "sent": 0, "bad": 0, "sensors": [
            {"id": i, "have": True, "age_ms": 40, "w": 8, "h": 8, "dist_mm": self.grid(i, t),
             "status": [5] * 64, "flip_rows": False, "flip_cols": False} for i in (0, 1)]})
        pst, probs = self._posture(t), self.probs(t)
        st.set_posture({"posture": pst, "posture_ko": POSTURE_KO[pst], "conf": probs[pst], "valid": True, "present": True,
                        "online_sensors": 2, "probs": probs, "instant": {"posture": pst, "conf": probs[pst], "probs": probs},
                        "placement": "left", "model": {"file": "models/posture_net.bin", "labels": LABELS},
                        "rule": {"enabled": True, "face_down": pst == "face_down", "conf": 0.995, "share": 1 if pst == "face_down" else 0},
                        "misplaced_rule": {"mode": "and", "baseline": True, "baseline_source": "post"}})
        st.link("posture", True, f"가짜 각본 {self.kind}")
        st.link("raw", True, f"가짜 각본 {self.kind}")
        st.link("mqtt", True, f"가짜 각본 {self.kind}")
        if "once" not in self.fired:
            self.fired.add("once")
            st.on_mqtt(TOPIC_HEALTH_HUB, _fake_env(self.seq, {"status": "online"}))
        tick = float(self.rules.get("tick") or 10)
        while t >= self.next_tick:   # hub 는 10 s 마다 판정한다
            self._fsm(self.next_tick)
            self.seq += 1
            phase = {"FOCUS_NPC": "focus", "RECOVERY": "recovery", "REST": "recovery"}.get(self.state, "fatigue")
            st.on_mqtt(TOPIC_PHASE, _fake_env(self.seq, {
                "fsm_state": self.state, "phase": phase, "context": "npc", "c_fatigue": round(self.c_now, 4),
                "c_focus": 0.1, "gate": self.gate if self.state in ("ACTION_POSTURE", "ACTION_BREAK") else "none",
                "cause": self.cause if self.state in ("ACTION_POSTURE", "MONITOR") else None, "reasons": [],
                "sensor_summary": {"present": True, "presence_source": "both",
                                   "tof": {"posture": pst, "posture_confidence": probs[pst], "posture_source": "service"},
                                   "personalize": {"active_profile_id": self._active_profile(), "decision_source": "policy"}}}))
            self.next_tick += tick


def fake_loop(store: Store, stop: threading.Event, start: float = 0.0, scenario: str = "cycle",
              rules: dict[str, Any] | None = None) -> None:
    sc = FakeHubScenario(store, scenario, rules or {}) if scenario in ("facedown", "recline", "rest", "rest_old") \
        else FakeScenario(store)
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

        def do_POST(self) -> None:  # noqa: N802
            """프로필 보내기·카드 응답. 다른 사이트가 몰래 부르지 못하게 X-Devview 머리글을 요구한다
            (사용자 정의 머리글이 있으면 브라우저가 사전 요청(OPTIONS)을 하는데 여기서는 받지 않는다)."""
            path = self.path.split("?")[0]
            if self.headers.get("X-Devview") != "1":
                self._send(403, b"forbidden", "text/plain")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 16384)).decode("utf-8")) if n else {}
            except (ValueError, UnicodeDecodeError):
                self._send(400, b"bad json", "text/plain")
                return
            if path == "/api/profile":
                res = store.send_profile(_map(body).get("data"), str(_map(body).get("label") or "")[:20])
            elif path == "/api/feedback":
                res = store.send_feedback(_map(body).get("request_id"), _map(body).get("verdict"))
            else:
                self._send(404, b"not found", "text/plain")
                return
            self._send(200 if res["ok"] else 409, json.dumps(res, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="DESKMATE 노트북 개발자 화면(보기 전용 — 프로필·카드 응답만 보냄)")
    ap.add_argument("--pi4", default="172.20.10.3", help="Pi 4 주소(핫스팟 So 기준 172.20.10.3)")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--port", type=int, default=8790, help="이 화면의 로컬 포트")
    ap.add_argument("--hub-profile", default="", metavar="NAME",
                    help="hub 시연 프로파일 이름(예: demo-b). 보드가 발행하지 않아서 화면에 '(노트북 지정)' 으로 표시")
    ap.add_argument("--hub-config", default=os.path.normpath(os.path.join(HERE, "..", "..", "..", "deskmate_hub", "config")),
                    metavar="DIR", help="hub config 디렉터리(fsm·ingest·control.yaml, profiles/). 보드와 같은 hub 커밋의 것을 준다")
    ap.add_argument("--hub-floor", default="face_down=0.99,drowsy=0.80", metavar="L=C,…",
                    help="hub 가 자세를 세는 신뢰도 하한(demo-b bd47052). 화면 라벨이 이 아래면 'hub 무시' 표시. 빈 값이면 표시 안 함")
    ap.add_argument("--act-laptop", action="store_true",
                    help="이 노트북을 대역 기기로: 가상 무드등 → 화면 밝기, 가상 스피커 → 볼륨·짧은 소리(Windows). 끝나면 원래대로")
    ap.add_argument("--wake-volume", type=float, default=WAKE_VOLUME * 100, metavar="PCT",
                    help="깨우기(자세 개입 무드등·자세/휴식 말하기·알림음) 때 노트북 음량을 max(지금, PCT %%) 로 올린다 — "
                         "틀어 둔 음악으로 깨우기. 0 이면 깨우기 없음(첫 설정 소리 구간대로)")
    ap.add_argument("--wake-max-min", type=float, default=WAKE_MAX_S / 60, metavar="MIN",
                    help="깨우기 음량을 이만큼 지나면 되돌린다(회복·집중으로 가면 그 전에)")
    ap.add_argument("--read-only", action="store_true",
                    help="화면에서 보내기(프로필·카드 응답)도 끈다 — 녹화만 할 때")
    ap.add_argument("--fake", action="store_true", help="보드 없이 가짜 각본으로")
    ap.add_argument("--fake-scenario", choices=("cycle", "facedown", "recline", "rest", "rest_old"), default="cycle",
                    help="cycle = 40 s 각본 · facedown = 15 s 부터 90 s 엎드림(hub 규칙대로 → 무드등) · recline = 계속 젖힘(의심까지만)")
    ap.add_argument("--fake-start", type=float, default=0.0, metavar="S",
                    help=f"가짜 각본을 S 초부터(0–{FAKE_PERIOD:.0f}, 예: 26 = 졸음→무드등 직전)")
    args = ap.parse_args(argv)

    store = Store()
    store.hub_floors = parse_floors(args.hub_floor)
    rules = load_hub_rules(args.hub_config, args.hub_profile or ("demo-b" if args.fake else ""))
    store.trigger = TriggerTracker(rules)
    store.virtual.rest_min_s = None if rules.get("error") else rules.get("rest_min")
    if not rules.get("error") and rules.get("floors") is not None and args.hub_floor == ap.get_default("hub_floor"):
        store.hub_floors = {k: float(v) for k, v in rules["floors"].items()}   # 하한도 설정에서
    print(f"hub 규칙: {args.hub_config} · 프로파일 {rules.get('profile') or '-'}" + (f" — 못 읽음: {rules['error']}" if rules.get("error") else ""))
    stop = threading.Event()
    threads: list[threading.Thread] = []
    if args.fake:
        store.mode, store.target = "fake", "가짜 데이터"
        store.hub_profile = args.hub_profile or "demo-b"
        store.publisher = None if args.read_only else LoopbackPublisher(store)
        threads.append(threading.Thread(target=fake_loop, args=(store, stop, args.fake_start, args.fake_scenario, rules), daemon=True))
    else:
        h = args.pi4
        store.target = h
        store.hub_profile = args.hub_profile
        store.publisher = None if args.read_only else Publisher(h, args.mqtt_port)
        threads += [
            threading.Thread(target=mqtt_loop, args=(h, args.mqtt_port, store, stop), daemon=True),
            threading.Thread(target=poll_loop, args=("posture", [f"http://{h}:8771/api/state"], 0.3,
                                                     store.set_posture, store, stop), daemon=True),
            threading.Thread(target=poll_loop, args=("raw", [f"http://{h}:8772/raw", f"http://{h}:8771/raw"], 0.2,
                                                     store.set_raw, store, stop), daemon=True),
        ]
    if args.act_laptop:
        if sys.platform != "win32":
            print("--act-laptop 은 Windows 에서만 — 무시")
        else:
            import laptop_win
            state_file = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "deskmate-devview", "laptop_original.json")
            store.laptop = LaptopActuator(store, laptop_win, state_file,
                                          wake_volume=(min(100.0, args.wake_volume) / 100) if args.wake_volume > 0 else None,
                                          wake_max_s=args.wake_max_min * 60)
            store.laptop.start()
            import atexit
            atexit.register(store.laptop.restore)
            print(f"노트북 대역 기기 켬 — 원래 밝기 {store.laptop.orig['brightness']} % · 볼륨 {_pct(store.laptop.orig['volume'])}"
                  " (Ctrl+C 로 끝내면 되돌림)" + (f" — {store.laptop.error}" if store.laptop.error else ""))
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
        if store.laptop is not None:
            store.laptop.restore()
            print(f"노트북 밝기·볼륨 되돌림: {store.laptop.cur['brightness']} % · {_pct(store.laptop.cur['volume'])}"
                  + (f" — {store.laptop.error}" if store.laptop.error else ""))


if __name__ == "__main__":
    main()
