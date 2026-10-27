"""센서 표본 → SensorFrame 변환 (순수 함수).

MVP(2026-09-18) 임시 스케일이다. 모든 숫자는 config/ingest.yaml 에서 오고,
baseline 정규화(features/)가 들어오면 이 선형 매핑을 대체한다.
브로커·스레드 없이 테스트할 수 있도록 상태는 SessionTracker 에만 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    elapsed_reset_on_rest: bool = False     # ingest.yaml elapsed.reset_on_rest: 휴식(REST)을 마치면 경과 시간을 그때부터 다시 잰다
    sd_still_since: float | None = None     # (실험) mmwave.still_drowsy_and: ToF 정지가 시작된 시각
    sd_debug: dict[str, Any] | None = None  # 이번 tick 의 규칙 판정(로그용)
    sd_hr_recent: list[tuple[float, float]] = field(default_factory=list)   # (실험) hr_down_presence_base: 최근 심박 (t, bpm)
    sd_hr_base: list[float] = field(default_factory=list)                   # 깨어 움직일 때 모은 심박(기준선 표본)
    sd_down_since: float | None = None
    lr_hr_recent: list[tuple[float, float]] = field(default_factory=list)   # 반응 학습 입력: 최근 심박 (t, bpm)
    lr_hr_base: list[float] = field(default_factory=list)                   # 깨어 움직일 때 심박(재실 기준선 표본)
    # 반응 학습: 피로 기록 앱의 마지막 KSS 응답 (kss, 응답 시각). live.py 가 deskmate/fatigue/label 큐(cache.fatigue_labels)에서
    # 채운다 — 같은 토픽을 피로 점수(fatigue/live.py)도 읽어서 cache 슬롯을 따로 두지 않는다. FSM 규칙은 안 쓴다
    self_kss: tuple[float, float] | None = None

    def observe_state(self, state: State, now: float) -> None:
        if state is State.START and self.session_started is None:
            self.session_started = now
        if (self.elapsed_reset_on_rest and self.last_state is State.REST and state is not State.REST
                and self.session_started is not None):
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
    # 라벨별 신뢰도 하한(tof.min_confidence_by_label, 기본 없음): 시연 프로파일은 face_down 0.99 — 깨어 글 쓰는 사람을 GRU 가
    # face_down 으로 읽는 오판(중앙값 0.87)과 실제 엎드림(p10 1.00)을 신뢰도로 가른다(PC 시뮬 10-25). 걸러지면 unknown 으로 둔다.
    floor = float((cfg.get("min_confidence_by_label") or {}).get(posture, 0.0))
    filtered = False
    if confidence < max(float(cfg["min_confidence"]), floor):
        filtered = posture != "unknown"
        posture = "unknown"
    phi_table = cfg["posture_phi"]
    delta_table = cfg["posture_delta"]
    motion = _clip(float(data.get("motion_score") or 0.0))
    phi = max(float(phi_table.get(posture, 0.0)), motion * float(cfg["motion_phi_weight"]))
    nod = data.get("nod_rate_hz")
    nod_delta = _ramp(nod, 0.0, float(cfg["nod_rate_full_hz"])) if nod is not None else 0.0
    nod_delta = min(nod_delta, float(cfg.get("nod_delta_max", 1.0)))        # 0x03 끄덕임 증거 상한(시연 프로파일 0.3)
    if data.get("drowsy_model") and not filtered:
        flag_delta = 1.0                                                       # 자세 모델(서비스)이 drowsy 라고 했다 — 라벨 증거와 같은 힘(신뢰도 하한을 넘었을 때만)
    elif data.get("drowsy_rule", data.get("drowsy")):
        flag_delta = float(cfg.get("rule_drowsy_delta", 1.0))                 # ESP32 0x03 규칙 플래그만 — 상한(시연 프로파일 0.3)
    else:
        flag_delta = 0.0
    delta = max(float(delta_table.get(posture, 0.0)), nod_delta, flag_delta)
    return Signal(phi=_clip(phi), delta=_clip(delta), available=True)


def _service_sample(view: CacheView, now: float, cfg: dict[str, Any]) -> Sample | None:
    """`tof.posture_source: service` 일 때 쓸 만한 별도 자세 서비스(deskmate_tofposture, GRU) 판정. 없거나 낡았거나
    valid 가 아니거나 살아 있는 센서가 `tof.service.min_online_sensors` 보다 적으면 None(→ fallback)."""
    tcfg = cfg.get("tof") or {}
    if str(tcfg.get("posture_source", "mlp")) != "service":
        return None
    fresh = cfg["freshness_sec"]
    svc = view.fresh("tof_service", now, fresh.get("tof_service", fresh.get("tof", 3)))
    if svc is None or not svc.data.get("valid", False):
        return None
    scfg = (tcfg.get("service") or {})
    if int(svc.data.get("online_sensors") or 0) < int(scfg.get("min_online_sensors", 1)):
        return None
    return _mask_seated_away(svc, view, now, cfg)


_NOT_SEATED = ("away", "misplaced")
_GRID_VALID_STATUS = (5, 9, 6, 10)   # tof_mlp 와 같은 유효(5·9)+준유효(6·10)


def _bottom_row_seated(view: CacheView, now: float, cfg: dict[str, Any], max_mm: float) -> bool:
    """최신 0x05 격자에서 어느 센서든 가장 아랫줄(행 h-1)에 max_mm 보다 가까운 유효 칸이 있으면 True. 격자가 없거나 낡으면 False."""
    fresh = cfg["freshness_sec"]
    grid = view.fresh("tof_grid", now, fresh.get("tof_grid", fresh.get("tof", 3)))
    if grid is None:
        return False
    w, h = int(grid.data.get("grid_width") or 8), int(grid.data.get("grid_height") or 8)
    for sensor in (grid.data.get("sensors") or {}).values():
        dist, status = list(sensor.get("distance_mm") or []), list(sensor.get("status") or [])
        for k in range((h - 1) * w, min(h * w, len(dist))):
            st = status[k] if k < len(status) else None
            if 0 < dist[k] < max_mm and (st is None or st in _GRID_VALID_STATUS):
                return True
    return False


def _mask_seated_away(svc: Sample, view: CacheView, now: float, cfg: dict[str, Any]) -> Sample:
    """`tof.service.away_bottom_row_mm` 이 있고 격자 가장 아랫줄에 그보다 가까운 칸이 있으면(앉아 있다) 서비스 away 를
    away·misplaced 를 뺀 확률 1위 자세로 바꾼다. 신뢰도는 그 라벨의 원래 확률 그대로라 대개 `min_confidence` 아래 → FSM 에는
    away 처럼 증거 0 이고(다시 나눠 키우면 약한 drowsy 가 시연 하한을 넘어 무드등이 헛켜질 수 있다) 화면 자세 칸만 away 가 아닌
    라벨이 된다. 재실(presence.source)은 건드리지 않는다.
    바꾼 표본은 present True·drowsy False·away_masked True·service_posture(원래 라벨)."""
    d = svc.data
    max_mm = float(((cfg.get("tof") or {}).get("service") or {}).get("away_bottom_row_mm") or 0)
    if d.get("posture") != "away" or max_mm <= 0 or not _bottom_row_seated(view, now, cfg, max_mm):
        return svc
    probs = {k: float(v) for k, v in (d.get("probs") or {}).items() if k not in _NOT_SEATED}
    if not probs:
        return svc
    label = max(probs, key=probs.__getitem__)
    return Sample(received=svc.received, ts=svc.ts, seq=svc.seq, data=dict(
        d, posture=label, posture_confidence=round(probs[label], 4), present=True,
        drowsy=False, away_masked=True, service_posture="away"))    # 대신 보여 주는 라벨일 뿐 — 졸음 플래그로 새지 않게


def _tof_sample(view: CacheView, now: float, cfg: dict[str, Any]) -> Sample | None:
    """posture 의 바탕이 되는 ToF 표본. 0x03 규칙 특징이 신선하면 그것, 없고 자세 서비스 판정만 신선하면 그 판정으로
    0x03 모양을 만든다(ESP32 가 0x05 만 보내거나 0x03 이 끊겨도 서비스 자세는 FSM 에 닿게)."""
    fresh = cfg["freshness_sec"]
    tof = view.fresh("tof", now, fresh.get("tof", 3))
    if tof is not None:
        return tof
    svc = _service_sample(view, now, cfg)
    if svc is None:
        return None
    d = svc.data
    return Sample(received=svc.received, ts=svc.ts, seq=None, data={
        "present": bool(d.get("present", False)), "posture": d.get("posture", "unknown"),
        "posture_confidence": float(d.get("posture_confidence") or 0.0), "posture_source": "service",
        "drowsy": bool(d.get("drowsy", False)), "baseline_ready": True, "sensors_ok": int(d.get("online_sensors") or 0),
        "from_service": True,
    })


def _tof_with_model(view: CacheView, tof_data: dict[str, Any], now: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """0x03 규칙 특징 위에 Pi 4 자세 모델 판정의 posture·confidence 를 덮어쓴다.

    `tof.posture_source`:
      service — 별도 서비스 deskmate_tofposture(GRU, /api/state → `tof_service` 표본, 2026-10-23). 표본이 신선하고
                valid 이고 센서가 살아 있을 때. 라벨이 drowsy 면 `drowsy` 도 켠다(졸음 증거는 posture_delta 1.0 과 0x03
                플래그 자리 둘 다). 쓸 수 없으면 `tof.service.fallback`(mlp 기본 | rule)로 내려간다.
      mlp     — hub 안 순수 Python MLP(tof_mlp, 10-17). 표본이 신선하며 기준선이 잡혀 있을 때만.
      rule    — ESP32 0x03 규칙 판정 그대로.
    motion_score·nod_rate 는 모델 밖이라 언제나 규칙 값이다.
    """
    tcfg = cfg.get("tof") or {}
    source = str(tcfg.get("posture_source", "mlp"))
    if source == "service":
        svc = _service_sample(view, now, cfg)
        if svc is not None and svc.data.get("posture") not in (None, "unknown"):
            return dict(tof_data, posture=str(svc.data["posture"]),
                        posture_confidence=float(svc.data.get("posture_confidence") or 0.0),
                        drowsy=bool(tof_data.get("drowsy")) or bool(svc.data.get("drowsy")),
                        drowsy_model=bool(svc.data.get("drowsy")),      # 모델 라벨 drowsy 는 상한 없이 1.0(_tof_posture_signal)
                        drowsy_rule=bool(tof_data.get("drowsy")),       # 0x03 규칙 플래그만 따로 — 모델 drowsy 가 걸러져도 규칙 경로로 새지 않게
                        misplaced=bool(svc.data.get("misplaced", False)), posture_source="service",
                        **({"away_masked": True} if svc.data.get("away_masked") else {}))
        source = str((tcfg.get("service") or {}).get("fallback", "mlp"))
    if source != "mlp":
        return dict(tof_data, posture_source="rule")
    fresh = cfg["freshness_sec"]
    mlp = view.fresh("tof_mlp", now, fresh.get("tof_mlp", fresh.get("tof", 3)))
    if not mlp or not mlp.data.get("baseline_ready") or mlp.data.get("posture") in (None, "unknown"):
        return dict(tof_data, posture_source="rule")
    return dict(tof_data, posture=str(mlp.data["posture"]),
                posture_confidence=float(mlp.data.get("posture_confidence") or 0.0), posture_source="mlp")


_tof_with_mlp = _tof_with_model     # 10-17 이름(테스트·외부 참조 호환)


def _usable_mmwave(mm: Sample | None) -> Sample | None:
    """C1001 표본이 `valid: false`(락온 전·통신 오류)면 없는 것으로 본다 — 재실은 ToF 가 대신한다."""
    return mm if mm is not None and mm.data.get("valid", True) is not False else None


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
    mm = _usable_mmwave(mm)
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
        mm = _usable_mmwave(cache.snapshot().fresh("mmwave", now, fresh))
        return None if mm is None else _mmwave_at_desk(mm, pcfg)

    return hint


def pc_ratio(view: CacheView, now: float, window_sec: float) -> float:
    ticks = [t for t in view.keystroke_history if now - t.received <= window_sec]
    if not ticks:
        return 0.0
    return sum(1 for t in ticks if t.input_active) / len(ticks)


def _still_drowsy_and(signals: dict[str, Signal], view: CacheView, mm: Sample, drowsy: str, tof_posture_used: bool,
                      now: float, cfg: dict[str, Any], tracker: SessionTracker) -> None:
    """(실험, 기본 꺼짐) ToF 로 가만히 있는데 mmWave 졸음 증거가 있으면 자세 delta 를 1.0 으로 — mmwave.still_drowsy_and.
    지금은 ToF 가 살아 있으면 mmWave 졸음 판정을 안 쓴다(ToF 가 끊겼을 때만 대체). 팀 제안(10-23): 둘을 AND 로 같이 쓴다.
      mode drowsy_state — 펌웨어 DrowsyDetector 의 DROWSY(지금 0x20 계약). 정지만 3 분이어도 DROWSY 라 깨어 있는 정지와 못 가린다.
      mode hr_down      — 펌웨어의 '심박 하락' 증거(중앙값 120 s 가 기준선보다 4 bpm↓ 30 s). 0x20 에 아직 없는 필드(확장 제안).
      ToF 정지 = 자세 판정 표본의 격자 움직임 tof_motion_mm ≤ tof_motion_max_mm 가 tof_still_s 이어짐(tof_still: none 이면 조건 없음)."""
    sd = cfg["mmwave"].get("still_drowsy_and") or {}
    if not sd.get("enabled") or not tof_posture_used:
        tracker.sd_still_since, tracker.sd_debug = None, None
        return
    fresh = cfg["freshness_sec"]
    mlp = view.fresh("tof_mlp", now, fresh.get("tof_mlp", fresh.get("tof", 3)))
    motion = mlp.data.get("tof_motion_mm") if mlp else None
    if str(sd.get("tof_still", "motion")) == "none":
        still_now = True
    else:
        still_now = motion is not None and float(motion) <= float(sd.get("tof_motion_max_mm", 8.0))
    if still_now:
        if tracker.sd_still_since is None:
            tracker.sd_still_since = now
    else:
        tracker.sd_still_since = None
    tof_still = tracker.sd_still_since is not None and now - tracker.sd_still_since >= float(sd.get("tof_still_s", 60))
    mode = str(sd.get("mode", "hr_down"))
    hr_dbg: dict[str, Any] = {}
    if mode == "drowsy_state":
        evidence = drowsy == "DROWSY"
    elif mode == "hr_down_presence_base":
        ks = view.fresh("keystroke", now, fresh["keystroke"])
        moving = (bool(ks and ks.data.get("typing_active")) or not still_now or tracker.still_since is None)
        evidence, hr_dbg = _hr_presence_drop(mm, moving, now, sd, tracker)
    else:
        evidence = bool(mm.data.get("hr_down"))
    active = tof_still and evidence
    if active:
        p = signals["posture"]
        signals["posture"] = Signal(phi=p.phi, delta=1.0, available=True)
    tracker.sd_debug = {"mode": mode, "tof_motion_mm": motion, "tof_still": tof_still, "evidence": evidence, "active": active,
                        **hr_dbg}


def _hr_presence_drop(mm: Sample, moving: bool, now: float, sd: dict[str, Any],
                      tracker: SessionTracker) -> tuple[bool, dict[str, Any]]:
    """(실험) mode hr_down_presence_base — 사용자 설계 입력(10-23): "재실 판단 시 심박수와 졸음 심박수는 차이가 있을 것".
    기준선 = 책상 재실 ∧ 깨어 움직일 때(타이핑 중 · mmWave 체동 · ToF 움직임)의 심박만 모은 중앙값(최근 base_n 개). 가만히 있는 동안엔
    기준선을 얼린다(펌웨어는 졸음 전이면 τ 600 s 로 계속 따라가서, 오래 가만히 있으면 하락이 기준선에 묻힌다).
    지금 = 최근 hr_win_s 심박 중앙값. 기준선 − 지금 ≥ drop_bpm 이 down_hold_s 이어지면 증거. 0x20 의 heart_bpm 만 쓰므로 펌웨어 변경이 없다."""
    bpm = mm.data.get("heart_bpm")
    ok = bool(mm.data.get("heart_valid")) and bpm is not None and 40 <= float(bpm) <= 150
    if ok:
        tracker.sd_hr_recent.append((now, float(bpm)))
        if moving:
            tracker.sd_hr_base.append(float(bpm))
            del tracker.sd_hr_base[:-int(sd.get("base_n", 60))]
    tracker.sd_hr_recent = [(t, b) for t, b in tracker.sd_hr_recent if now - t <= float(sd.get("hr_win_s", 120))]
    base = _median(tracker.sd_hr_base) if len(tracker.sd_hr_base) >= int(sd.get("base_min_n", 20)) else None
    cur = _median([b for _, b in tracker.sd_hr_recent]) if len(tracker.sd_hr_recent) >= int(sd.get("hr_min_n", 8)) else None
    drop = None if base is None or cur is None else base - cur
    if drop is not None and drop >= float(sd.get("drop_bpm", 4.0)):
        if tracker.sd_down_since is None:
            tracker.sd_down_since = now
    else:
        tracker.sd_down_since = None
    evidence = tracker.sd_down_since is not None and now - tracker.sd_down_since >= float(sd.get("down_hold_s", 30))
    return evidence, {"hr_base": base, "hr_cur": cur, "hr_drop": None if drop is None else round(drop, 1), "moving": moving}


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2]


def _hr_drop_feature(mm: Sample, moving: bool, now: float, lc: dict[str, Any], tracker: SessionTracker) -> float | None:
    """반응 학습 입력(FSM 규칙 아님): 재실 기준 심박 − 지금 심박(bpm). 기준선 = 책상 재실 ∧ 깨어 움직일 때(타이핑·mmWave 체동)
    심박의 최근 base_n 표본 중앙값(가만히 있으면 얼림), 지금 = 최근 hr_win_s 중앙값. 10-23 시뮬에서 이 방식(Bp)이 조용한 졸음을
    4/4 잡고 깨어 읽기 오탐 0(시뮬 근거, 실측 전). 표본이 모자라면 None. (9b0720b 이식)"""
    bpm = mm.data.get("heart_bpm")
    if bool(mm.data.get("heart_valid")) and bpm is not None and 40 <= float(bpm) <= 150:
        tracker.lr_hr_recent.append((now, float(bpm)))
        if moving:
            tracker.lr_hr_base.append(float(bpm))
            del tracker.lr_hr_base[:-int(lc.get("base_n", 60))]
    tracker.lr_hr_recent = [(t, b) for t, b in tracker.lr_hr_recent if now - t <= float(lc.get("hr_win_s", 120))]
    if len(tracker.lr_hr_base) < int(lc.get("base_min_n", 20)) or len(tracker.lr_hr_recent) < int(lc.get("hr_min_n", 8)):
        return None
    return _median(tracker.lr_hr_base) - _median([b for _, b in tracker.lr_hr_recent])


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
    tof = _tof_sample(view, now, cfg) if "tof" in cfg else None

    mm_present = bool(mm.data.get("present", False)) if mm else False
    tof_present = bool(tof.data.get("present", False)) if tof else False
    present, _ = resolve_presence(mm, tof, cfg)
    pcfg = cfg.get("presence") or {}
    # mmWave 가 책상에 사람이 없다고 하면 ToF 가 무엇을 보든 자세로 쓰지 않는다(빈 의자·지나가는 사람·방 안 다른 자리)
    tof_gated = bool(pcfg.get("gate_posture", True)) and _usable_mmwave(mm) is not None and not present
    signals: dict[str, Signal] = {}

    # ---- posture: ToF tof_feature 가 신선하면 그것을, 아니면 mmWave 체동으로 대체 ----
    mcfg = cfg["mmwave"]
    tof_posture_used = False
    if tof and tof_present and not tof_gated:
        signals["posture"] = _tof_posture_signal(_tof_with_model(view, tof.data, now, cfg), cfg["tof"])
        tof_posture_used = True
        tracker.still_since = None
    hr_drop: float | None = None                   # 반응 학습 입력(아래 mm 블록에서)
    mm_motion: float | None = None
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
        _still_drowsy_and(signals, view, mm, drowsy, tof_posture_used, now, cfg, tracker)
        ks_now = view.fresh("keystroke", now, fresh["keystroke"])
        moving = bool(ks_now and ks_now.data.get("typing_active")) or tracker.still_since is None
        hr_drop = _hr_drop_feature(mm, moving, now, cfg.get("learning_inputs") or {}, tracker)
        mm_motion = level
    else:
        tracker.still_since = None
        tracker.resp_lost_since = None
        tracker.sd_still_since, tracker.sd_debug = None, None
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

    sr = cfg.get("self_report") or {}
    kss = tracker.self_kss
    self_kss = kss[0] if kss is not None and now - kss[1] <= float(sr.get("max_age_sec", 900)) else None
    return SensorFrame(
        now=now,
        present=present,
        pc_ratio=pc_ratio(view, now, float(cfg["pc_ratio_window_sec"])),
        signals=signals,
        touch=touch,
        action_done=action_done,
        break_accepted=break_accepted,
        self_kss=self_kss,
        hr_drop_bpm=None if hr_drop is None else round(float(hr_drop), 2),
        mm_motion=mm_motion,
    )


def sensor_summary(view: CacheView, now: float, cfg: dict[str, Any],
                   tracker: SessionTracker | None = None) -> dict[str, Any]:
    """state/phase 의 sensor_summary — 화면 표시용 특징 요약. 없는 필드는 생략한다."""
    fresh = cfg["freshness_sec"]
    out: dict[str, Any] = {}
    tof = _tof_sample(view, now, cfg) if "tof" in cfg else None
    if tof:
        # 화면용 요약. 원본 zone 격자(UART 0x05)는 Pi 4 메모리에만 두고 여기 싣지 않는다(firmware README "원칙").
        data = _tof_with_model(view, tof.data, now, cfg)
        out["tof"] = {
            k: data[k]
            for k in ("posture", "posture_confidence", "posture_source", "motion_score", "head_delta_mm",
                      "nod_rate_hz", "drowsy", "misplaced", "nods_in_window", "baseline_ready", "grid_width",
                      "grid_height", "coverage_ratio", "sensors_ok", "away_masked")
            if k in data
        }
    svc = view.fresh("tof_service", now, fresh.get("tof_service", fresh.get("tof", 3))) if "tof" in cfg else None
    if svc:
        # 별도 자세 서비스(GRU) 의 최근 판정 — posture_source 가 service 가 아니어도 화면·실기 로그용으로 보여 준다.
        # 아랫줄에 사람이 있는데 away 면 자세 칸과 같은 라벨로(_mask_seated_away) — 화면의 두 자세 줄이 다른 말을 하지 않게
        svc = _mask_seated_away(svc, view, now, cfg)
        out["tof_service"] = {
            k: svc.data[k]
            for k in ("posture", "posture_confidence", "valid", "present", "misplaced", "online_sensors", "since_ms",
                      "presence_source", "away_masked", "service_posture")
            if k in svc.data
        }
        if isinstance(svc.data.get("model"), dict) and svc.data["model"].get("file"):
            out["tof_service"]["model"] = str(svc.data["model"]["file"]).rsplit("/", 1)[-1]
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
