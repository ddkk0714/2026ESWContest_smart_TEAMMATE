"""센서 표본 → SensorFrame 변환 (순수 함수).

MVP(2026-09-18) 임시 스케일이다. 모든 숫자는 config/ingest.yaml 에서 오고,
baseline 정규화(features/)가 들어오면 이 선형 매핑을 대체한다.
브로커·스레드 없이 테스트할 수 있도록 상태는 SessionTracker 에만 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..inference import SensorFrame, Signal, State
from .cache import CacheView


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _ramp(value: float | None, low: float, high: float) -> float:
    """low 이하 0, high 이상 1 인 선형 램프."""
    if value is None or high <= low:
        return 0.0
    return _clip((float(value) - low) / (high - low))


@dataclass
class SessionTracker:
    """프레임 사이에 이어지는 상태 — 재실 엣지, 정적 지속, 호흡 소실, 세션 시작 시각."""
    prev_present: bool = False
    still_since: float | None = None
    resp_lost_since: float | None = None
    session_started: float | None = None
    pending_feedback: str | None = None     # accept | reject
    last_state: State = State.IDLE

    def observe_state(self, state: State, now: float) -> None:
        if state is State.START and self.session_started is None:
            self.session_started = now
        if state in (State.IDLE, State.END):
            self.session_started = None
        self.last_state = state


def pc_ratio(view: CacheView, now: float, window_sec: float) -> float:
    ticks = [t for t in view.keystroke_history if now - t.received <= window_sec]
    if not ticks:
        return 0.0
    return sum(1 for t in ticks if t.input_active) / len(ticks)


def build_frame(
    view: CacheView,
    now: float,
    cfg: dict[str, Any],
    tracker: SessionTracker,
    *,
    fsm_state: State,
) -> SensorFrame:
    """캐시 스냅샷 하나로 이번 tick 의 SensorFrame 을 만든다."""
    fresh = cfg["freshness_sec"]
    mm = view.fresh("mmwave", now, fresh["mmwave"])
    env = view.fresh("env", now, fresh["env"])
    ks = view.fresh("keystroke", now, fresh["keystroke"])

    present = bool(mm.data.get("present", False)) if mm else False
    signals: dict[str, Signal] = {}

    # ---- posture (ToF 전까지 mmWave 체동으로 대체) ----
    mcfg = cfg["mmwave"]
    if mm and present:
        level = float(mm.data.get("motion_level") or 0.0)
        if level <= mcfg["still_motion_level_max"]:
            if tracker.still_since is None:
                tracker.still_since = now
        else:
            tracker.still_since = None
        still_sec = (now - tracker.still_since) if tracker.still_since is not None else 0.0
        drowsy = str(mm.data.get("drowsy_state") or "NOLOCK")
        delta = max(
            _clip(still_sec / float(mcfg["still_full_sec"])),
            float(mcfg["drowsy_delta"].get(drowsy, 0.0)),
        )
        signals["posture"] = Signal(phi=_clip(level / 100.0), delta=delta, available=True)

        # ---- respiration: 재실 중 호흡 소실 지속 ----
        if mm.data.get("resp_valid"):
            tracker.resp_lost_since = None
        else:
            if tracker.resp_lost_since is None:
                tracker.resp_lost_since = now
        lost = (now - tracker.resp_lost_since) if tracker.resp_lost_since is not None else 0.0
        signals["respiration"] = Signal(
            phi=0.0, delta=1.0 if lost >= mcfg["resp_lost_sec"] else 0.0, available=True
        )
    else:
        tracker.still_since = None
        tracker.resp_lost_since = None
        signals["posture"] = Signal(available=False)
        signals["respiration"] = Signal(available=False)

    # ---- keystroke ----
    kcfg = cfg["keystroke"]
    if ks and ks.data.get("typing_active"):
        d = ks.data
        phi = _ramp(d.get("idle_ratio"), kcfg["idle_ratio_low"], kcfg["idle_ratio_high"])
        delta = max(
            _ramp(d.get("flight_cv"), kcfg["flight_cv_low"], kcfg["flight_cv_high"]),
            _ramp(d.get("correction_rate"), 0.0, kcfg["correction_rate_high"]),
        )
        signals["keystroke"] = Signal(phi=phi, delta=delta, available=True)
    else:
        signals["keystroke"] = Signal(available=False)

    # ---- environment ----
    ecfg = cfg["environment"]
    if env and env.data.get("co2_valid") and env.data.get("co2_ppm") is not None:
        delta = _ramp(env.data["co2_ppm"], ecfg["co2_ppm_low"], ecfg["co2_ppm_high"])
        signals["environment"] = Signal(phi=0.0, delta=delta, available=True)
    else:
        signals["environment"] = Signal(available=False)

    # ---- elapsed ----
    if tracker.session_started is not None:
        elapsed = now - tracker.session_started
        signals["elapsed"] = Signal(
            phi=0.0, delta=_clip(elapsed / float(cfg["elapsed"]["full_sec"])), available=True
        )
    else:
        signals["elapsed"] = Signal(available=False)

    # ---- 이벤트 플래그 ----
    touch = False
    if cfg.get("auto_start_on_presence") and fsm_state is State.IDLE:
        touch = present and not tracker.prev_present
    tracker.prev_present = present

    action_done = bool(cfg.get("auto_complete_actions")) and fsm_state in (
        State.ACTION_ENV, State.ACTION_POSTURE, State.ACTION_BREAK,
    )
    break_accepted: bool | None = None
    if tracker.pending_feedback in ("accept", "reject") and fsm_state is State.ACTION_BREAK:
        break_accepted = tracker.pending_feedback == "accept"
        action_done = False
    tracker.pending_feedback = None

    return SensorFrame(
        now=now,
        present=present,
        pc_ratio=pc_ratio(view, now, float(cfg["pc_ratio_window_sec"])),
        signals=signals,
        touch=touch,
        action_done=action_done,
        break_accepted=break_accepted,
    )


def sensor_summary(view: CacheView, now: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """state/phase 의 sensor_summary — 화면 표시용 특징 요약. 없는 필드는 생략한다."""
    fresh = cfg["freshness_sec"]
    out: dict[str, Any] = {}
    mm = view.fresh("mmwave", now, fresh["mmwave"])
    if mm:
        out["present"] = bool(mm.data.get("present", False))
        out["mmwave"] = {
            k: mm.data[k]
            for k in ("motion_state", "motion_level", "distance_cm", "drowsy_state")
            if k in mm.data
        }
    env = view.fresh("env", now, fresh["env"])
    if env:
        for k in ("co2_ppm", "temp_c", "humidity_pct", "lux"):
            if env.data.get(k) is not None:
                out[k] = env.data[k]
    ks = view.fresh("keystroke", now, fresh["keystroke"])
    if ks:
        out["keystroke"] = {"ts": ks.ts, **ks.data}
    out["valid"] = bool(mm or env or ks)
    return out
