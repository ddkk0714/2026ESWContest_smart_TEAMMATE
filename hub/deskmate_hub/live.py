"""실센서 라이브 루프 — MQTT 센서 → SensorFrame → FSM → deskmate/state/phase.

    python -m deskmate_hub run --broker 192.168.10.1

frame_period_sec(=score_period_sec) 마다 한 tick. 프레임은 리플레이 호환 JSONL 로,
발행한 envelope 는 별도 JSONL 로 logs/ 에 남긴다(둘 다 gitignore).
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any, TextIO

from .inference import FSMEngine, GateMode, SensorFrame, State, load_config
from .control import ControlDispatcher, MockPlugAdapter, load_control_config
from .features import BaselineStore
from .ingest import SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary
from .presentation import state_envelope


def frame_to_dict(frame: SensorFrame) -> dict[str, Any]:
    """replay.iter_frames 가 그대로 읽는 형식."""
    d = asdict(frame)
    d["signals"] = {k: asdict(v) for k, v in frame.signals.items()}
    return d


class LiveHub:
    """전송 계층과 무관한 라이브 루프. 테스트에서는 source 없이 tick_once 만 호출한다."""

    def __init__(
        self,
        cache: SensorCache,
        *,
        fsm_cfg: dict[str, Any] | None = None,
        ingest_cfg: dict[str, Any] | None = None,
        publish=None,
        publish_request=None,
        publish_control=None,
        control_cfg: dict[str, Any] | None = None,
        frame_log: TextIO | None = None,
        state_log: TextIO | None = None,
        out: TextIO = sys.stdout,
    ) -> None:
        self.cache = cache
        self.fsm_cfg = fsm_cfg or load_config()
        self.ingest_cfg = ingest_cfg or load_ingest_config()
        self.engine = FSMEngine(self.fsm_cfg)
        self.tracker = SessionTracker()
        if str(self.ingest_cfg.get("normalization", "linear")).lower() == "baseline":
            bcfg = dict(self.ingest_cfg.get("baseline") or {})
            persist = bcfg.get("persist") or {}
            self.tracker.baseline = BaselineStore(
                bcfg, persist_path=persist.get("path") if persist.get("enabled") else None,
            )
        self.publish = publish or (lambda envelope: None)
        self.publish_request = publish_request or (lambda envelope: None)
        self.pending_request: dict[str, Any] | None = None
        # 제어: control.yaml enabled 면 ACTION_ENV 를 디스패처가 맡는다(auto_complete 대신). mock 어댑터는 즉시 성공 응답.
        self.control_cfg = control_cfg if control_cfg is not None else load_control_config()
        self.control: ControlDispatcher | None = None
        self.mock_plug: MockPlugAdapter | None = None
        if self.control_cfg.get("enabled"):
            log = lambda m: print(m, file=self.out, flush=True)  # noqa: E731
            if str(self.control_cfg.get("adapter", "mqtt")) == "mock" or publish_control is None:
                self.mock_plug = MockPlugAdapter(self.cache.put_control_result)
                self.control = ControlDispatcher(self.control_cfg, publish_cmd=self.mock_plug.handle_command, on_log=log)
            else:
                self.control = ControlDispatcher(self.control_cfg, publish_cmd=publish_control, on_log=log)
        self.frame_log, self.state_log, self.out = frame_log, state_log, out
        self.boot_id = f"{time.time_ns() & 0xFFFFFFFF:08x}"
        self.seq = 0
        self.period = float(self.ingest_cfg.get("frame_period_sec") or self.fsm_cfg["timers"]["score_period_sec"])

    def tick_once(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        view = self.cache.snapshot()
        feedback = self.cache.pop_feedback()
        if feedback and feedback.get("verdict") in ("accept", "reject"):
            # request_id 가 오면 현재 질문과 맞는지 본다. 없거나 'atlas-display'(HTTP 시절 기본값)면 그대로 받는다.
            rid = feedback.get("request_id")
            current = self.pending_request["data"]["request_id"] if self.pending_request else None
            if rid in (None, "", "atlas-display", current):
                self.tracker.pending_feedback = feedback["verdict"]
                self.pending_request = None
                if self.control is not None:
                    self.control.on_feedback(feedback["verdict"], now)

        if self.control is not None:
            if self.mock_plug is not None:
                self.mock_plug.tick()
            for res in self.cache.pop_control_results():
                self.control.on_result(res, now)

        frame = build_frame(view, now, self.ingest_cfg, self.tracker, fsm_state=self.engine.state)
        if self.control is not None and self.engine.state is State.ACTION_ENV:
            # ACTION_ENV 완료 여부는 제어 결과가 결정한다 (ingest 의 auto_complete 를 덮어쓴다)
            frame.action_done = self.control.action_done(now)
            frame.break_accepted = None
        prev_state = self.engine.state
        result = self.engine.tick(frame)
        self.tracker.observe_state(result.state, now)
        if self.control is not None:
            if result.state is State.ACTION_ENV and prev_state is not State.ACTION_ENV:
                self.control.on_enter_action(result.state.value, result.cause, result.gate, now)
            elif prev_state is State.ACTION_ENV and result.state is not State.ACTION_ENV:
                self.control.close_episode()

        summary = sensor_summary(view, now, self.ingest_cfg)
        if self.control is not None:
            summary["control"] = self.control.summary()
        if self.tracker.baseline is not None:
            snap = self.tracker.baseline.snapshot()
            summary["baseline"] = {"calibrating": snap["calibrating"], "ready": sorted(snap["session"]),
                                   "seeded": sorted({m for b in snap["buckets"].values() for m in b})}
        envelope = state_envelope(result, boot_id=self.boot_id, seq=self.seq, ts=now, sensor_summary=summary)
        self.seq += 1
        self.publish(envelope)
        self._maybe_request(result, prev_state, now)

        if self.frame_log:
            self.frame_log.write(json.dumps(frame_to_dict(frame), ensure_ascii=False) + "\n"); self.frame_log.flush()
        if self.state_log:
            self.state_log.write(json.dumps(envelope, ensure_ascii=False) + "\n"); self.state_log.flush()

        marker = "→" if result.state is not prev_state else " "
        avail = "".join(k[0] if s.available else "." for k, s in sorted(frame.signals.items()))
        print(
            f"[{time.strftime('%H:%M:%S', time.localtime(now))}] {marker} {result.state.value:<16} "
            f"ctx={result.context.value:<5} fat={result.scores.c_fatigue:.2f} foc={result.scores.c_focus:.2f} "
            f"present={int(frame.present)} pc={frame.pc_ratio:.2f} sig[{avail}] {','.join(result.actions)}",
            file=self.out, flush=True,
        )
        return envelope


    _REQUEST_STATES = {
        State.ACTION_BREAK: ("break_suggest", "take_a_break"),
        State.ACTION_ENV: ("env_suggest", "adjust_environment"),
        State.ACTION_POSTURE: ("posture_suggest", "fix_posture"),
    }

    def _maybe_request(self, result, prev_state, now: float) -> None:
        """제안 게이트(0.45~0.75)로 ACTION_* 에 새로 들어오면 display 에 확인 질문을 보낸다.

        자동(≥0.75)은 실행 후 알림이므로 질문하지 않고, 무동작(<0.45)은 로그만 남는다.
        display 는 request_id 를 기억했다가 feedback/user 에 실어 보낸다(display/atlas state_source.dart).
        """
        if result.state is prev_state or result.state not in self._REQUEST_STATES:
            return
        if result.gate is not GateMode.SUGGEST:
            self.pending_request = None
            return
        kind, prompt = self._REQUEST_STATES[result.state]
        self.pending_request = {
            "schema_version": "1.0", "ts": now, "node": "hub", "boot_id": self.boot_id, "seq": self.seq,
            "data": {
                "request_id": f"{self.boot_id}-{self.seq}", "kind": kind, "prompt_code": prompt,
                "options": ["accept", "reject"], "expires_in_s": int(self.fsm_cfg["timers"].get("focus_break_poll_sec", 180)),
                "evidence": list(result.actions), "cause": result.cause,
                "c_fatigue": round(float(result.scores.c_fatigue), 4),
            },
        }
        self.seq += 1
        self.publish_request(self.pending_request)


def run_live(broker: str, port: int, *, fsm_config: str | None, ingest_config: str | None, log_dir: str) -> int:
    from .ingest.mqtt_source import MqttSource

    os.makedirs(log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    cache = SensorCache()
    source = MqttSource(cache, broker, port, on_log=lambda m: print(m, file=sys.stderr))
    with open(os.path.join(log_dir, f"frames-{stamp}.jsonl"), "a", encoding="utf-8") as flog, \
         open(os.path.join(log_dir, f"state-{stamp}.jsonl"), "a", encoding="utf-8") as slog:
        hub = LiveHub(
            cache,
            fsm_cfg=load_config(fsm_config) if fsm_config else None,
            ingest_cfg=load_ingest_config(ingest_config) if ingest_config else None,
            publish=source.publish_state,
            publish_request=source.publish_request,
            publish_control=source.publish_control,
            frame_log=flog, state_log=slog,
        )
        print(f"deskmate hub live — broker {broker}:{port}, period {hub.period:.0f}s, logs {log_dir}/", file=sys.stderr)
        source.start()
        try:
            next_tick = time.time()
            while True:
                hub.tick_once()
                next_tick += hub.period
                time.sleep(max(0.0, next_tick - time.time()))
        except KeyboardInterrupt:
            print("\n[hub] stopping", file=sys.stderr)
        finally:
            source.stop()
    return 0
