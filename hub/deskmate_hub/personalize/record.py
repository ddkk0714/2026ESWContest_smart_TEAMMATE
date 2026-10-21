"""기록 전용 연결 — live 가 tick 마다 부르면 개인화 로그(personalization-log.md)를 쌓는다. numpy 없음.

정책(`enabled: false`)을 켜지 않아도 `record: true` 면 결정 tick(`route:<cause>`)마다 에피소드를 열고
(`decision.source: "fallback"`, `arm: null`), 피드백·10 분 뒤 결과를 채워 한 줄로 쓰고, 세션이 끝나면
요약 한 줄(`personalize_session`)을 더 쓴다. 정책을 켜면 같은 자리에서 decide() 결과를 decision 으로 넣으면 된다.

프레임이 싣지 않는 값은 여기서 모은다: 자세 라벨 이력(10 분 분포·지속), 졸음 횟수(30 분), 세션 시각.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .adapter import SituationExtras, extras_from_cache, fsm_record, is_decision_tick, local_clock, situation_from_tick
from .log import PersonalizeLogger, local_iso
from .rewards import REWARD_ACCEPT

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
    """LiveHub 가 소유. on_tick → (link_request) → on_feedback → (on_tick 이 10 분 뒤 닫음)."""

    def __init__(self, cfg: dict[str, Any], path: str, *, boot_id: str, ingest_cfg: dict[str, Any],
                 on_log: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.ingest_cfg = ingest_cfg
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

    @property
    def path(self) -> str:
        return self.logger.path

    @path.setter
    def path(self, value: str) -> None:
        self.logger.path = value

    # ── 프레임 밖 입력 ─────────────────────────────────────────
    def _posture_label(self, view: Any, now: float) -> tuple[str, bool]:
        """(자세 라벨, 졸음). ToF 0x03 이 신선하면 그 라벨(MLP 덮어쓰기 포함), 아니면 unknown. 자리비움은 away."""
        from ..ingest.mapping import _tof_with_mlp

        fresh = self.ingest_cfg.get("freshness_sec") or {}
        tof = view.fresh("tof", now, fresh.get("tof", 3)) if "tof" in self.ingest_cfg else None
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
        state, prev = _state_name(result.state), _state_name(prev_state)
        label, drowsy = self._observe_inputs(view, now)
        hour, _, _ = local_clock(now, self.offset)

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
        situation = situation_from_tick(frame, result, self._extras(view, now, label, drowsy), self.cfg)
        eid = self.logger.open_episode(
            session_id=self.session_id, situation=situation, fsm=fsm,
            decision=decision if decision is not None else fallback_decision(result.gate, fsm_action=fsm_action),
            ts=now)
        self._episodes[eid] = {"cause": fsm["cause"], "gate": gate, "upright_seen": False}
        self.last_intervention_ts = now
        self.last_episode_id = eid
        return eid

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
        self.logger.feedback(eid, verdict, ts=ts, response_ms=response_ms, request_id=request_id)
        return eid

    # ── 닫기 ───────────────────────────────────────────────────
    def _close(self, eid: str, *, status: str, c_after: float | None, next_state: str | None) -> None:
        meta = self._episodes.pop(eid, {})
        rec = self.logger._open.get(eid)
        if rec is None:
            return
        if rec.get("feedback") is None and meta.get("gate") == "auto":
            rec["feedback"] = {"verdict": "auto_kept", "response_ms": None, "feedback_ts_ms": None, "request_id": None}
        verdict = (rec.get("feedback") or {}).get("verdict")
        self.logger.close(
            eid, status=status, c_fatigue_after=c_after, reward_accept=REWARD_ACCEPT.get(verdict, 0.0),
            posture_recovered=bool(meta.get("upright_seen")) if meta.get("cause") == "posture" else None,
            next_state=next_state)
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
