"""LiveHub 를 실기와 같은 입력 경로로 먹이는 어댑터.

- ToF: 센서별 8×8 → UART 0x05 payload(encode_tof_sensor_frames) → ingest_frame(TofGridAssembler, posture 훅 = 모델)
       → cache.tof_grid / cache.tof_mlp.  0x03 규칙 특징(RuleFeatures) → encode_payload(0x03) → ingest_frame → cache.tof.
       Pi 4 의 `UART\\t{json}` 라인과 같은 디코더를 지나므로 필드 단위·범위도 실기와 같게 깎인다.
- 환경·키스트로크: tools/mqtt_scenario_sim.py 와 같은 토픽·payload 를 route_mqtt_message 로.
- 피드백: cache.put_feedback({request_id, verdict, response_ms}) — display 가 보내는 것과 같은 모양.
- 제어: LiveHub 의 ControlDispatcher + MockPlugAdapter. publish_cmd 를 감싸 명령을 밖으로도 넘긴다(DeviceWorld 가 받는다).
시간은 전부 가상 시계(`t`, epoch 초)로 넘긴다 — ingest 의 신선도 판정도 그 시계로 돈다.
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Callable

import numpy as np

from deskmate_hub.control import load_control_config
from deskmate_hub.inference import State, load_config
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.ingest.uart_frame import (TYPE_TOF_FEATURES, TYPE_TOF_GRID, Frame, TofGridAssembler, decode_payload,
                                            encode_payload, encode_tof_sensor_frames)
from deskmate_hub.ingest.uart_source import UartStats, ingest_frame
from deskmate_hub.live import LiveHub

ACTION_STATES = {"ACTION_ENV": "env", "ACTION_POSTURE": "posture", "ACTION_BREAK": "break"}


class _PostureHook:
    """ingest_frame 의 posture 훅 — 모델에 재실 힌트(0x03 규칙)를 함께 넘기고 마지막 판정을 보관한다."""

    def __init__(self, model):
        self.model = model
        self.present: bool | None = None
        self.last: dict[str, Any] | None = None

    def update(self, sample: dict[str, Any], now: float) -> dict[str, Any] | None:
        if self.model is None:
            return None
        try:
            v = self.model.update(sample, now, present=self.present)
        except TypeError:
            v = self.model.update(sample, now)
        self.last = v
        return v


class HubBridge:
    def __init__(self, *, posture_model=None, fsm_config: str | None = None, ingest_cfg: dict | None = None,
                 control_cfg: dict | None = None, esm_log_path: str | None = None, t0: float = 0.0, node: str = "sim",
                 personalize_cfg: dict | None = None, personalize_policy_path: str | None = None,
                 personalize_log_path: str | None = None):
        self.cache = SensorCache()
        self.out = io.StringIO()
        fsm_cfg = load_config(fsm_config) if fsm_config else load_config()
        ingest = ingest_cfg or load_ingest_config()
        control = dict(control_cfg or load_control_config())
        if esm_log_path:
            control["esm_log_path"] = esm_log_path
            os.makedirs(os.path.dirname(esm_log_path) or ".", exist_ok=True)
        kw: dict[str, Any] = {}
        if personalize_cfg is not None:       # fork/feat/personalize-policy 의 LiveHub 만 받는다(없으면 기록 없이 돈다)
            kw = {"personalize_cfg": personalize_cfg, "personalize_policy_path": personalize_policy_path,
                  "personalize_log_path": personalize_log_path}
        try:
            self.hub = LiveHub(self.cache, fsm_cfg=fsm_cfg, ingest_cfg=ingest, control_cfg=control, out=self.out, **kw)
        except TypeError:
            if not kw:
                raise
            self.hub = LiveHub(self.cache, fsm_cfg=fsm_cfg, ingest_cfg=ingest, control_cfg=control, out=self.out)
        self.personalize = getattr(self.hub, "personalize_recorder", None)
        self.fsm_cfg, self.ingest_cfg, self.control_cfg = fsm_cfg, ingest, control
        self.node = node
        self.period = float(self.hub.period)
        self.states: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self._new_requests: list[dict[str, Any]] = []
        self._new_commands: list[dict[str, Any]] = []
        self.hub.publish = lambda env: None
        self.hub.publish_request = self._on_request
        if self.hub.control is not None:
            send = self.hub.control.publish_cmd
            self.hub.control.publish_cmd = lambda p: (self._on_command(p), send(p))[1]
        self.hook = _PostureHook(posture_model)
        self.stats = UartStats()
        self.grid = TofGridAssembler()
        self.frame_id = 0
        self.seq = {"tof": 0, "grid": 0, "env": 0, "ks": 0}
        self.boot_id = self.hub.boot_id
        self.t0 = t0
        self.prev_state = State.IDLE
        self.last_tick: dict[str, Any] | None = None
        self.command_sink: Callable[[dict[str, Any]], None] | None = None

    # ---------------------------------------------------------------- 수집 콜백
    def _on_request(self, env: dict[str, Any]) -> None:
        rec = {"t": env["ts"], **env["data"]}
        self.requests.append(rec)
        self._new_requests.append(rec)

    def _on_command(self, payload: dict[str, Any]) -> None:
        rec = {"t": self._now, **payload}
        self.commands.append(rec)
        self._new_commands.append(rec)
        if self.command_sink is not None:
            self.command_sink(rec)

    _now = 0.0

    # ---------------------------------------------------------------- 입력
    def feed_tof(self, frames: dict[int, tuple[np.ndarray, np.ndarray]], features: dict[str, Any], t: float) -> dict[str, Any] | None:
        """센서별 (dist, status) + 0x03 특징 → 캐시. 모델 판정(dict) 을 돌려준다(이번 표본에서 났으면)."""
        self._now = t
        self.hook.present = bool(features.get("present"))
        self.frame_id = (self.frame_id + 1) & 0xFF
        packed = {idx: (np.asarray(d).reshape(-1).astype(int).tolist(), np.asarray(s).reshape(-1).astype(int).tolist())
                  for idx, (d, s) in frames.items()}
        ts_ms = int(round(t * 1000)) & 0xFFFFFFFF
        verdict = None
        for payload in encode_tof_sensor_frames(packed, frame_id=self.frame_id):
            self.seq["grid"] += 1
            kind = ingest_frame(self.cache, Frame(type=TYPE_TOF_GRID, seq=self.seq["grid"], ts_ms=ts_ms, payload=payload),
                                self.stats, received=t, node=self.node, grid=self.grid, posture=self.hook)
            if kind == "tof_grid":
                verdict = self.hook.last
        clean = {k: v for k, v in features.items() if not k.startswith("_")}
        self.seq["tof"] += 1
        ingest_frame(self.cache, Frame(type=TYPE_TOF_FEATURES, seq=self.seq["tof"], ts_ms=ts_ms,
                                       payload=encode_payload(TYPE_TOF_FEATURES, clean)), self.stats, received=t, node=self.node)
        return verdict

    def feed_env(self, env: dict[str, Any], t: float) -> None:
        self.seq["env"] += 1
        data = {"co2_ppm": int(round(env["co2_ppm"])), "temp_c": round(float(env["temp_c"]), 1),
                "humidity_pct": round(float(env["humidity_pct"]), 1), "lux": int(round(env["lux"])),
                "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True}
        body = {"schema_version": "1.0", "ts": round(t, 3), "node": self.node, "boot_id": self.boot_id,
                "seq": self.seq["env"], "data": data}
        route_mqtt_message(self.cache, f"deskmate/sensor/env/{self.node}", json.dumps(body).encode("utf-8"), t)

    def feed_keystroke(self, typing: bool, keys: tuple[float, float, float], t: float, rnd) -> None:
        idle, cv, corr = keys
        j = lambda v, r=0.15: round(v * (1 + rnd.uniform(-r, r)), 3)  # noqa: E731
        if typing:
            flight = j(150.0)
            data = {"window_s": 60, "dwell_mean_ms": j(92.0), "dwell_std_ms": j(21.0), "flight_mean_ms": flight,
                    "flight_std_ms": round(flight * cv, 2), "idle_ratio": min(1.0, j(idle)),
                    "correction_rate": round(min(1.0, j(corr)), 4), "typing_active": True, "mouse_active": True,
                    "input_active": True, "flight_cv": j(cv), "mouse_event_rate": j(40.0)}
        else:
            data = {"window_s": 60, "dwell_mean_ms": 0, "dwell_std_ms": 0, "flight_mean_ms": 0, "flight_std_ms": 0,
                    "idle_ratio": 1.0, "correction_rate": 0, "typing_active": False, "mouse_active": False,
                    "input_active": False, "flight_cv": 0, "mouse_event_rate": 0}
        payload = json.dumps({"ts": round(t, 3), "node": "pc-collector", **data}).encode("utf-8")
        route_mqtt_message(self.cache, "deskmate/sensor/keystroke", payload, t)

    def feed_weather(self, weather: dict[str, Any], t: float) -> None:
        """deskmate/sensor/weather(retain, Pi 5 앱) 모사. personalize 만 읽는다(FSM 입력 아님)."""
        self.seq["env"] += 1
        data = {k: weather.get(k) for k in ("temp_c", "humidity", "wind_ms", "condition", "season", "fetched_at", "source")}
        body = {"schema_version": "1.0", "ts": round(t, 3), "node": "pi5-display", "boot_id": self.boot_id,
                "seq": self.seq["env"], "data": data}
        route_mqtt_message(self.cache, "deskmate/sensor/weather", json.dumps(body).encode("utf-8"), t)

    def feedback(self, verdict: str, t: float, *, request_id: str | None = None, response_ms: int | None = None) -> None:
        fb: dict[str, Any] = {"verdict": verdict}
        if request_id:
            fb["request_id"] = request_id
        if response_ms is not None:
            fb["response_ms"] = int(response_ms)
        self.cache.put_feedback(fb, received=t)

    def control_result(self, payload: dict[str, Any], t: float) -> None:
        self.cache.put_control_result(payload, received=t)

    # ---------------------------------------------------------------- tick
    def tick(self, t: float) -> dict[str, Any]:
        self._now = t
        self._new_requests, self._new_commands = [], []
        prev = self.hub.engine.state
        env = self.hub.tick_once(t)
        d = env["data"]
        res = self.hub._last_result
        state = d["fsm_state"]
        summary = d.get("sensor_summary") or {}
        info = {
            "t": t, "state": state, "prev_state": prev.value, "entered": state != prev.value,
            "gate": d.get("gate"), "cause": d.get("cause"), "context": res.context.value if res else None,
            "c_fatigue": round(float(d.get("c_fatigue", 0.0)), 4), "c_focus": round(float(d.get("c_focus", 0.0)), 4),
            "actions": list(d.get("reasons") or []), "guards": list(res.guards) if res else [],
            "present": bool(summary.get("present", False)),
            "tof": summary.get("tof"), "env_flags": summary.get("env_flags", []),
            "control": summary.get("control"), "requests": list(self._new_requests), "commands": list(self._new_commands),
            "pending_request": (self.hub.pending_request or {}).get("data", {}).get("request_id"),
            "personalize": summary.get("personalize"),
            "asked_by_policy": "personalize:ask" in (d.get("reasons") or []),
        }
        # 개입 이벤트: ACTION_* 에 새로 들어온 tick. auto 는 실행 후 알림(질문 없음), suggest 는 카드(request), none 은 로그만.
        events: list[dict[str, Any]] = []
        if info["entered"] and state in ACTION_STATES:
            pv = info["personalize"] or {}
            req = info["requests"][0] if info["requests"] else {}
            events.append({"kind": f"{ACTION_STATES[state]}_{info['gate']}", "state": state, "gate": info["gate"],
                           "cause": info["cause"], "request_id": req.get("request_id"),
                           "expires_in_s": req.get("expires_in_s"), "c_fatigue": info["c_fatigue"],
                           "arm": req.get("arm") or pv.get("arm"), "arm_label": req.get("arm_label") or pv.get("arm_label"),
                           "explanation": req.get("explanation") or pv.get("explanation"),
                           "fsm_gate": pv.get("fsm_gate"), "delivered_gate": pv.get("delivered_gate"),
                           "asked_by_policy": info["asked_by_policy"], "episode_id": pv.get("episode_id")})
        if info["entered"] and state == "REST":
            events.append({"kind": "rest_forced" if "force_rest" in info["actions"] else "rest_accepted", "state": state,
                           "gate": info["gate"], "cause": info["cause"], "c_fatigue": info["c_fatigue"]})
        for a in info["actions"]:
            if a in ("wake_alert", "posture_nudge", "cool_down", "heart_alert"):
                events.append({"kind": f"guard_{a}", "state": state, "gate": info["gate"], "cause": info["cause"]})
        info["events"] = events
        self.prev_state = prev
        self.last_tick = info
        self.states.append(info)
        return info

    def report(self, t: float) -> dict[str, Any]:
        rep = self.hub.report_envelope(t)
        return rep.get("data", rep)

    def hub_log(self) -> str:
        return self.out.getvalue()


def decode_tof_sample(sample: dict[str, Any]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """TofGridAssembler 표본 → {센서: (dist[8,8], status[8,8])} (플롯·검사용)."""
    w, h = int(sample["grid_width"]), int(sample["grid_height"])
    return {int(i): (np.asarray(f["distance_mm"]).reshape(h, w), np.asarray(f["status"]).reshape(h, w))
            for i, f in (sample.get("sensors") or {}).items()}


__all__ = ["HubBridge", "decode_tof_sample", "decode_payload"]
