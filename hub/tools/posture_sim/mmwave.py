"""mmWave(DFRobot SEN0623/C1001 60 GHz) 대역 — 재실 판정의 우선 센서. 1 Hz 로 0x20 mmwave_feature 를 만든다.

재실 판정(팀 결정 10-23, hub ingest/mapping.py `_decide_presence` 와 같은 규칙): mmWave 가 신선하면 mmWave 를 먼저 —
부재면 부재, 재실이면 거리(distance_cm ≤ desk_max_cm)로 "책상 재실"을 가린다. C1001 은 11 m·100°×100° 라 원룸의 침대·주방에 있는
사람도 재실로 잡는다(→ 먼 거리). 거리가 없으면 ToF 로, mmWave 가 끊기면 ToF 만. 호흡까지 잡아 가만히 조는 사람도 재실, 빈 의자·짐은 부재.
자리비움 구간의 `where`(room | out)로 사람이 방 안에 남았는지 정한다.

모의 특성(config/sensors.yaml `mmwave`, 전부 가정)
  enter_delay_s    사람이 앉은 뒤 present 가 켜지기까지
  leave_hold_s     자리를 뜬 뒤에도 present 를 유지하는 시간(C1001 무인 판정 지연)
  dropout_p        재실 중 1 초마다 짧게 놓칠 확률(dropout_s 동안 false)
  motion_level     구간 종류별 bodyMove 0~100 범위
  resp_bpm         호흡(졸음은 느림), 큰 움직임 중엔 resp_valid=false 가 섞인다
  drowsy_lock_s    졸음 구간이 이만큼 이어지면 DrowsyDetector 가 DROWSY 를 낼 수 있다(episode_p 확률, 에피소드마다 한 번 추첨)
"""
from __future__ import annotations

import random
from collections import deque
from typing import Any

DEFAULT_LEVEL = {"away": [0, 0], "upright": [12, 30], "mouse_work": [15, 30], "writing": [18, 35], "reading": [6, 15],
                 "turtle": [12, 28], "chin_rest": [5, 12], "recline": [5, 14], "drowsy": [2, 8], "face_down": [1, 6],
                 "lean_forward": [10, 25], "elbows": [8, 20]}


