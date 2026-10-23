"""센서 표본 → SensorFrame 변환 (순수 함수).

MVP(2026-09-18) 임시 스케일이다. 모든 숫자는 config/ingest.yaml 에서 오고,
baseline 정규화(features/)가 들어오면 이 선형 매핑을 대체한다.
브로커·스레드 없이 테스트할 수 있도록 상태는 SessionTracker 에만 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..features import BaselineStore
from ..inference import SensorFrame, Signal, State
from .cache import CacheView, Sample


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


def _tof_posture_signal(data: dict[str, Any], cfg: dict[str, Any]) -> Signal:
    """ToF tof_feature → posture Signal. 숫자는 전부 config/ingest.yaml `tof` 에서 온다(실측 전 잠정)."""
    posture = str(data.get("posture") or "unknown")
    confidence = float(data.get("posture_confidence") or 0.0)
    if confidence < float(cfg["min_confidence"]):
        posture = "unknown"
    phi_table = cfg["posture_phi"]
    delta_table = cfg["posture_delta"]
    motion = _clip(float(data.get("motion_score") or 0.0))
    phi = max(float(phi_table.get(posture, 0.0)), motion * float(cfg["motion_phi_weight"]))
    nod = data.get("nod_rate_hz")
    nod_delta = _ramp(nod, 0.0, float(cfg["nod_rate_full_hz"])) if nod is not None else 0.0
    delta = max(float(delta_table.get(posture, 0.0)), nod_delta, 1.0 if data.get("drowsy") else 0.0)
    return Signal(phi=_clip(phi), delta=_clip(delta), available=True)


def _tof_with_mlp(view: CacheView, tof_data: dict[str, Any], now: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """0x03 규칙 특징 위에 Pi 4 MLP 판정(tof_mlp, 2026-10-17)의 posture·confidence 를 덮어쓴다.

    `tof.posture_source: mlp`(기본)이고 tof_mlp 표본이 신선하며 기준선이 잡혀 있을 때만. 아니면 규칙 판정 그대로
    (가중치 파일이 없어도 허브는 돈다). drowsy·nod_rate 는 모델 밖이라 언제나 규칙 값이다.
    """
    tcfg = cfg.get("tof") or {}
    if str(tcfg.get("posture_source", "mlp")) != "mlp":
        return dict(tof_data, posture_source="rule")
    fresh = cfg["freshness_sec"]
    mlp = view.fresh("tof_mlp", now, fresh.get("tof_mlp", fresh.get("tof", 3)))
    if not mlp or not mlp.data.get("baseline_ready") or mlp.data.get("posture") in (None, "unknown"):
        return dict(tof_data, posture_source="rule")
    return dict(tof_data, posture=str(mlp.data["posture"]),
                posture_confidence=float(mlp.data.get("posture_confidence") or 0.0), posture_source="mlp")


def _mmwave_at_desk(mm: Sample, pcfg: dict[str, Any]) -> bool | None:
    """mmWave 표본 하나 → 책상에 사람이 있는가. present=false 면 False, 거리 게이트(`presence.desk_max_cm`) 안이면
    True, 밖이면 False(방엔 있지만 책상은 빔 — C1001 은 11 m 를 봐서 원룸의 침대·주방도 잡는다). 게이트가 없으면 present
    그대로, 거리가 비어 있으면 None(모름 — ToF 에 맡긴다)."""
    if not bool(mm.data.get("present", False)):
        return False
    desk_max = pcfg.get("desk_max_cm")
    if desk_max is None:
        return True
    dist = mm.data.get("distance_cm")
    if dist is None:
        return None
    return float(dist) <= float(desk_max)


def resolve_presence(mm: Sample | None, tof: Sample | None, cfg: dict[str, Any]) -> tuple[bool, str]:
    """(책상 재실, 근거). `presence.source: mmwave`(기본)면 신선한 mmWave 가 정한다 — 없음 → 부재, 있음 + 거리 ≤
    desk_max_cm → 재실(엎드려 ToF 가 놓쳐도), 있음 + 더 멀다 → 부재(ToF 가 빈 의자를 사람으로 봐도). 거리가 없거나
    mmWave 가 끊겼을 때만 ToF present 로 대신한다. `either` 면 둘 중 하나라도 보면 재실(10-22 까지).
    근거: mmwave | mmwave_room(방엔 있음·책상 빔) | tof | either | none."""
    pcfg = cfg.get("presence") or {}
    mm_present = bool(mm.data.get("present", False)) if mm else False
    tof_present = bool(tof.data.get("present", False)) if tof else False
    if str(pcfg.get("source", "mmwave")) == "either":
        return mm_present or tof_present, "either"
    if mm is not None:
        at_desk = _mmwave_at_desk(mm, pcfg)
        if at_desk is not None:
            return at_desk, "mmwave_room" if mm_present and not at_desk else "mmwave"
    if tof is not None:
        return tof_present, "tof"
    if mm is not None:                      # 거리 없음 + ToF 없음 → mmWave present 그대로
        return mm_present, "mmwave"
    return False, "none"


def mmwave_presence_hint(cache: Any, cfg: dict[str, Any]) -> Callable[[float], bool | None]:
    """Pi 4 자세 모델 앞의 재실 게이트. now → mmWave 가 본 책상 재실(거리 게이트 포함), 표본이 없거나 낡았거나 거리가
    비어 있으면 None(게이트 안 함 — 격자로 판단)."""
    fresh = float(cfg["freshness_sec"]["mmwave"])
    pcfg = cfg.get("presence") or {}
    gate = bool(pcfg.get("gate_posture", True))

    def hint(now: float) -> bool | None:
        if not gate:
            return None
        mm = cache.snapshot().fresh("mmwave", now, fresh)
        return None if mm is None else _mmwave_at_desk(mm, pcfg)

    return hint


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
    tof = view.fresh("tof", now, fresh.get("tof", 3)) if "tof" in cfg else None

    mm_present = bool(mm.data.get("present", False)) if mm else False
    tof_present = bool(tof.data.get("present", False)) if tof else False
    present, _ = resolve_presence(mm, tof, cfg)
    pcfg = cfg.get("presence") or {}
    # mmWave 가 책상에 사람이 없다고 하면 ToF 가 무엇을 보든 자세로 쓰지 않는다(빈 의자·지나가는 사람·방 안 다른 자리)
    tof_gated = bool(pcfg.get("gate_posture", True)) and mm is not None and not present
    signals: dict[str, Signal] = {}

    # ---- posture: ToF tof_feature 가 신선하면 그것을, 아니면 mmWave 체동으로 대체 ----
    mcfg = cfg["mmwave"]
    tof_posture_used = False
    if tof and tof_present and not tof_gated:
        signals["posture"] = _tof_posture_signal(_tof_with_mlp(view, tof.data, now, cfg), cfg["tof"])
        tof_posture_used = True
        tracker.still_since = None
    if mm and mm_present and present:       # 책상이 비면 mmWave 체동·호흡 대체 신호도 쓰지 않는다(침대에서 자는 사람)
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
        if not tof_posture_used:
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
        if not tof_posture_used:
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
    tof = view.fresh("tof", now, fresh.get("tof", 3)) if "tof" in cfg else None
    if tof:
        # 화면용 요약. 원본 zone 격자(UART 0x05)는 Pi 4 메모리에만 두고 여기 싣지 않는다(firmware README "원칙").
        data = _tof_with_mlp(view, tof.data, now, cfg)
        out["tof"] = {
            k: data[k]
            for k in ("posture", "posture_confidence", "posture_source", "motion_score", "head_delta_mm",
                      "nod_rate_hz", "drowsy", "nods_in_window", "baseline_ready", "grid_width", "grid_height",
                      "coverage_ratio", "sensors_ok")
            if k in data
        }
    mm = view.fresh("mmwave", now, fresh["mmwave"])
    if mm or tof:
        # 재실은 build_frame 과 같은 규칙(presence.source) — 화면 배지와 FSM 이 다른 말을 하지 않게
        out["present"], out["presence_source"] = resolve_presence(mm, tof, cfg)
        if mm:
            out["in_room"] = bool(mm.data.get("present", False))       # 책상은 비어도 방엔 있음(화면 표시용)
    if mm:
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
