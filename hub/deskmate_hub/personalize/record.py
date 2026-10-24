"""기록 연결 — live 가 tick 마다 부르면 개인화 로그(personalization-log.md)를 쌓는다. 순수 Python(Pi 4).

정책(`enabled: false`)을 켜지 않아도 `record: true` 면 결정 tick(`route:<cause>`)마다 에피소드를 열고
(`decision.source: "fallback"`, `arm: null`), 피드백·10 분 뒤 결과를 채워 한 줄로 쓰고, 세션이 끝나면
요약 한 줄(`personalize_session`)을 더 쓴다. 정책을 켜면 같은 자리에서 decide() 결과를 decision 으로 넣으면 된다.

프레임이 싣지 않는 값은 여기서 모은다: 자세 라벨 이력(10 분 분포·지속), 졸음 횟수(30 분), 세션 시각.
"""
from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .adapter import (SituationExtras, arm_commands, extras_from_cache, fsm_record, is_decision_tick, local_clock,
                      situation_from_tick)
from .log import PersonalizeLogger, local_iso
from .onboarding import kept, load_profile, parse_hhmm, parse_profile, profile_adjustments, save_profile
from .profile import ProfileSummary
from .rewards import REWARD_ACCEPT, reward_effect
from .signals import ImplicitDimTracker, WakeTracker

SESSION_END_STATES = ("END", "IDLE")
SLUMP_MIN_HOLD_SEC = 120.0          # 로그 스키마 §8: 2 분 이상 지속돼야 슬럼프 구간
DROWSY_WINDOW_SEC = 1800.0


def _state_name(state: Any) -> str:
    return str(getattr(state, "value", state))


@dataclass
class SessionStats:
    """personalize_session 한 줄의 집계. tick 마다 observe()."""
    session_id: str
    started_ts: float
    start_hour_local: float
    slump_labels: tuple[str, ...]
    first_slump_min: float | None = None
    slump_count: int = 0
    recovery_after_alert_min: list[float] = field(default_factory=list)
    break_interval_min: list[float] = field(default_factory=list)
    fatigue_by_hour: dict[str, list[float]] = field(default_factory=dict)
    co2_at_fatigue: list[float] = field(default_factory=list)
    _slump_since: float | None = None
    _slump_counted: bool = False
    _alert_ts: float | None = None
    _last_rest_ts: float | None = None
    _prev_state: str | None = None

    def observe(self, now: float, hour_local: float, state: str, c_fatigue: float, posture_label: str,
                co2_ppm: float | None, env_flags: list[str] | tuple[str, ...]) -> None:
        acc = self.fatigue_by_hour.setdefault(str(int(hour_local)), [0.0, 0])
        acc[0] += float(c_fatigue)
        acc[1] += 1
        if posture_label in self.slump_labels:
            if self._slump_since is None:
                self._slump_since, self._slump_counted = now, False
            if not self._slump_counted and now - self._slump_since >= SLUMP_MIN_HOLD_SEC:
                self._slump_counted = True
                self.slump_count += 1
                if self.first_slump_min is None:
                    self.first_slump_min = round((self._slump_since - self.started_ts) / 60.0, 2)
        else:
            self._slump_since = None
        if state != self._prev_state:
            if state == "ACTION_POSTURE":
                self._alert_ts = now
            elif state == "REST":
                if self._last_rest_ts is not None:
                    self.break_interval_min.append(round((now - self._last_rest_ts) / 60.0, 2))
                self._last_rest_ts = now
            elif state == "FATIGUE" and co2_ppm is not None and any(
                    f in ("co2_high", "co2_rise", "co2_rising") for f in env_flags):
                self.co2_at_fatigue.append(float(co2_ppm))
            self._prev_state = state
        if self._alert_ts is not None and posture_label == "upright":
            self.recovery_after_alert_min.append(round((now - self._alert_ts) / 60.0, 2))
            self._alert_ts = None

    def record(self, now: float, end_hour_local: float, utc_offset_hours: float) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "ts_ms": int(self.started_ts * 1000), "local_time": local_iso(self.started_ts, utc_offset_hours),
            "start_hour_local": round(self.start_hour_local, 2), "end_hour_local": round(end_hour_local, 2),
            "length_min": round((now - self.started_ts) / 60.0, 2),
            "first_slump_min": self.first_slump_min, "slump_count": self.slump_count,
            "recovery_after_alert_min": list(self.recovery_after_alert_min),
            "break_interval_min": list(self.break_interval_min),
            "fatigue_by_hour": {h: [round(s, 4), n] for h, (s, n) in self.fatigue_by_hour.items()},
            "co2_at_fatigue": list(self.co2_at_fatigue),
        }


