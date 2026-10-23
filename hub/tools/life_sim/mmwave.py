"""mmWave(DFRobot SEN0623/C1001 60 GHz) 대역 — 재실 판정의 우선 센서. 1 Hz 로 0x20 mmwave_feature 를 만든다.

재실 판정(팀 결정 10-23, hub ingest/mapping.py `_decide_presence` 와 같은 규칙): mmWave 가 신선하면 mmWave 를 먼저 —
부재면 부재, 재실이면 거리(distance_cm ≤ desk_max_cm)로 "책상 재실"을 가린다. C1001 은 11 m·100°×100° 라 원룸의 침대·주방에 있는
사람도 재실로 잡는다(→ 먼 거리). 거리가 없으면 ToF 로, mmWave 가 끊기면 ToF 만. 호흡까지 잡아 가만히 조는 사람도 재실, 빈 의자·짐은 부재.
자리비움 구간의 `where`(room | out)로 사람이 방 안에 남았는지 정한다.

모의 특성(config/devices.yaml `mmwave`, 전부 가정)
  enter_delay_s    사람이 앉은 뒤 present 가 켜지기까지
  leave_hold_s     자리를 뜬 뒤에도 present 를 유지하는 시간(C1001 무인 판정 지연)
  dropout_p        재실 중 1 초마다 짧게 놓칠 확률(dropout_s 동안 false)
  motion_level     구간 종류별 bodyMove 0~100 범위
  resp_bpm         호흡(졸음은 느림), 큰 움직임 중엔 resp_valid=false 가 섞인다
  drowsy_lock_s    졸음 구간이 이만큼 이어지면 DrowsyDetector 가 DROWSY 를 낼 수 있다(episode_p 확률, 에피소드마다 한 번 추첨)
"""
from __future__ import annotations

import random
from typing import Any

DEFAULT_LEVEL = {"away": [0, 0], "upright": [12, 30], "mouse_work": [15, 30], "writing": [18, 35], "reading": [6, 15],
                 "turtle": [12, 28], "chin_rest": [5, 12], "recline": [5, 14], "drowsy": [2, 8], "face_down": [1, 6],
                 "lean_forward": [10, 25], "elbows": [8, 20]}


class MmwaveSensor:
    def __init__(self, cfg: dict[str, Any], *, seed: int):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", True))
        self.rnd = random.Random(seed + 41)
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
        motion = "none" if not self.present else ("active" if level > int(c.get("active_level", 25)) else "still")
        resp_lo, resp_hi = (c.get("resp_bpm") or {}).get("drowsy" if kind in ("drowsy", "face_down") else "awake", [12, 18])
        resp_ok = self.present and not (motion == "active" and self.rnd.random() < float(c.get("resp_lost_when_active_p", 0.5)))
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
        self.last = {
            "present": self.present, "motion_state": motion, "motion_level": level,
            "distance_cm": (int(round(self.rnd.uniform(*((c.get("room_distance_cm") or [200, 450]) if in_room
                                                          else (c.get("distance_cm") or [65, 95])))))
                            if self.present and c.get("report_distance", True) else None),
            "resp_bpm": int(round(self.rnd.uniform(resp_lo, resp_hi))) if resp_ok else None, "resp_valid": bool(resp_ok),
            "heart_bpm": None, "heart_valid": False,          # 심박은 쓰지 않는다(개발 원칙 4) — fsm drowsy guard 는 꺼진 채
            "drowsy_state": state, "valid": True,
        }
        return self.last


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
