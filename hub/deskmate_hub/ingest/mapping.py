"""센서 표본 → SensorFrame 변환 (순수 함수).

MVP(2026-09-18) 임시 스케일이다. 모든 숫자는 config/ingest.yaml 에서 오고,
baseline 정규화(features/)가 들어오면 이 선형 매핑을 대체한다.
브로커·스레드 없이 테스트할 수 있도록 상태는 SessionTracker 에만 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..features import BaselineStore
from ..inference import SensorFrame, Signal, State
from .cache import CacheView


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _ramp(value: float | None, low: float, high: float) -> float:
    """low 이하 0, high 이상 1 인 선형 램프."""
    if value is None or high <= low:
        return 0.0
    return _clip((float(value) - low) / (high - low))


def _environment_values(data: dict[str, Any]) -> dict[str, float]:
    """무효 표본의 이전 숫자가 환경 증거와 실행 이유에 섞이지 않게 한다."""
    return {
        key: float(data[key])
        for key, valid in (("co2_ppm", "co2_valid"), ("temp_c", "temp_valid"),
                           ("humidity_pct", "humidity_valid"), ("lux", "lux_valid"))
        if data.get(valid) is True and data.get(key) is not None
    }


def _comfort_deviation(value: float, low: float, high: float, margin: float) -> float:
    return _clip(max(low - value, value - high, 0.0) / margin)


def _environment_scores(values: dict[str, float], cfg: dict[str, Any],
                        session_co2_start: float | None = None) -> dict[str, float]:
    scores: dict[str, float] = {}
    if "co2_ppm" in values:
        co2 = values["co2_ppm"]
        scores["co2_abs"] = _ramp(co2, cfg["co2_ppm_low"], cfg["co2_ppm_high"])
        scores["co2_rise"] = (_ramp(co2 - session_co2_start, cfg["co2_rise_low"],
                                    cfg["co2_rise_high"]) if session_co2_start is not None else 0.0)
    if "temp_c" in values:
        scores["temp_dev"] = _comfort_deviation(values["temp_c"], cfg["temp_comfort_min"],
                                                 cfg["temp_comfort_max"], cfg["temp_margin"])
    if "humidity_pct" in values:
        scores["humidity_dev"] = _comfort_deviation(
            values["humidity_pct"], cfg["humidity_comfort_min"],
            cfg["humidity_comfort_max"], cfg["humidity_margin"])
    if "lux" in values:
        scores["lux_dim"] = 1.0 - _ramp(values["lux"], cfg["lux_dim_low"], cfg["lux_dim_high"])
    return scores


@dataclass
class SessionTracker:
    """프레임 사이에 이어지는 상태 — 재실 엣지, 정적 지속, 호흡 소실, 세션 시작 시각."""
    prev_present: bool = False
    still_since: float | None = None
    resp_lost_since: float | None = None
    session_started: float | None = None
    co2_session_start: float | None = None
    pending_feedback: str | None = None     # accept | reject
    last_state: State = State.IDLE
    baseline: BaselineStore | None = None   # normalization: baseline 일 때만

    def observe_state(self, state: State, now: float) -> None:
        if state is State.START and self.session_started is None:
            self.session_started = now
        if state in (State.IDLE, State.END):
            self.session_started = None
            self.co2_session_start = None
        if self.baseline is not None:
            # START(기준선 측정) 진입 → 보정 창 시작, START 이탈 → 확정
            if state is State.START and self.last_state is not State.START:
                self.baseline.begin_calibration()
            elif self.last_state is State.START and state is not State.START:
                self.baseline.end_calibration(now)
        self.last_state = state

    def evidence(self, metric: str, x: float | None, linear: float, *, direction: int = 1, now: float | None = None) -> float:
        """원지표 x → [0,1] 증거. 기준선이 있으면 Modified z, 없으면 linear 램프 값을 쓴다. 보정 창이면 표본을 모은다."""
        if self.baseline is None:
            return linear
        self.baseline.observe(metric, x)
        z = self.baseline.normalize(metric, x, direction=direction, now=now)
        return linear if z is None else z


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
        phi = tracker.evidence("motion_level", level, _clip(level / 100.0), now=now)
        signals["posture"] = Signal(phi=phi, delta=delta, available=True)

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
        phi = tracker.evidence("idle_ratio", d.get("idle_ratio"),
                               _ramp(d.get("idle_ratio"), kcfg["idle_ratio_low"], kcfg["idle_ratio_high"]), now=now)
        delta = max(
            tracker.evidence("flight_cv", d.get("flight_cv"),
                             _ramp(d.get("flight_cv"), kcfg["flight_cv_low"], kcfg["flight_cv_high"]), now=now),
            tracker.evidence("correction_rate", d.get("correction_rate"),
                             _ramp(d.get("correction_rate"), 0.0, kcfg["correction_rate_high"]), now=now),
        )
        signals["keystroke"] = Signal(phi=phi, delta=delta, available=True)
    else:
        signals["keystroke"] = Signal(available=False)

    # ---- environment ----
    ecfg = cfg["environment"]
    values = _environment_values(env.data) if env else {}
    if values:
        if ("co2_ppm" in values and tracker.session_started is not None
                and env is not None and env.received >= tracker.session_started
                and tracker.co2_session_start is None):
            # 세션 시작 뒤 첫 유효값만 기준으로 고정해 느린 누적 상승을 놓치지 않는다.
            tracker.co2_session_start = values["co2_ppm"]
        scores = _environment_scores(values, ecfg, tracker.co2_session_start if tracker.session_started is not None else None)
        if "co2_ppm" in values:
            scores["co2_abs"] = tracker.evidence("co2_ppm", values["co2_ppm"],
                                                  scores["co2_abs"], now=now)
        signals["environment"] = Signal(
            phi=max(scores.get("temp_dev", 0.0), scores.get("humidity_dev", 0.0)),
            delta=max(scores.get("co2_abs", 0.0), scores.get("co2_rise", 0.0),
                      scores.get("lux_dim", 0.0)), available=True)
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


def sensor_summary(view: CacheView, now: float, cfg: dict[str, Any],
                   tracker: SessionTracker | None = None) -> dict[str, Any]:
    """state/phase 의 sensor_summary — 화면 표시용 특징 요약. 없는 필드는 생략한다."""
    fresh = cfg["freshness_sec"]
    out: dict[str, Any] = {}
    mm = view.fresh("mmwave", now, fresh["mmwave"])
    if mm:
        out["present"] = bool(mm.data.get("present", False))
        out["mmwave"] = {
            k: mm.data[k]
            # 심박·호흡은 *_valid 와 짝으로 넘긴다. 락온이 풀린 구간의 값을 화면이 그대로
            # 그리면 "심박 0" 으로 읽히는데, 그건 사람이 없다는 뜻이 아니다(PR #24 d40a7a5).
            for k in ("motion_state", "motion_level", "distance_cm",
                      "resp_bpm", "resp_valid", "heart_bpm", "heart_valid", "drowsy_state")
            if k in mm.data
        }
    env = view.fresh("env", now, fresh["env"])
    if env:
        values = _environment_values(env.data)
        for key in values:
            out[key] = env.data[key]
        ecfg = cfg["environment"]
        start = tracker.co2_session_start if tracker is not None and tracker.session_started is not None else None
        scores = _environment_scores(values, ecfg, start)
        threshold = ecfg["env_flag_threshold"]
        flags: list[str] = []
        if scores.get("co2_abs", 0.0) >= threshold and "co2_ppm" in values:
            flags.append("co2_high")
        if start is not None and scores.get("co2_rise", 0.0) >= threshold and "co2_ppm" in values:
            flags.append("co2_rising")
        if ("temp_c" in values and scores["temp_dev"] >= threshold
                and (values["temp_c"] < ecfg["temp_comfort_min"]
                     or values["temp_c"] > ecfg["temp_comfort_max"])):
            flags.append("too_hot" if values["temp_c"] > ecfg["temp_comfort_max"] else "too_cold")
        if ("humidity_pct" in values and scores["humidity_dev"] >= threshold
                and (values["humidity_pct"] < ecfg["humidity_comfort_min"]
                     or values["humidity_pct"] > ecfg["humidity_comfort_max"])):
            flags.append("too_humid" if values["humidity_pct"] > ecfg["humidity_comfort_max"] else "too_dry")
        if scores.get("lux_dim", 0.0) >= threshold and "lux" in values:
            flags.append("too_dark")
        if flags:
            out["env_flags"] = flags
    ks = view.fresh("keystroke", now, fresh["keystroke"])
    if ks:
        out["keystroke"] = {"ts": ks.ts, **ks.data}
    out["valid"] = bool(mm or env or ks)
    return out