def fallback_decision(gate: Any, *, fsm_action: str | None) -> dict[str, Any]:
    """정책을 돌리지 않은 결정(기록 전용). 로그 스키마 §4 와 같은 키, arm 은 null."""
    return {
        "arm": None, "candidates": [], "excluded": {}, "source": "fallback",
        "policy_mode": "global", "n_personal": 0, "scores": {},
        "delivered_gate": _state_name(gate), "auto_threshold": None,
        "explanation": f"정책 미적용(기록 전용) · FSM 기본 동작 {fsm_action or '-'}",
        "ventilate_minutes": None,
    }


class EpisodeRecorder:
    """LiveHub 가 소유. on_tick → (link_request) → on_feedback → (on_tick 이 10 분 뒤 닫음).

    policy(InterventionPolicy)를 주면 결정 tick 에 decide() 를 불러 그 결과를 에피소드의 decision 으로
    쓰고, 피드백·10 분 뒤 결과를 정책에 되돌려 준 뒤 policy_path 에 저장한다. 없으면 기록만(fallback).
    """

    def __init__(self, cfg: dict[str, Any], path: str, *, boot_id: str, ingest_cfg: dict[str, Any],
                 on_log: Callable[[str], None] | None = None, policy: Any = None, policy_path: str | None = None):
        self.cfg = cfg
        self.ingest_cfg = ingest_cfg
        self.policy = policy
        self.policy_path = policy_path
        self.last_decision: Any = None          # 이번 tick 에 정책이 낸 Decision (없으면 None)
        self.last_view: dict[str, Any] | None = None   # 화면용 요약(state/phase sensor_summary.personalize)
        self._on_log = on_log
        self.offset = float(cfg.get("local_utc_offset_hours", 9))
        fcfg = cfg.get("features") or {}
        self.slump_labels = tuple(fcfg.get("slump_labels") or ("recline", "face_down", "chin_rest", "drowsy"))
        self.posture_window_sec = float(fcfg.get("posture_window_min", 10)) * 60.0
        self.logger = PersonalizeLogger(path, boot_id=boot_id, cfg=cfg, on_log=on_log)
        self._session_seq = 0
        self.session_id: str | None = None
        self.session_started_ts: float | None = None
        self.last_intervention_ts: float | None = None
        self.stats: SessionStats | None = None
        self._posture: deque[tuple[float, str]] = deque()      # (ts, label) 최근 posture_window
        self._posture_since: tuple[str, float] | None = None   # (label, since_ts)
        self._slump_since: float | None = None
        self._drowsy_ts: deque[float] = deque()                # 졸음 false→true 전이 시각(30 분)
        self._drowsy_prev = False
        self._episodes: dict[str, dict[str, Any]] = {}         # eid → {cause, gate, upright_seen}
        self._by_request: dict[str, str] = {}
        self.last_episode_id: str | None = None
        self.last_posture_label = "unknown"
        # 묻지 않고 얻는 신호(signals.py, 2026-10-25): 밝힘 뒤 직접 어둡게 함 · 깨우기 성공
        self.implicit = ImplicitDimTracker(cfg.get("implicit"))
        self.wake = WakeTracker(cfg.get("wake"))
        self.last_signal: dict[str, Any] | None = None     # 화면 설명 한 줄(sensor_summary.personalize.signal)
        self._ctl_links: list[tuple[Any, str | None]] = []  # (제어 Episode, 개인화 에피소드 id) — 최근 8개
        self._seen_cmds: deque[str] = deque(maxlen=128)
        # 첫 설정 프로필(onboarding.py): deskmate/profile/user → prior·취침 시각. profiles/<id>/user_profile.json 에 유지
        self.onboarding_cfg = cfg.get("onboarding") or {}
        self.user_profile: dict[str, Any] | None = None
        self.profile_summary: ProfileSummary | None = None
        self._sit_cfg = cfg
        self._profile_seen: float | None = None
        base = (os.path.dirname(policy_path) if policy_path else
                os.path.join(os.path.dirname(path) or str(cfg.get("log_dir") or "logs"), "profiles",
                             str(cfg.get("profile_id", "default"))))
        self.user_profile_path = os.path.join(base, str(self.onboarding_cfg.get("file", "user_profile.json")))
        if self.onboarding_cfg.get("enabled"):
            stored = load_profile(self.user_profile_path)
            if stored is not None:
                self._apply_profile(stored, now=None, save=False)

    @property
    def path(self) -> str:
        return self.logger.path

    @path.setter
    def path(self, value: str) -> None:
        self.logger.path = value

    # ── 프레임 밖 입력 ─────────────────────────────────────────
    def _posture_label(self, view: Any, now: float) -> tuple[str, bool]:
        """(자세 라벨, 졸음). ToF 0x03(또는 자세 서비스 판정)이 신선하면 그 라벨(모델 덮어쓰기 포함), 아니면 unknown. 자리비움은 away."""
        from ..ingest.mapping import _tof_sample, _tof_with_mlp

        fresh = self.ingest_cfg.get("freshness_sec") or {}
        # 0x03 이 없고 자세 서비스(GRU) 판정만 신선해도 그 라벨을 쓴다(FSM 과 같은 규칙, mapping._tof_sample) — 깨우기 판정이 본다
        tof = _tof_sample(view, now, self.ingest_cfg) if "tof" in self.ingest_cfg and "freshness_sec" in self.ingest_cfg else None
        drowsy = False
        label = "unknown"
        if tof is not None:
            if not tof.data.get("present", False):
                label = "away"
            else:
                data = _tof_with_mlp(view, tof.data, now, self.ingest_cfg)
                label = str(data.get("posture") or "unknown")
            drowsy = bool(tof.data.get("drowsy"))
        mm = view.fresh("mmwave", now, fresh.get("mmwave", 3)) if fresh else None
        if mm is not None and str(mm.data.get("drowsy_state") or "") == "DROWSY":
            drowsy = True
        return label, drowsy

    def _observe_inputs(self, view: Any, now: float) -> tuple[str, bool]:
        label, drowsy = self._posture_label(view, now)
        self.last_posture_label = label
        self._posture.append((now, label))
        while self._posture and now - self._posture[0][0] > self.posture_window_sec:
            self._posture.popleft()
        if self._posture_since is None or self._posture_since[0] != label:
            self._posture_since = (label, now)
        if label in self.slump_labels:
            if self._slump_since is None:
                self._slump_since = now
        else:
            self._slump_since = None
        if drowsy and not self._drowsy_prev:
            self._drowsy_ts.append(now)
        self._drowsy_prev = drowsy
        while self._drowsy_ts and now - self._drowsy_ts[0] > DROWSY_WINDOW_SEC:
            self._drowsy_ts.popleft()
        return label, drowsy

    def _extras(self, view: Any, now: float, label: str, drowsy: bool) -> SituationExtras:
        counted = [lbl for _, lbl in self._posture if lbl != "unknown"]
        dist = {lbl: counted.count(lbl) / len(counted) for lbl in set(counted)} if counted else {}
        base = SituationExtras(
            posture_dist=dist, posture_label=label,
            posture_hold_min=(now - self._posture_since[1]) / 60.0 if self._posture_since else 0.0,
            slump_hold_min=(now - self._slump_since) / 60.0 if self._slump_since is not None else 0.0,
            drowsy=drowsy, drowsy_count_30min=len(self._drowsy_ts),
            session_started_ts=self.session_started_ts, last_intervention_ts=self.last_intervention_ts,
        )
        return extras_from_cache(view, now, self.cfg, base=base)

    # ── tick ───────────────────────────────────────────────────
    def on_tick(self, frame: Any, result: Any, prev_state: Any, view: Any, now: float, *,
                retry: bool = False, env_flags: list[str] | tuple[str, ...] = (),
                fsm_action: str | None = None, decision: dict[str, Any] | None = None) -> str | None:
        """결정 tick 이면 에피소드를 열고 id 를 돌려준다. 기한이 지난 에피소드는 닫는다. 세션 시작·끝도 여기서."""
        self.last_episode_id = None
        self.last_decision = None
        state, prev = _state_name(result.state), _state_name(prev_state)
        label, drowsy = self._observe_inputs(view, now)
        hour, _, _ = local_clock(now, self.offset)
        self._maybe_apply_profile(view, now)
        self._observe_signals(view, now, label, float(result.scores.c_fatigue))

        if state == "START" and prev != "START":
            self._end_session(now, hour, next_state=state)
            self._session_seq += 1
            self.session_id = f"{self.logger.boot_id}-s{self._session_seq}"
            self.session_started_ts = now
            self.last_intervention_ts = None
            self.stats = SessionStats(session_id=self.session_id, started_ts=now, start_hour_local=hour,
                                      slump_labels=self.slump_labels)
        elif state in SESSION_END_STATES and prev not in SESSION_END_STATES:
            self._end_session(now, hour, next_state=state)

        if self.stats is not None:
            co2 = None
            env = view.fresh("env", now, (self.ingest_cfg.get("freshness_sec") or {}).get("env", 30))
            if env is not None and env.data.get("co2_valid") is True and env.data.get("co2_ppm") is not None:
                co2 = float(env.data["co2_ppm"])
            self.stats.observe(now, hour, state, float(result.scores.c_fatigue), label, co2, env_flags)

        if label == "upright":
            for meta in self._episodes.values():
                meta["upright_seen"] = True
        for eid in self.logger.due(now):
            self._close(eid, status="measured", c_after=float(result.scores.c_fatigue), next_state=state)

        if self.session_id is None or not is_decision_tick(result):
            return None
        gate = _state_name(result.gate)
        if gate == "none":
            return None
        fsm = fsm_record(result, retry=retry)
        situation = situation_from_tick(frame, result, self._extras(view, now, label, drowsy), self._sit_cfg,
                                        profile=self.profile_summary)
        if decision is None and self.policy is not None and fsm["cause"]:
            try:
                decision = self.policy.decide(situation, cause=fsm["cause"], gate=result.gate,
                                              session_id=self.session_id, now=now)
            except Exception as exc:  # 정책 오류가 허브를 멈추면 안 된다 — 기록만 남기고 FSM 기본 동작
                self._log(f"[personalize] decide 실패, FSM 기본 동작: {exc}")
                decision = None
        if (decision is not None and getattr(decision, "delivered_gate", None) == "none"
                and getattr(decision, "source", "") == "fallback"):
            decision = None   # 후보 없음·세션 상한: FSM 기본 동작 그대로, 로그엔 fallback 로 남긴다
        self.last_decision = decision if not isinstance(decision, dict) else None
        eid = self.logger.open_episode(
            session_id=self.session_id, situation=situation, fsm=fsm,
            decision=decision if decision is not None else fallback_decision(result.gate, fsm_action=fsm_action),
            ts=now)
        delivered = self.last_decision.delivered_gate if self.last_decision is not None else gate
        self._episodes[eid] = {"cause": fsm["cause"], "gate": delivered, "upright_seen": False}
        trigger = self.wake.wants(fsm["cause"], label, drowsy, float(result.scores.c_fatigue))
        if trigger is not None:
            self.wake.start(eid, now, trigger, float(result.scores.c_fatigue))
            self._episodes[eid]["wake"] = True
        self.last_intervention_ts = now
        self.last_episode_id = eid
        self.last_view = self._view(eid, self.logger._open[eid]["decision"], fsm, delivered)
        return eid

    def _log(self, message: str) -> None:
        if self._on_log:
            self._on_log(message)

    def _view(self, eid: str, decision: dict[str, Any], fsm: dict[str, Any], delivered: str) -> dict[str, Any]:
        label = None
        if self.policy is not None and decision.get("arm") in getattr(self.policy, "arms", {}):
            label = self.policy.arms[decision["arm"]].label_ko
        return {"episode_id": eid, "arm": decision.get("arm"), "arm_label": label, "source": decision.get("source"),
                "explanation": decision.get("explanation"), "fsm_gate": fsm.get("gate"), "delivered_gate": delivered,
                "policy_mode": decision.get("policy_mode"), "n_personal": decision.get("n_personal"),
                "auto_threshold": decision.get("auto_threshold"), "ventilate_minutes": decision.get("ventilate_minutes"),
                "signal": self.last_signal}

    # ── 첫 설정 프로필 ─────────────────────────────────────────
    def _maybe_apply_profile(self, view: Any, now: float) -> None:
        sample = getattr(view, "user_profile", None)
        if sample is None or not self.onboarding_cfg.get("enabled") or sample.received == self._profile_seen:
            return
        self._profile_seen = sample.received
        profile = parse_profile(sample.data)
        if profile is None:
            self._log("[personalize] profile/user: 아는 필드가 없어 무시")
            return
        self._apply_profile(profile, now=now, save=True)

    def _apply_profile(self, profile: dict[str, Any], *, now: float | None, save: bool) -> None:
        ocfg = self.onboarding_cfg
        prof = kept(profile, ocfg.get("keep_fields"))
        if prof == self.user_profile:
            return          # retain 재수신·재시작 뒤 같은 값 — 이미 반영됨
        self.user_profile = prof
        if save:
            try:
                save_profile(self.user_profile_path, prof, received_ts=now)
            except OSError as exc:
                self._log(f"[personalize] 프로필 저장 실패: {exc}")
        if prof.get("profile_id"):
            self.logger.profile_id = prof["profile_id"]
        if self.policy is not None:
            try:
                notes = self.policy.apply_user_profile(prof)
            except (KeyError, TypeError, ValueError) as exc:   # mapping 표 오류가 허브를 멈추면 안 된다 — prior 는 그대로
                self._log(f"[personalize] 프로필 prior 반영 실패: {exc}")
                notes = []
            self._save_policy()
        else:
            notes = profile_adjustments(prof, ocfg)[2]
        bed = prof.get("bedtime") if ocfg.get("bedtime_from_profile", True) else None
        if bed:
            self.profile_summary = ProfileSummary(usual_bedtime=bed)
            if self.policy is not None:
                if self.policy.profile is None:
                    self.policy.profile = self.profile_summary
                elif getattr(self.policy.profile, "usual_bedtime", None) is None:
                    self.policy.profile.usual_bedtime = bed
            notes.append(f"취침 {bed} 기준")
        wake = parse_hhmm(prof.get("wake_time")) if ocfg.get("wake_sets_night_end", True) else None
        if wake is not None:
            self._sit_cfg = dict(self.cfg, profile=dict(self.cfg.get("profile") or {}, night_end_hour=wake))
        self._set_signal("profile", "첫 설정 반영: " + (" · ".join(notes) if notes else "기본 기준 유지"), now)

    def _set_signal(self, kind: str, text: str, now: float | None) -> None:
        self.last_signal = {"kind": kind, "text_ko": text, "ts": now}
        self.last_view = dict(self.last_view or {}, signal=self.last_signal)
        self._log(f"[personalize] {text}")

    # ── 묻지 않고 얻는 신호 ─────────────────────────────────────
    def _activity(self, view: Any, now: float) -> bool:
        """깨우기 판정의 '활동 재개': 키 입력, ToF 움직임(0x03 motion_score·자세 판정 tof_motion_mm)."""
        w = self.cfg.get("wake") or {}
        fresh = self.ingest_cfg.get("freshness_sec") or {}
        ks = view.fresh("keystroke", now, fresh.get("keystroke", 5))
        if ks is not None and (ks.data.get("typing_active") or ks.data.get("input_active")):
            return True
        tof = view.fresh("tof", now, fresh.get("tof", 3))
        score = tof.data.get("motion_score") if tof is not None else None
        if isinstance(score, (int, float)) and float(score) >= float(w.get("motion_score_min", 0.2)):
            return True
        for kind in ("tof_mlp", "tof_service"):
            sample = view.fresh(kind, now, fresh.get("tof", 3))
            mm = sample.data.get("tof_motion_mm") if sample is not None else None
            if isinstance(mm, (int, float)) and float(mm) >= float(w.get("motion_mm_min", 8.0)):
                return True
        return False

    def _observe_signals(self, view: Any, now: float, label: str, c_fatigue: float) -> None:
        env = view.fresh("env", now, (self.ingest_cfg.get("freshness_sec") or {}).get("env", 30))
        if env is not None and env.data.get("lux_valid") is True and env.data.get("lux") is not None:
            self._handle_signal(self.implicit.on_lux(float(env.data["lux"]), now), now)
        if self.wake.watches:
            for watch, reward in self.wake.observe(now, label, c_fatigue, self._activity(view, now)):
                self._wake_done(watch, reward, now)

    def _wake_done(self, watch: Any, reward: float, now: float) -> None:
        eid = watch.episode_id
        info = {"result": watch.result, "trigger": watch.trigger, "reward": reward, **watch.detail}
        meta = self._episodes.get(eid)
        if meta is not None:
            meta["wake_info"] = info
        mode = str((self.cfg.get("wake") or {}).get("effect_mode", "replace"))
        d = self.logger.decision_of(eid)
        if mode == "replace" and d is not None and self.policy is not None:
            # 10 분 C_fatigue 추세 대신 깨우기 결과로 효과 축을 바로 갱신한다(닫을 때 다시 갱신하지 않는다)
            self.policy.outcome(d, None, effect_override=reward)
            self._save_policy()
            if meta is not None:
                meta["effect_done"] = True
        self._set_signal("wake", WakeTracker.text_ko(watch), now)

    def _dim_targets(self, decision: Any) -> list[str]:
        arms = list((self.cfg.get("implicit") or {}).get("brighten_arms") or ["light_cool_bright"])
        arm = getattr(decision, "arm", None)
        return [arm] if arm in arms else arms

    def _handle_signal(self, sig: dict[str, Any] | None, now: float) -> None:
        if sig is None:
            return
        eid = sig.get("episode_id")
        entry = {"kind": sig["kind"], "reward": sig.get("reward"), "ts_ms": int(now * 1000),
                 "reason": sig.get("reason"), "detail": sig.get("detail")}
        if sig["kind"] != "ignored":
            rec = self.logger._open.get(eid or "")
            if rec is not None:
                rec.setdefault("signals", []).append(entry)
            meta = self._episodes.get(eid or "")
            if meta is not None:
                meta["implicit_neg"] = True
            d = sig.get("decision")
            updated: list[str] = []
            if self.policy is not None and d is not None and not isinstance(d, dict):
                updated = self.policy.implicit_feedback(d, float(sig["reward"]), arms=self._dim_targets(d))
                self._save_policy()
            entry["policy_arms"] = updated
            self._set_signal(sig["kind"], sig["text_ko"], now)
        else:
            self._log(f"[personalize] {sig['text_ko']}")
        self.logger.signal({"episode_id": eid, "session_id": self.session_id, "text_ko": sig["text_ko"], **entry})

    def on_manual_adjust(self, data: dict[str, Any], *, ts: float) -> dict[str, Any] | None:
        """feedback/user kind=manual_adjust(앱 조명 조절). 밝힘 뒤 창 안의 어둡게 하기면 음의 보상."""
        sig = self.implicit.on_manual_adjust(data, ts)
        self._handle_signal(sig, ts)
        return sig

    def on_control_result(self, payload: dict[str, Any], ts: float) -> None:
        self.implicit.on_result(payload, ts)

    def observe_control(self, control: Any, now: float) -> None:
        """제어 디스패처가 낸 명령을 본다(디스패처는 건드리지 않는다). 무드등 명령 → ImplicitDimTracker."""
        if control is None:
            return
        episodes = list(control.history[-5:]) + ([control.episode] if control.episode is not None else [])
        for ep in episodes:
            eid = self._eid_for(ep, now)
            for c in ep.commands:
                if c.sent_ts is None or c.command_id in self._seen_cmds:
                    continue
                self._seen_cmds.append(c.command_id)
                payload = {"command_id": c.command_id, "target_id": c.target_id, "operation": c.operation,
                           "value": c.value, "gate": c.gate}
                self.implicit.on_command(payload, float(c.sent_ts), episode_id=eid,
                                         decision=self.logger.decision_of(eid) if eid else None)

    def _eid_for(self, ep: Any, now: float) -> str | None:
        for known, eid in self._ctl_links:
            if known is ep:
                return eid
        # 제어 에피소드는 개인화 에피소드와 같은 tick 에 열린다(live: on_tick → on_enter_action)
        eid = self.last_episode_id if abs(float(getattr(ep, "started_ts", -1.0)) - now) < 1e-6 else None
        self._ctl_links.append((ep, eid))
        del self._ctl_links[:-8]
        return eid

    def commands_for(self, decision: Any) -> list[dict[str, Any]] | None:
        """정책이 고른 arm 의 기기 명령 spec(control.yaml actions 형식). 정책이 없거나 arm 이 없으면 None(= FSM 기본)."""
        if decision is None or self.policy is None or not getattr(decision, "arm", None):
            return None
        spec = self.policy.arms.get(decision.arm)
        return arm_commands(spec.device if spec else None)

    def _save_policy(self) -> None:
        if self.policy is not None and self.policy_path:
            try:
                self.policy.save(self.policy_path)
            except OSError as exc:
                self._log(f"[personalize] 정책 저장 실패: {exc}")

    def link_request(self, request_id: str, episode_id: str | None = None) -> None:
        """interaction/request 의 id 를 이번 tick 에 연 에피소드에 묶는다(피드백이 그 id 로 돌아온다)."""
        eid = episode_id or self.last_episode_id
        if eid is not None and eid in self._episodes:
            self._by_request[request_id] = eid

    def on_feedback(self, verdict: str, *, ts: float, request_id: str | None = None,
                    response_ms: int | None = None, kind: str | None = None) -> str | None:
        """display/hub 피드백을 에피소드에 붙인다. 자동 실행 되돌리기(kind auto_undo)는 가장 최근 auto 에피소드로."""
        if kind == "auto_undo":
            verdict = "undo"
            eid = next((e for e in reversed(list(self._episodes)) if self._episodes[e]["gate"] == "auto"), None)
        else:
            eid = self._by_request.get(request_id or "")
        if eid is None or eid not in self._episodes or verdict not in REWARD_ACCEPT:
            return None
        if self.logger._open[eid].get("feedback") is not None:
            return None   # 한 에피소드에 피드백은 한 번(늦게 온 중복·timeout 뒤 수락은 버린다)
        self.logger.feedback(eid, verdict, ts=ts, response_ms=response_ms, request_id=request_id)
        d = self.logger.decision_of(eid)
        if d is not None and self.policy is not None:
            self.policy.feedback(d, verdict, now=ts)
            self._save_policy()
        return eid

    # ── 닫기 ───────────────────────────────────────────────────
    def _close(self, eid: str, *, status: str, c_after: float | None, next_state: str | None) -> None:
        meta = self._episodes.pop(eid, {})
        rec = self.logger._open.get(eid)
        if rec is None:
            return
        wake_info = meta.get("wake_info")
        if meta.get("wake") and wake_info is None:
            self.wake.drop(eid)
            wake_info = {"result": "pending"}
        override = None
        if wake_info and wake_info.get("result") in ("success", "fail"):
            wcfg = self.cfg.get("wake") or {}
            if str(wcfg.get("effect_mode", "replace")) == "replace":
                override = float(wake_info["reward"])
            elif status == "measured" and c_after is not None:
                fat = reward_effect(float(rec["features"]["c_fatigue"]), float(c_after),
                                    float(self.cfg["bandit"]["effect_full_drop"]))
                b = float(wcfg.get("blend", 0.5))
                override = (1.0 - b) * fat + b * float(wake_info["reward"])
        if rec.get("feedback") is None and meta.get("gate") == "auto" and not meta.get("implicit_neg"):
            rec["feedback"] = {"verdict": "auto_kept", "response_ms": None, "feedback_ts_ms": None, "request_id": None}
        verdict = (rec.get("feedback") or {}).get("verdict")
        d = self.logger.decision_of(eid)
        if d is not None and self.policy is not None:
            if verdict == "auto_kept":
                self.policy.feedback(d, "auto_kept", now=None)
            if not meta.get("effect_done"):
                self.policy.outcome(d, c_after, status=status, effect_override=override)
            self._save_policy()
        self.logger.close(
            eid, status=status, c_fatigue_after=c_after, reward_accept=REWARD_ACCEPT.get(verdict, 0.0),
            posture_recovered=bool(meta.get("upright_seen")) if meta.get("cause") == "posture" else None,
            next_state=next_state, reward_effect_override=override,
            extra={"wake": wake_info} if wake_info is not None else None)
        if self.last_view is not None and self.last_view.get("episode_id") == eid:
            self.last_view = dict(self.last_view, closed=True, outcome=status)
        for rid, e in list(self._by_request.items()):
            if e == eid:
                del self._by_request[rid]

    def _end_session(self, now: float, hour: float, *, next_state: str) -> None:
        if self.stats is None:
            return
        for eid in list(self._episodes):
            self._close(eid, status="session_ended", c_after=None, next_state=next_state)
        self.logger.flush(status="session_ended", next_state=next_state)
        self.logger.session_summary(self.stats.record(now, hour, self.offset))
        self.stats = None
        self.session_id = None
        self.session_started_ts = None

    def close_all(self, now: float) -> None:
        """프로세스 종료. 열린 에피소드는 missing, 진행 중 세션은 요약만."""
        hour, _, _ = local_clock(now, self.offset)
        for eid in list(self._episodes):
            self._close(eid, status="missing", c_after=None, next_state=None)
        if self.stats is not None:
            self.logger.session_summary(self.stats.record(now, hour, self.offset))
            self.stats = None