class FwDrowsyDetector:
    """펌웨어 firmware/esp32_sensor_node/src/sensors/c1001/DrowsyDetector.{h,cpp} 의 Python 이식(시뮬 전용, 상수·순서 그대로).
    주 신호 = 큰 체동 스파이크가 없는 정지. 보조 증거 = 호흡 소실 · 심박 중앙값(120 s)이 각성 기준선(τ 600 s)보다 4 bpm 이상
    낮은 상태가 30 s 연속. 증거 0/1/2 개면 정지 180/60/25 s 뒤 DROWSY — 심박이 안 떨어져도 정지만 3 분이면 DROWSY 다."""
    MOVE_TAU_S, QUIET_SPIKE, STILL_AFTER_SPIKE_S, QUIET_MAX, MOVE_SPIKE = 15.0, 30, 5.0, 15.0, 40
    HR_MIN, HR_MAX, HR_BLANK_S, HR_WIN_S, HR_WIN_N, HR_WIN_MIN_N = 40, 150, 60.0, 120.0, 64, 8
    HR_BASE_MIN_N, HR_BASE_TAU_S, HR_DROP_BPM, HR_DOWN_HOLD_S = 20, 600.0, 4.0, 30.0
    LOCK_MIN, LOCK_MAX, LOCK_DEBOUNCE = 30, 170, 3
    HOLD_S = {0: 180.0, 1: 60.0, 2: 25.0}
    AWAKE_HOLD_S, WAKE_SPIKE, WARMUP_S, RESP_NO_BREATH = 10.0, 40, 20.0, 4

    def __init__(self, now: float):
        self.start = now
        self.presence, self.dist, self.dist_in, self.dist_out, self.locked = 0, 0, 0, 0, False
        self.resp_rate, self.resp_state = 0, self.RESP_NO_BREATH
        self.move_avg, self.last_move, self.last_spike, self.blank_until = 0.0, now, now, now
        self.hr: deque[tuple[int, float]] = deque(maxlen=self.HR_WIN_N)
        self.hr_ever_valid, self.hr_base, self.last_base = False, 0.0, now
        self.resp_lost = self.hr_down = self.hr_down_armed = False
        self.hr_down_since = now
        self.prev_sign, self.sign_since, self.drowsy = False, now, False
        self.state = "NOPERSON"

    def on_distance(self, cm: int) -> None:
        self.dist = cm
        if self.LOCK_MIN <= cm <= self.LOCK_MAX:
            self.dist_out = 0
            self.dist_in = min(self.LOCK_DEBOUNCE, self.dist_in + 1)
            if self.dist_in >= self.LOCK_DEBOUNCE:
                self.locked = True
        else:
            self.dist_in = 0
            self.dist_out = min(self.LOCK_DEBOUNCE, self.dist_out + 1)
            if self.dist_out >= self.LOCK_DEBOUNCE:
                self.locked = False

    def on_body_move(self, v: int, t: float) -> None:
        dt = t - self.last_move
        self.last_move = t
        if dt <= 0.0:
            dt = 0.001
        self.move_avg += (dt / (self.MOVE_TAU_S + dt)) * (float(v) - self.move_avg)
        if v > self.QUIET_SPIKE:
            self.last_spike = t
        if v > self.WAKE_SPIKE:
            self.drowsy = False
        if v > self.MOVE_SPIKE:
            self.blank_until = t + self.HR_BLANK_S

    def samples(self, now: float) -> list[int]:
        return [v for v, t in self.hr if now - t <= self.HR_WIN_S]

    def median(self, now: float) -> int:
        s = sorted(self.samples(now))
        return 0 if len(s) < self.HR_WIN_MIN_N else s[len(s) // 2]

    def drop(self, now: float) -> float:
        med = self.median(now)
        return 0.0 if self.hr_base <= 0.0 or med == 0 else self.hr_base - float(med)

    def on_heart_rate(self, bpm: int, t: float) -> None:
        ok = self.HR_MIN <= bpm <= self.HR_MAX
        if ok:
            self.hr_ever_valid = True
        dist_ok = self.LOCK_MIN <= self.dist <= self.LOCK_MAX
        if not ok or self.blank_until > t or self.presence != 1 or not dist_ok:
            return
        self.hr.append((int(bpm), t))
        med = self.median(t)
        dt = t - self.last_base
        self.last_base = t
        if med == 0:
            return
        if self.hr_base == 0.0:
            if len(self.samples(t)) < self.HR_BASE_MIN_N:
                return
            self.hr_base = float(med)
        elif not self.drowsy:
            if dt <= 0.0:
                dt = 0.001
            self.hr_base += (dt / (self.HR_BASE_TAU_S + dt)) * (float(med) - self.hr_base)

    def tick(self, now: float) -> str:
        present = self.presence == 1
        self.resp_lost = present and self.locked and (self.resp_rate == 0 or self.resp_state == self.RESP_NO_BREATH)
        raw = self.median(now) > 0 and self.drop(now) >= self.HR_DROP_BPM
        if raw:
            if not self.hr_down_armed:
                self.hr_down_armed, self.hr_down_since = True, now
        else:
            self.hr_down_armed = False
        self.hr_down = self.hr_down_armed and now - self.hr_down_since >= self.HR_DOWN_HOLD_S
        still = now - self.last_spike >= self.STILL_AFTER_SPIKE_S and self.move_avg < self.QUIET_MAX
        sign = present and self.locked and still
        if sign != self.prev_sign:
            self.prev_sign, self.sign_since = sign, now
        held = now - self.sign_since
        if not self.drowsy and sign and held >= self.HOLD_S[int(self.resp_lost) + int(self.hr_down)]:
            self.drowsy = True
        elif self.drowsy and not sign and held >= self.AWAKE_HOLD_S:
            self.drowsy = False
        if not present:
            self.state = "NOPERSON"
        elif not self.locked:
            self.state = "NOLOCK"
        elif self.drowsy:
            self.state = "DROWSY"
        elif not self.hr_ever_valid and now - self.start < self.WARMUP_S:
            self.state = "WARMUP"
        else:
            self.state = "AWAKE"
        return self.state


class HeartModel:
    """(가정) 참 심박 + C1001 추정기 오차. 사람마다 안정 심박, 타이핑 중 +active, 졸음·엎드림 −drop(τ 로 서서히),
    깨어 가만히 있으면 relax 만큼 서서히 내려감(오탐 위험). 측정은 3 s 마다 참값 + AR(1) 잡음 + 가끔 큰 이탈(펌웨어 실측 86→126→81),
    3 bpm 계단 양자화. 숫자는 전부 config/sensors.yaml mmwave.hr_model."""

    def __init__(self, cfg: dict[str, Any], rnd: random.Random):
        self.c, self.r = cfg, rnd
        self.rest = self._u("rest_bpm", [60, 78])
        self.active = self._u("active_bpm", [3, 6])
        self.true = self.rest
        self.drop, self.relax = 0.0, self._u("relax_bpm", [0, 3])
        self.err, self.exc, self.exc_until = 0.0, 0.0, -1.0
        self.still_since: float | None = None
        self.prev_kind: str | None = None

    def _u(self, key: str, default: list[float]) -> float:
        lo, hi = self.c.get(key) or default
        return self.r.uniform(float(lo), float(hi))

    def step(self, now: float, dt: float, *, kind: str, typing: bool) -> None:
        sleepy = kind in ("drowsy", "face_down")
        if sleepy and self.prev_kind not in ("drowsy", "face_down"):
            self.drop = self._u("drowsy_drop_bpm", [3, 9])            # 졸음 에피소드마다 한 번
        self.prev_kind = kind
        if typing or sleepy or kind == "away":
            self.still_since = None
        elif self.still_since is None:
            self.still_since = now
        relax = 0.0 if self.still_since is None else self.relax * min(1.0, (now - self.still_since) / float(self.c.get("relax_tau_s", 300)))
        target = self.rest + (self.active if typing else 0.0) - (self.drop if sleepy else 0.0) - relax
        tau = float(self.c.get("drowsy_tau_s", 120)) if sleepy else float(self.c.get("tau_s", 60))
        self.true += (target - self.true) * min(1.0, dt / tau)

    def measure(self, now: float) -> int:
        phi, sd = float(self.c.get("meas_phi", 0.9)), float(self.c.get("meas_sd_bpm", 5.0))
        self.err = phi * self.err + (1.0 - phi * phi) ** 0.5 * sd * self.r.gauss(0.0, 1.0)
        if now >= self.exc_until:
            self.exc = 0.0
            if self.r.random() < float(self.c.get("excursion_p", 0.01)):
                self.exc = self._u("excursion_bpm", [10, 40]) * (1 if self.r.random() < 0.5 else -1)
                self.exc_until = now + self._u("excursion_s", [10, 90])
        return int(round((self.true + self.err + self.exc) / 3.0) * 3)


class MmwaveSensor:
    def __init__(self, cfg: dict[str, Any], *, seed: int):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", True))
        self.rnd = random.Random(seed + 41)
        hr_cfg = self.c.get("hr_model") or {}
        self.hr_on = bool(hr_cfg.get("enabled", False))                # (실험) 심박 모델 + 펌웨어 DrowsyDetector
        self.hr_rnd = random.Random(seed + 97)                           # 기존 난수 순서를 건드리지 않게 따로
        self.heart = HeartModel(hr_cfg, self.hr_rnd) if self.hr_on else None
        self.fw: FwDrowsyDetector | None = None
        self.next_spike = 0.0
        self.last_t: float | None = None
        self.last_hr: int | None = None
        self.present = False
        self.since_change = 0.0
        self.truth_prev: bool | None = None
        self.truth_since = 0.0
        self.drop_until = -1.0
        self.warm_since: float | None = None
        self.drowsy_since: float | None = None
        self.drowsy_lock: bool | None = None
        self.last: dict[str, Any] | None = None

    def _u(self, key: str, default: list[float]) -> float:
        lo, hi = self.c.get(key) or default
        return self.rnd.uniform(float(lo), float(hi))

    def step(self, now: float, *, kind: str, typing: bool, where: str = "desk") -> dict[str, Any] | None:
        """1 초마다. kind = 지금 사람의 구간 종류(정답), where = desk | room(방 안 다른 곳) | out. 0x20 계약 필드 dict."""
        if not self.enabled:
            return None
        c = self.c
        in_room = kind == "away" and where == "room"
        truth = kind != "away" or in_room
        if truth != self.truth_prev:
            self.truth_prev, self.truth_since = truth, now
        held = now - self.truth_since
        if truth:
            want = held >= float(c.get("enter_delay_s", 2.0))
            if want and now >= self.drop_until and self.rnd.random() < float(c.get("dropout_p", 0.0005)):
                self.drop_until = now + self._u("dropout_s", [2, 6])
            if now < self.drop_until:
                want = False
        else:
            want = self.present and held < float(c.get("leave_hold_s", 15.0))
        self.present = want
        level_rng = (c.get("room_motion_level") or [5, 40]) if in_room else (
            (c.get("motion_level") or {}).get(kind) or DEFAULT_LEVEL.get(kind, [10, 25]))
        level = int(round(self.rnd.uniform(*level_rng))) if self.present else 0
        if self.present and typing:
            level = max(level, int(round(self.rnd.uniform(15, 30))))
        if self.present and kind == "drowsy" and self.rnd.random() < float(c.get("nod_spike_p", 0.08)):
            level = int(round(self.rnd.uniform(20, 40)))      # 끄덕임·고개 들기 순간
        if self.hr_on and self.present:
            level = self._hr_spike(now, level, kind=kind, typing=typing)
        motion = "none" if not self.present else ("active" if level > int(c.get("active_level", 25)) else "still")
        resp_lo, resp_hi = (c.get("resp_bpm") or {}).get("drowsy" if kind in ("drowsy", "face_down") else "awake", [12, 18])
        resp_ok = self.present and not (motion == "active" and self.rnd.random() < float(c.get("resp_lost_when_active_p", 0.5)))
        if self.hr_on and self.present and kind == "face_down" and \
                self.hr_rnd.random() < float((c.get("hr_model") or {}).get("face_down_resp_lost_p", 0.8)):
            resp_ok = False                                   # 엎드려 가슴이 가려짐(펌웨어 보조 증거 1)
        # DrowsyDetector: 재실 뒤 warmup → AWAKE; 졸음 구간이 lock 시간 이어지면 에피소드마다 확률로 DROWSY
        if not self.present:
            self.warm_since, state = None, "NOPERSON"
        else:
            if self.warm_since is None:
                self.warm_since = now
            state = "WARMUP" if now - self.warm_since < float(c.get("warmup_s", 60)) else "AWAKE"
        if kind == "drowsy" and self.present:
            if self.drowsy_since is None:
                self.drowsy_since = now
                self.drowsy_lock = self.rnd.random() < float(c.get("drowsy_episode_p", 0.6))
            if self.drowsy_lock and now - self.drowsy_since >= float(c.get("drowsy_lock_s", 180)) and state == "AWAKE":
                state = "DROWSY"
        else:
            self.drowsy_since, self.drowsy_lock = None, None
        dist_cm = (int(round(self.rnd.uniform(*((c.get("room_distance_cm") or [200, 450]) if in_room
                                                 else (c.get("distance_cm") or [65, 95])))))
                   if self.present and c.get("report_distance", True) else None)
        extra: dict[str, Any] = {}
        if self.hr_on:
            state, extra = self._fw_step(now, kind=kind, typing=typing, level=level, dist_cm=dist_cm,
                                         resp_bpm=int(round(self.rnd.uniform(resp_lo, resp_hi))) if resp_ok else 0)
        self.last = {
            "present": self.present, "motion_state": motion, "motion_level": level,
            "distance_cm": dist_cm,
            "resp_bpm": int(round(self.rnd.uniform(resp_lo, resp_hi))) if resp_ok else None, "resp_valid": bool(resp_ok),
            "heart_bpm": None, "heart_valid": False,          # 심박은 쓰지 않는다(개발 원칙 4) — fsm drowsy guard 는 꺼진 채
            "drowsy_state": state, "valid": True,
        }
        if extra:
            self.last.update(extra)
        return self.last

    # ---------------------------------------------------------------- (실험) 심박 모델 + 펌웨어 DrowsyDetector
    def _hr_spike(self, now: float, level: int, *, kind: str, typing: bool) -> int:
        """펌웨어 정지 판정의 기준인 큰 체동 스파이크(> 30). 실측: 작업 중 10~30 s 마다, 눈 감고 앉음 88 s 동안 0 개.
        타이핑 · 독서(책장) · 그 밖 깨어 있는 자세(자세 고침) 마다 간격을 따로 뽑는다. 졸음은 기존 nod_spike_p 만."""
        hc = self.c.get("hr_model") or {}
        if kind in ("drowsy", "face_down", "away"):
            return level
        cls = "typing" if typing else ("reading" if kind == "reading" else "idle")
        if now >= self.next_spike:
            if self.next_spike > 0.0:
                lo, hi = (hc.get("spike_level") or {}).get("typing" if cls == "typing" else "other", [31, 60])
                level = max(level, int(round(self.hr_rnd.uniform(lo, hi))))
            lo, hi = (hc.get("spike_every_s") or {}).get(cls, {"typing": [10, 30], "reading": [60, 180]}.get(cls, [60, 240]))
            self.next_spike = now + self.hr_rnd.uniform(float(lo), float(hi))
        return level

    def _fw_step(self, now: float, *, kind: str, typing: bool, level: int, dist_cm: int | None,
                 resp_bpm: int) -> tuple[str, dict[str, Any]]:
        dt = 1.0 if self.last_t is None else max(0.0, now - self.last_t)
        self.last_t = now
        if self.fw is None:
            self.fw = FwDrowsyDetector(now)
        fw, sec = self.fw, int(now)
        fw.presence = 1 if self.present else 0
        self.heart.step(now, dt, kind=kind, typing=typing)
        if self.present:
            fw.on_body_move(level, now)                                   # 체동 1000 ms
            if sec % 2 == 0 and dist_cm is not None:
                fw.on_distance(dist_cm)                                   # 거리 2001 ms
            if sec % 3 == 0:
                fw.resp_rate, fw.resp_state = resp_bpm, (0 if resp_bpm else FwDrowsyDetector.RESP_NO_BREATH)
                self.last_hr = self.heart.measure(now)                    # 호흡·심박 3001 ms
                fw.on_heart_rate(self.last_hr, now)
        state = fw.tick(now)
        return state, {"heart_bpm": self.last_hr if self.present else None, "heart_valid": bool(self.present and self.last_hr),
                       # 아래는 0x20 에 없는 펌웨어 내부 값 — "증거 비트를 0x20 에 싣는다"는 제안을 시뮬에서만 흉내 낸다
                       "hr_down": bool(fw.hr_down), "hr_drop": round(fw.drop(now), 1), "hr_base": round(fw.hr_base, 1),
                       "fw_still": bool(fw.prev_sign), "resp_lost_ev": bool(fw.resp_lost), "hr_true": round(self.heart.true, 1)}


def presence_decision(mm: dict[str, Any] | None, tof_present: bool, *, desk_max_cm: float = 130.0,
                      priority: str = "mmwave") -> tuple[bool, str]:
    """책상 재실 판정 — hub `_decide_presence` 와 같은 규칙. 돌려주는 값: (재실, 근거)."""
    if priority != "mmwave":
        return bool(tof_present) or bool(mm and mm.get("present")), "any"
    if mm is not None and mm.get("valid", True):
        if not mm.get("present"):
            return False, "mmwave"
        if mm.get("distance_cm") is not None:
            near = float(mm["distance_cm"]) <= desk_max_cm
            return near, "mmwave" if near else "mmwave_far"
        return bool(tof_present), "mmwave+tof"
    return bool(tof_present), "tof"
