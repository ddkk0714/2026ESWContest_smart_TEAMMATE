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

from .inference import (FSMEngine, GateMode, SensorFrame, SessionRecorder, State,
                        load_config, report_envelope)
from .control import ControlDispatcher, MockPlugAdapter, load_control_config
from .features import BaselineStore
from .ingest import SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary
from .presentation import state_envelope
from .esm import make_label, write_label


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
        # 리포트는 세션이 끝나야 나오는 게 아니라 진행 중에도 스냅샷을 낼 수 있어야
        # 한다. 화면의 리포트 탭이 세션 내내 비어 있으면 아무도 안 본다.
        self.recorder = SessionRecorder()
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
        self.esm_log_path = self.control_cfg.get("esm_log_path") or os.path.join("logs", f"esm-{self.boot_id}.jsonl")
        # 라벨 번호는 state/phase seq 와 따로 센다(seq 에 구멍이 나면 수신 측이 유실로 본다).
        self._esm_seq = 0
        self._last_result = None

    def tick_once(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        view = self.cache.snapshot()
        feedback = self.cache.pop_feedback()
        if feedback:
            verdict = feedback.get("verdict")
            # request_id 가 오면 현재 질문과 맞는지 본다. 없거나 'atlas-display'(HTTP 시절 기본값)면 그대로 받는다.
            rid = feedback.get("request_id")
            current = self.pending_request["data"]["request_id"] if self.pending_request else None
            if verdict not in ("accept", "reject", "correct", "timeout"):
                print(f"[esm] 알 수 없는 verdict: {verdict!r}", file=self.out)
            elif verdict == "correct":
                if feedback.get("corrected_state") not in ("FOCUS_PC", "FATIGUE", "REST", "IDLE"):
                    print("[esm] 알 수 없는 corrected_state", file=self.out)
                else:
                    self._record_feedback(feedback, now, None)
            elif verdict == "timeout":
                if current is not None and rid == current:
                    request = self.pending_request
                    self.pending_request = None
                    self._record_feedback(feedback, now, request)
                    if self.control is not None:
                        self.control.on_feedback("timeout", now)
            elif rid in (None, "", "atlas-display", current):
                request = self.pending_request
                self.tracker.pending_feedback = verdict
                self.pending_request = None
                if self.control is not None:
                    self.control.on_feedback(verdict, now)
                self._record_feedback(feedback, now, request)

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
        self._last_result = result
        self.tracker.observe_state(result.state, now)
        self.recorder.observe(frame, result)
        if self.control is not None:
            if result.state is State.ACTION_ENV and prev_state is not State.ACTION_ENV:
                self.control.on_enter_action(result.state.value, result.cause, result.gate, now)
            elif prev_state is State.ACTION_ENV and result.state is not State.ACTION_ENV:
                self.control.close_episode()

        # tracker 를 넘겨야 세션 시작 대비 CO₂ 상승(co2_rising)도 화면 이유에 실린다.
        summary = sensor_summary(view, now, self.ingest_cfg, self.tracker)
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


    def report_envelope(self, now: float | None = None) -> dict[str, Any]:
        """지금까지의 세션 요약. 주기 발행과 세션 종료에 같은 모양을 쓴다."""
        return report_envelope(
            self.recorder.finalize(), now=time.time() if now is None else now,
            control_episodes=(self.control.history + ([self.control.episode] if self.control.episode else []))
            if self.control is not None else (),
        )

    def _record_feedback(self, feedback: dict[str, Any], now: float,
                         request: dict[str, Any] | None) -> None:
        verdict = feedback["verdict"]
        result = self._last_result
        scores = result.scores if result is not None else None
        episode = None
        if self.control is not None:
            episode = self.control.episode or (self.control.history[-1] if self.control.history else None)
        request_data = request["data"] if request is not None else None
        request_ts = request["ts"] if request is not None else None
        if request_ts is None and verdict == "reject" and episode is not None:
            request_ts = episode.executed_ts
        response_ms = feedback.get("response_ms")
        if not isinstance(response_ms, int) or isinstance(response_ms, bool):
            response_ms = None
        if verdict == "correct":
            kind = "correction"
        elif request_data is not None:
            kind = request_data["kind"]
        elif verdict == "reject" and episode is not None and episode.gate is GateMode.AUTO and episode.executed:
            kind = "auto_undo"
        else:
            kind = "feedback"
        record = make_label(
            label_id=f"{self.boot_id}-esm{self._esm_seq}", ts=now, verdict=verdict,
            request_id=feedback.get("request_id") or None,
            kind=kind,
            predicted_state=self.engine.state.value,
            cause=result.cause if verdict == "correct" and result is not None else
                  request_data.get("cause") if request_data is not None else
                  episode.cause if episode is not None else result.cause if result is not None else None,
            gate=result.gate.value if verdict == "correct" and result is not None else
                 "suggest" if request_data is not None else
                 episode.gate.value if episode is not None else result.gate.value if result is not None else None,
            c_fatigue=scores.c_fatigue if scores is not None else None,
            c_focus=scores.c_focus if scores is not None else None,
            corrected_state=feedback.get("corrected_state"), response_ms=response_ms,
            request_ts=request_ts,
            score_period_sec=float(self.fsm_cfg["timers"]["score_period_sec"]),
        )
        self._esm_seq += 1
        self.recorder.record_esm(record)
        write_label(record, self.esm_log_path, on_log=lambda m: print(m, file=self.out))

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
        if not hub.control_cfg.get("esm_log_path"):
            # 프레임·상태 로그와 같은 곳에 둔다(--log-dir). 기본 logs/ 는 gitignore.
            hub.esm_log_path = os.path.join(log_dir, f"esm-{hub.boot_id}.jsonl")
        print(f"deskmate hub live — broker {broker}:{port}, period {hub.period:.0f}s, logs {log_dir}/", file=sys.stderr)
        source.start()
        # 보드 bridge(service_bridge)와 같이 세션 리포트 스냅샷을 주기 발행한다(retain). 없으면 PC 경로에서
        # 화면의 리포트 탭이 비고 정량 지표(metrics)도 못 본다 — 10-01 실기에서 발견.
        report_period = float(os.environ.get("DESKMATE_REPORT_PERIOD_SEC", "10"))
        try:
            next_tick = time.time()
            next_report = next_tick + report_period
            while True:
                hub.tick_once()
                if report_period > 0 and time.time() >= next_report:
                    source.publish_report(hub.report_envelope())
                    next_report = time.time() + report_period
                next_tick += hub.period
                time.sleep(max(0.0, next_tick - time.time()))
        except KeyboardInterrupt:
            print("\n[hub] stopping", file=sys.stderr)
        finally:
            source.stop()
    return 0
