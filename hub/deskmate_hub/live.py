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
from .profile import apply_profile, apply_section
from .esm import make_label, write_label
from .fatigue.live import build_fatigue_runtime, load_fatigue_config
from .personalization import PersonalizationRuntime, load_personalization_config
from .personalization.privacy import PersonalizationPrivacy, load_privacy_config
from .personalization.ondevice import OnDeviceLearner, load_ondevice_config

try:  # 개인화 로그 기록(personalize/record.py). 설정·패키지를 못 읽으면 기록만 빠지고 허브는 돈다.
    from .personalize import EpisodeRecorder, InterventionPolicy, load_personalize_config
except ImportError:  # pragma: no cover
    EpisodeRecorder = None  # type: ignore[assignment,misc]
    InterventionPolicy = None  # type: ignore[assignment,misc]
    load_personalize_config = None  # type: ignore[assignment]


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
        personalization_cfg: dict[str, Any] | None = None,
        privacy_cfg: dict[str, Any] | None = None,
        ondevice_cfg: dict[str, Any] | None = None,
        personalization_backend_factory=None,
        personalize_cfg: dict[str, Any] | None = None,
        personalize_log_path: str | None = None,
        personalize_policy_path: str | None = None,
        profile: str | None = None,
        fatigue_cfg: dict[str, Any] | None = None,
        fatigue_model=None,
        frame_log: TextIO | None = None,
        state_log: TextIO | None = None,
        out: TextIO = sys.stdout,
    ) -> None:
        self.cache = cache
        self.fsm_cfg = fsm_cfg or load_config()
        self.ingest_cfg = ingest_cfg or load_ingest_config()
        # 시연 프로파일(DESKMATE_PROFILE=demo-a|demo-b, profile.py): fsm·ingest 위에 오버레이. profile="" 이면 환경변수를 무시한다.
        self.fsm_cfg, self.ingest_cfg, self.profile = apply_profile(
            self.fsm_cfg, self.ingest_cfg, profile, log=lambda m: print(m, file=out, flush=True))
        self.engine = FSMEngine(self.fsm_cfg)
        self.tracker = SessionTracker(
            elapsed_reset_on_rest=bool((self.ingest_cfg.get("elapsed") or {}).get("reset_on_rest", False)))
        self.privacy_cfg = privacy_cfg if privacy_cfg is not None else load_privacy_config()
        self._personalization_factory = personalization_backend_factory
        self.privacy = PersonalizationPrivacy(
            self.privacy_cfg,
            baseline_file=((self.ingest_cfg.get("baseline") or {}).get("persist") or {}).get("path"),
            on_reset=self._reset_personalization, on_grant=self._grant_personalization,
        )
        # 리포트는 세션이 끝나야 나오는 게 아니라 진행 중에도 스냅샷을 낼 수 있어야
        # 한다. 화면의 리포트 탭이 세션 내내 비어 있으면 아무도 안 본다.
        self.recorder = SessionRecorder()
        if str(self.ingest_cfg.get("normalization", "linear")).lower() == "baseline":
            bcfg = dict(self.ingest_cfg.get("baseline") or {})
            persist = bcfg.get("persist") or {}
            self.tracker.baseline = BaselineStore(
                bcfg, persist_path=persist.get("path") if persist.get("enabled") and (not self.privacy.enabled or self.privacy.consented) else None,
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
        self.privacy.boot_id = self.boot_id
        self.seq = 0
        self.period = float(self.ingest_cfg.get("frame_period_sec") or self.fsm_cfg["timers"]["score_period_sec"])
        self.esm_log_path = self.control_cfg.get("esm_log_path") or os.path.join("logs", f"esm-{self.boot_id}.jsonl")
        # 라벨 번호는 state/phase seq 와 따로 센다(seq 에 구멍이 나면 수신 측이 유실로 본다).
        self.personalization_cfg = personalization_cfg if personalization_cfg is not None else load_personalization_config()
        self.personalization = PersonalizationRuntime(
            self._effective_model_config(), period=self.period,
            normalization=self.ingest_cfg.get("normalization", "linear"),
            backend_factory=personalization_backend_factory,
        )
        self._esm_seq = 0
        self._last_result = None
        # 개인화 로그(personalization-log.md): personalize.yaml `record: true` 면 결정·피드백·10 분 뒤 결과를
        # logs/personalize-<boot_id>.jsonl 에 한 줄씩 쓴다. 정책(enabled)은 별개 — 꺼져 있어도 기록은 쌓는다.
        self.personalize_cfg = personalize_cfg if personalize_cfg is not None else (
            load_personalize_config() if load_personalize_config is not None else {})
        # 프로파일의 personalize: 절(정책 gate.conf_auto 등) — FSM 만 낮추고 정책이 0.75 로 남아 자동이 제안으로 깎이던 결함(10-24 PC 리허설)
        self.personalize_cfg = apply_section(self.personalize_cfg, "personalize", self.profile or "")
        # `enabled: true` 면 3층 정책(InterventionPolicy, 순수 Python — Pi 4 에서도 돈다)도 켜서 결정 tick 에
        # arm·전달 게이트를 고르고 profiles/<profile_id>/policy.json 에 학습 상태를 저장한다.
        self.personalize_recorder: EpisodeRecorder | None = None
        self.personalize_policy = None
        if EpisodeRecorder is not None and (self.personalize_cfg.get("record") or self.personalize_cfg.get("enabled")):
            log_dir = self.personalize_cfg.get("log_dir") or "logs"
            path = personalize_log_path or os.path.join(log_dir, f"personalize-{self.boot_id}.jsonl")
            policy_path = None
            if self.personalize_cfg.get("enabled"):
                if InterventionPolicy is None:
                    print("[personalize] enabled 지만 정책 모듈을 못 불러 기록만 한다", file=self.out)
                else:
                    policy_path = personalize_policy_path or os.path.join(
                        log_dir, "profiles", str(self.personalize_cfg.get("profile_id", "default")), "policy.json")
                    self.personalize_policy = InterventionPolicy.load(policy_path, self.personalize_cfg)
            self.personalize_recorder = EpisodeRecorder(
                self.personalize_cfg, path, boot_id=self.boot_id, ingest_cfg=self.ingest_cfg,
                on_log=lambda m: print(m, file=self.out), policy=self.personalize_policy, policy_path=policy_path)
        self.ondevice_cfg = ondevice_cfg if ondevice_cfg is not None else load_ondevice_config()
        self.ondevice = OnDeviceLearner(self.ondevice_cfg, self.privacy)
        # 피로 점수(공용 KSS 모델, docs/fatigue-dataset.md §6) — 관찰 전용, FSM 입력 아님. 모델 JSON 이 없으면 None.
        self.fatigue_cfg = fatigue_cfg if fatigue_cfg is not None else load_fatigue_config()
        self.fatigue = build_fatigue_runtime(
            self.fatigue_cfg, tick_period=self.period, model=fatigue_model,
            personal_allowed=self._fatigue_personal_allowed, on_log=lambda m: print(m, file=self.out))

    def tick_once(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        view = self.cache.snapshot()
        for feedback, received in self.cache.pop_feedback_events():
            if feedback.get("kind") == "personalization":
                self.privacy.handle(feedback)
                continue
            if feedback.get("kind") == "manual_adjust":
                # 앱에서 사용자가 직접 조명을 바꿨다는 보고 — 개인화 암묵 신호 전용(ESM·제어 승인 아님, personalize/signals.py)
                if self.personalize_recorder is not None:
                    self.personalize_recorder.on_manual_adjust(feedback, ts=now if received is None else min(now, received))
                continue
            feedback_now = now if received is None else min(now, received)
            self._expire_pending_request(feedback_now)
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
                        self.control.on_feedback(verdict, now, received_at=feedback_now)
                    self._record_feedback(feedback, now, request)

        # Pi 5 피로 기록 앱 응답: 세션 문맥(시작 KSS·수면·프로필)과 프로필 보정. 모델이 없으면 버린다.
        for label, _received in self.cache.pop_fatigue_label_events():
            if self.fatigue is not None:
                self.fatigue.on_label(label)

        self._expire_pending_request(now)
        if self.control is not None:
            if self.mock_plug is not None:
                self.mock_plug.tick()
            for res, received in self.cache.pop_control_result_events():
                self.control.on_result(res, now if received is None else min(now, received))
                if self.personalize_recorder is not None:      # 무드등 결과의 previous·actual → 밝힘 여부
                    self.personalize_recorder.on_control_result(res, now if received is None else min(now, received))
            self.control.tick(now)

        frame = build_frame(view, now, self.ingest_cfg, self.tracker, fsm_state=self.engine.state)
        request_kind = self._REQUEST_STATES.get(self.engine.state)
        awaiting_request = (self.pending_request is not None and request_kind is not None
                            and self.pending_request["data"]["kind"] == request_kind[0])
        if awaiting_request:
            # The MVP auto-completion flag is not a substitute for a person's
            # response. Keep BREAK/POSTURE suggestions alive too, not just ENV.
            frame.action_done = False
        if self.control is not None and self._control_owns(self.engine.state):
            # ACTION_ENV(그리고 제어 에피소드가 열린 ACTION_POSTURE) 완료 여부는 제어 결과가 결정한다
            # (ingest 의 auto_complete 를 덮어쓴다). ACTION_BREAK 는 사람이 쉬는 일이라 그대로 둔다.
            frame.action_done = self.control.action_done(now)
            frame.break_accepted = None
        prev_state = self.engine.state
        result = self.engine.tick(frame)
        if awaiting_request and result.state is prev_state:
            result.gate = GateMode.SUGGEST
            result.cause = self.pending_request["data"].get("cause")
        self._last_result = result
        self.tracker.observe_state(result.state, now)
        self.recorder.observe(frame, result)
        # tracker 를 넘겨야 세션 시작 대비 CO₂ 상승(co2_rising)도 화면 이유에 실린다.
        summary = sensor_summary(view, now, self.ingest_cfg, self.tracker)
        # 개인화: 결정 tick(route:<cause>)에 에피소드를 열고, 정책이 켜져 있으면 arm·전달 게이트를 고른다.
        # 정책은 FSM 게이트를 낮추기만 한다(auto → suggest). 제어·질문·발행보다 먼저 돌아야 그 결과가 반영된다.
        specs = None
        if self.personalize_recorder is not None:
            request_kind = self._REQUEST_STATES.get(result.state)
            self.personalize_recorder.on_tick(
                frame, result, prev_state, view, now, retry=bool(getattr(self.engine, "_retry", False)),
                env_flags=summary.get("env_flags") or (), fsm_action=request_kind[1] if request_kind else None)
            decision = self.personalize_recorder.last_decision
            if decision is not None:
                if decision.delivered_gate == "suggest" and result.gate is GateMode.AUTO:
                    result.gate = GateMode.SUGGEST
                    result.actions.append("personalize:ask")
                specs = self.personalize_recorder.commands_for(decision)
            if self.personalize_recorder.last_view is not None:
                summary["personalize"] = self.personalize_recorder.last_view
        if self.control is not None:
            # 제어 에피소드: ACTION_ENV 는 control.yaml 기본 명령으로, 다른 ACTION_* 는 정책이 기기 arm(조명 등)을
            # 골랐을 때만(specs 비어 있지 않음). ACTION_POSTURE 는 2026-10-24 부터 정책 spec 이 없어도 control.yaml
            # `actions.posture`(졸음 → 무드등 밝게)가 있으면 그것으로 — 정책 spec 이 있으면 그쪽이 우선이고, 설정에
            # `actions.posture` 가 없으면 예전과 같이 명령 없음. 자동이면 바로 실행하고 되돌리기 창이 열린다.
            entered = result.state in self._REQUEST_STATES and result.state is not prev_state
            if (entered and result.state is State.ACTION_POSTURE and not specs
                    and self.control.has_default_actions(result.state.value, result.cause)):
                # 정책이 기기 arm 을 안 줬다(정책 없음, 또는 화면·음성 arm) → control.yaml 기본 명령으로
                self.control.on_enter_action(result.state.value, result.cause, result.gate, now, specs=None)
            elif entered and (result.state is State.ACTION_ENV or specs):
                self.control.on_enter_action(result.state.value, result.cause, result.gate, now, specs=specs)
            elif (prev_state in self._REQUEST_STATES and result.state is not prev_state
                  and self.control.episode is not None):
                self.control.close_episode()
        if self.control is not None:
            summary["control"] = self.control.summary()
            if self.personalize_recorder is not None:          # 이번 tick 에 나간 무드등 명령 → '밝힘 뒤 직접 어둡게' 추적
                self.personalize_recorder.observe_control(self.control, now)
        if self.tracker.baseline is not None:
            snap = self.tracker.baseline.snapshot()
            summary["baseline"] = {"calibrating": snap["calibrating"], "ready": sorted(snap["session"]),
                                   "seeded": sorted({m for b in snap["buckets"].values() for m in b})}
        if self.privacy.enabled:
            summary["privacy"] = self.privacy.snapshot()
        if self.personalization_cfg.get("enabled"):
            if self.ondevice_cfg.get("enabled"):
                self.ondevice.bind(self.personalization)
            summary["personalization"] = self.personalization.observe(
                frame_to_dict(frame), reset=(result.state in (State.START, State.END) and result.state is not prev_state),
            )
        if self.ondevice_cfg.get("enabled"):
            self.ondevice.observe(self.personalization, result.state.value, now)
            self.ondevice.advance(result.state.value)
            summary["ondevice_learning"] = self.ondevice.snapshot()
        if self.fatigue is not None:
            # fatigue_logger 가 남기는 학습 tick 과 같은 입력(state/phase 의 ts·c_fatigue·sensor_summary)이어야
            # 학습과 기기의 특징이 같다 — c_fatigue 는 state_envelope 처럼 0~1 로 자르고 4자리로 둔다.
            self.fatigue.set_hub_session(self.tracker.session_started)
            view = self.fatigue.observe({
                "ts": now, "fsm_state": result.state.value, "sensor_summary": summary,
                "c_fatigue": round(max(0.0, min(1.0, float(result.scores.c_fatigue))), 4),
            })
            if view is not None:
                summary["fatigue"] = view
        envelope = state_envelope(result, boot_id=self.boot_id, seq=self.seq, ts=now, sensor_summary=summary)
        self.seq += 1
        self.publish(envelope)
        self._maybe_request(result, prev_state, now)
        if (self.personalize_recorder is not None and self.personalize_recorder.last_episode_id
                and self.pending_request is not None):
            self.personalize_recorder.link_request(self.pending_request["data"]["request_id"])

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


    def _control_owns(self, state: State) -> bool:
        """이 상태의 완료(action_done)를 제어 결과가 정하는가. ACTION_ENV 는 언제나, ACTION_POSTURE 는 이번 진입에
        제어 에피소드가 열렸을 때만(control.yaml `actions.posture` 또는 정책 기기 arm). ACTION_BREAK 는 아니다."""
        if state is State.ACTION_ENV:
            return True
        if state is State.ACTION_POSTURE and self.control is not None:
            ep = self.control.episode
            return ep is not None and ep.state == state.value
        return False

    def report_envelope(self, now: float | None = None) -> dict[str, Any]:
        """지금까지의 세션 요약. 주기 발행과 세션 종료에 같은 모양을 쓴다."""
        return report_envelope(
            self.recorder.finalize(), now=time.time() if now is None else now,
            control_episodes=(self.control.history + ([self.control.episode] if self.control.episode else []))
            if self.control is not None else (),
        )

    def _effective_model_config(self):
        cfg = dict(self.personalization_cfg)
        if self.privacy.enabled and not self.privacy.consented:
            cfg["enabled"] = False
        return cfg

    def _fatigue_personal_allowed(self) -> bool:
        if not (self.fatigue_cfg.get("personal") or {}).get("require_consent", True):
            return True
        return self.privacy.enabled and self.privacy.valid and self.privacy.consented

    def _reset_personalization(self):
        if hasattr(self, "ondevice"):
            self.ondevice.reset()
        if getattr(self, "fatigue", None) is not None:
            self.fatigue.reset_personal()
        if self.tracker.baseline is not None:
            self.tracker.baseline = BaselineStore(self.tracker.baseline.cfg)
        self.personalization = PersonalizationRuntime(
            dict(self.personalization_cfg, enabled=False), period=self.period,
            normalization=self.ingest_cfg.get("normalization", "linear"),
        )

    def _grant_personalization(self):
        if getattr(self, "fatigue", None) is not None:
            self.fatigue.load_personal()
        baseline = self.tracker.baseline
        if baseline is not None:
            persist = baseline.cfg.get("persist") or {}
            baseline.persist_path = persist.get("path") if persist.get("enabled") else None
        self.personalization = PersonalizationRuntime(
            self._effective_model_config(), period=self.period,
            normalization=self.ingest_cfg.get("normalization", "linear"),
            backend_factory=self._personalization_factory,
        )

    def _expire_pending_request(self, now: float) -> None:
        request = self.pending_request
        if request is None:
            return
        expiry = request["data"].get("expires_in_s")
        if expiry is None or now < request["ts"] + expiry:
            return
        self.pending_request = None
        self._record_feedback({"verdict": "timeout", "request_id": request["data"]["request_id"]},
                              now, request, source="hub")
        if self.control is not None:
            self.control.on_feedback("timeout", now)

    def _record_feedback(self, feedback: dict[str, Any], now: float,
                         request: dict[str, Any] | None, *, source: str = "display") -> None:
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
        if self.personalize_recorder is not None and verdict != "correct":
            self.personalize_recorder.on_feedback(verdict, ts=now, request_id=feedback.get("request_id") or None,
                                      response_ms=response_ms, kind=kind)
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
        record["source"] = source
        self._esm_seq += 1
        self.recorder.record_esm(record)
        self.ondevice.correct(record)
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
        expires = float(self.fsm_cfg["timers"].get("focus_break_poll_sec", 180))
        if self.control is not None and self._control_owns(result.state):
            # 제어가 맡은 제안(ENV, 기본 명령이 있는 POSTURE)은 카드 만료를 디스패처의 제안 시한과 맞춘다
            expires = min(expires, self.control.suggest_timeout)
        self.pending_request = {
            "schema_version": "1.0", "ts": now, "node": "hub", "boot_id": self.boot_id, "seq": self.seq,
            "data": {
                "request_id": f"{self.boot_id}-{self.seq}", "kind": kind, "prompt_code": prompt,
                "options": ["accept", "reject"], "expires_in_s": expires,
                "evidence": list(result.actions), "cause": result.cause,
                "c_fatigue": round(float(result.scores.c_fatigue), 4),
            },
        }
        view = self.personalize_recorder.last_view if self.personalize_recorder is not None else None
        if view is not None and view.get("episode_id") == self.personalize_recorder.last_episode_id:
            # 개인화 정책이 고른 개입과 "왜 이 개입인지" 한 줄 — 화면 카드가 그대로 보여 준다
            self.pending_request["data"]["explanation"] = view.get("explanation")
            self.pending_request["data"]["arm"] = view.get("arm")
            self.pending_request["data"]["arm_label"] = view.get("arm_label")
            if view.get("ventilate_minutes") is not None:       # 창문 환기 카드: "N 분만 열어 주세요"(날씨가 정한 길이)
                self.pending_request["data"]["ventilate_minutes"] = view["ventilate_minutes"]
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
        if hub.personalize_recorder is not None:
            hub.personalize_recorder.path = os.path.join(log_dir, f"personalize-{hub.boot_id}.jsonl")
        print(f"deskmate hub live — broker {broker}:{port}, period {hub.period:.0f}s, logs {log_dir}/"
              + (f", profile {hub.profile}" if hub.profile else ""), file=sys.stderr)
        source.start()
        # 별도 자세 서비스(deskmate_tofposture) 판정을 1 s 마다 받아 온다(ingest.yaml tof.posture_source: service, PC 경로)
        from .ingest.tof_service import TofServicePoller, should_poll
        service_poller = None
        if should_poll(hub.ingest_cfg.get("tof"), os.environ):
            service_poller = TofServicePoller(cache, hub.ingest_cfg.get("tof"),
                                              on_log=lambda m: print(m, file=sys.stderr))
            service_poller.start()
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
            if hub.personalize_recorder is not None:
                hub.personalize_recorder.close_all(time.time())
            if service_poller is not None:
                service_poller.stop()
            source.stop()
    return 0
