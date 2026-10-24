"""별도 자세 서비스(deskmate_tofposture, GRU) 의 판정을 hub 에 들이는 통로 (2026-10-23).

Pi 4 에는 자세 모델이 두 벌 있다 — hub 안의 순수 Python MLP(`posture/tof_mlp.py`)와, `hub/atlas` 가 0x05 격자를
tcp:9100 으로 복사해 주는 별도 서비스 `deskmate_tofposture`(PostureNet CNN+GRU, HTTP 8771). 10-23 까지 hub FSM 은
MLP 판정만 썼고 서비스 결과(/api/state)를 읽는 곳이 없었다 — 그래서 실기 FSM 의 졸음 근거가 0x03 규칙 플래그뿐이었다.
이 모듈은 `/api/state` JSON 을 `tof_service` 표본으로 캐시에 넣고, `ingest.yaml tof.posture_source: service` 면
`ingest.mapping` 이 그 자세·신뢰도로 posture Signal 을 만든다.

들어오는 길은 둘이다.
- 보드: 제한 Python 에 `_socket` 이 없어 Python 이 직접 HTTP 를 못 읽는다. `hub/atlas`(C++) 가 1 s 마다 GET 해서
  `TOFSVC\\t<json>` 한 줄로 stdin 에 써 주고(`service_bridge`), `TofServiceLineSource.feed_line` 이 받는다.
- PC·테스트: `TofServicePoller` 가 urllib 로 직접 폴링한다. urllib 는 소켓을 끌어오므로 **함수 안에서만** import 한다
  (보드 경로 import 검사 `tests/test_board_runtime.py`).

서비스 계약(hub/atlas-tof device_posture.cpp stateJson, 10-17~10-22 판 동일):
  schema "deskmate/tof_posture/1" · valid · posture(9 라벨) · index · conf(평활 확률) · since_ms · present(valid ∧ ≠away) ·
  misplaced · online_sensors · probs{label: p} · instant{posture, conf, probs} · sensors[{name, online, posture, conf, hz, …}] ·
  model{file, classes, rate_hz, fusion, labels}. 라벨 0~8 = upright, lean_forward, recline, face_down, chin_rest, drowsy, away,
  misplaced, forward_head — hub `tof.posture_phi/posture_delta` 표의 이름과 같다(misplaced 만 새로 추가).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .cache import Sample, SensorCache

SERVICE_LABELS: tuple[str, ...] = (
    "upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away", "misplaced", "forward_head",
)
LINE_PREFIX = "TOFSVC\t"
CACHE_KIND = "tof_service"

_DEFAULTS: dict[str, Any] = {
    "url": "http://127.0.0.1:8771/api/state",
    "poll_s": 1.0,
    "timeout_s": 0.5,
    "fallback": "mlp",          # 서비스가 없거나 낡았을 때: mlp(hub MLP → 규칙) | rule
    "min_online_sensors": 1,    # 이보다 적게 살아 있으면 서비스 판정을 쓰지 않는다
    "poller": "auto",           # auto(보드 네이티브 MQTT 모드가 아니면 Python 폴러) | on | off
}


def service_config(tof_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """`ingest.yaml tof.service` 에 기본값을 채운 사본."""
    out = dict(_DEFAULTS)
    out.update((tof_cfg or {}).get("service") or {})
    return out


def uses_service(tof_cfg: dict[str, Any] | None) -> bool:
    return str((tof_cfg or {}).get("posture_source", "mlp")) == "service"


def should_poll(tof_cfg: dict[str, Any] | None, environ: dict[str, str]) -> bool:
    """Python 폴러를 띄울지. 보드(`DESKMATE_NATIVE_MQTT=1`, 소켓 없음)는 C++ 가 TOFSVC 줄로 넣어 주므로 auto 면 끈다."""
    if not uses_service(tof_cfg):
        return False
    mode = str(service_config(tof_cfg).get("poller", "auto")).lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    return environ.get("DESKMATE_NATIVE_MQTT") != "1"


def parse_service_state(state: dict[str, Any]) -> dict[str, Any]:
    """`/api/state` JSON → 캐시 표본 data. 계약 필드만 남긴다(원본 격자·확률 벡터 전체는 싣지 않는다).

    posture 는 `valid` 일 때만 서비스 라벨, 아니면 unknown. present 는 서비스 값(valid ∧ ≠away)을 따르되 valid 가
    아니면 False. drowsy 는 라벨이 drowsy 인지(졸음은 FSM 에서 posture_delta 1.0 — 0x03 규칙 플래그와 같은 자리).
    """
    if not isinstance(state, dict):
        raise ValueError("service state must be an object")
    valid = bool(state.get("valid", False))
    label = str(state.get("posture") or "unknown")
    posture = label if valid and label in SERVICE_LABELS else "unknown"
    probs_raw = state.get("probs")
    probs = {str(k): round(float(v), 4) for k, v in probs_raw.items()} if isinstance(probs_raw, dict) else {}
    conf = state.get("conf")
    if conf is None:
        conf = probs.get(label, 0.0)
    online = int(state.get("online_sensors") or 0)
    data: dict[str, Any] = {
        "posture": posture,
        "posture_confidence": round(float(conf or 0.0), 4),
        "present": bool(state.get("present", posture not in ("unknown", "away"))) and valid,
        "misplaced": bool(state.get("misplaced", label == "misplaced")) and valid,
        "drowsy": valid and label == "drowsy",
        "valid": valid,
        "online_sensors": online,
        "baseline_ready": valid and online > 0,
        "source": "service",
    }
    for key in ("schema", "index", "ts_ms", "since_ms"):
        if key in state:
            data[key] = state[key]
    if probs:
        data["probs"] = probs
    model = state.get("model")
    if isinstance(model, dict):
        data["model"] = {k: model[k] for k in ("file", "classes", "rate_hz", "fusion") if k in model}
    sensors = state.get("sensors")
    if isinstance(sensors, list):
        data["sensors"] = [
            {k: s[k] for k in ("name", "online", "posture", "conf", "hz", "frames", "flip_rows", "flip_cols", "stale") if k in s}
            for s in sensors if isinstance(s, dict)
        ]
    # 10-22 판 서비스가 더 주는 것(재실 근거·mmWave 요약·로짓)은 있으면 그대로 — 실기 로그용
    for key in ("presence_source", "mmwave", "model_output"):
        if key in state:
            data[key] = state[key]
    return data


def put_service_state(cache: SensorCache, state: dict[str, Any], received: float | None = None) -> Sample:
    received = time.time() if received is None else received
    data = parse_service_state(state)
    ts = received
    sample = Sample(received=received, ts=ts, seq=None, data=data)
    cache.put(CACHE_KIND, sample)
    return sample


@dataclass
class TofServiceStats:
    lines: int = 0
    accepted: int = 0
    rejected: int = 0
    last_error: str | None = None


class TofServiceLineSource:
    """`TOFSVC\\t<json>` 줄(C++ hub/atlas 가 1 s 마다 /api/state 를 받아 넘김) → tof_service 표본."""

    def __init__(self, cache: SensorCache) -> None:
        self.cache = cache
        self.stats = TofServiceStats()

    def feed_line(self, line: str, now: float | None = None) -> bool:
        self.stats.lines += 1
        if not line.startswith(LINE_PREFIX):
            self.stats.rejected += 1
            self.stats.last_error = "bad prefix"
            return False
        try:
            state = json.loads(line[len(LINE_PREFIX):].strip())
            put_service_state(self.cache, state, now)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            self.stats.rejected += 1
            self.stats.last_error = str(exc)
            return False
        self.stats.accepted += 1
        return True


@dataclass
class TofServicePollStats:
    polls: int = 0
    ok: int = 0
    errors: int = 0
    last_error: str | None = None
    last_ok: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class TofServicePoller:
    """PC·테스트용: urllib 로 `/api/state` 를 주기 폴링해 캐시에 넣는다. 보드에서는 쓰지 않는다(소켓 없음)."""

    def __init__(self, cache: SensorCache, tof_cfg: dict[str, Any] | None = None, *,
                 on_log: Callable[[str], None] | None = None, now: Callable[[], float] = time.time) -> None:
        scfg = service_config(tof_cfg)      # ingest.yaml `tof` 절(그 안의 service 키를 읽는다)
        self.cache = cache
        self.url = str(scfg["url"])
        self.period = max(0.1, float(scfg["poll_s"]))
        self.timeout = max(0.05, float(scfg["timeout_s"]))
        self.stats = TofServicePollStats()
        self._log = on_log or (lambda _m: None)
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reported_down = False

    def poll_once(self) -> bool:
        """한 번 받아 캐시에 넣는다. 성공이면 True. 소켓은 여기서만 쓴다."""
        import urllib.error
        import urllib.request

        self.stats.polls += 1
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as resp:  # noqa: S310 — 로컬 서비스 주소
                body = resp.read()
            state = json.loads(body.decode("utf-8"))
            put_service_state(self.cache, state, self._now())
        except (urllib.error.URLError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.stats.errors += 1
            self.stats.last_error = str(exc)
            if not self._reported_down:
                self._log(f"[tof_service] {self.url} 응답 없음({exc}) — {self.period:g} s 마다 재시도, 그동안 MLP/규칙 판정")
                self._reported_down = True
            return False
        self.stats.ok += 1
        self.stats.last_ok = self._now()
        if self._reported_down or self.stats.ok == 1:
            self._log(f"[tof_service] {self.url} 연결 — 자세 서비스 판정 사용")
            self._reported_down = False
        return True

    def _run(self) -> None:
        next_at = time.monotonic()
        while not self._stop.is_set():
            self.poll_once()
            next_at += self.period
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = 0.0
            if self._stop.wait(delay):
                break

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tof-service-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout + self.period)
            self._thread = None


__all__ = [
    "SERVICE_LABELS", "LINE_PREFIX", "CACHE_KIND", "service_config", "uses_service", "should_poll",
    "parse_service_state", "put_service_state", "TofServiceLineSource", "TofServicePoller", "TofServiceStats",
]
