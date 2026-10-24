"""잠재 피로 모델(life_sim) — 졸음·키 입력 저하가 각본이 아니라 원인에서 생긴다. 기본 꺼짐(reaction.yaml fatigue.enabled).

왜: 지금까지 졸음 구간·키 입력 품질은 각본에 고정돼 있어서 CO₂·작업 시간·휴식·환기가 졸음을 만들거나 줄이지 못했다.
그 데이터로 피로 분류기를 학습하면 각본을 외운다. 이 모듈은 사람마다 보이지 않는 피로 F(0~1)를 1 초마다 굴리고,
F 에서 졸음 시작·스스로 깸·자세 무너짐·키 입력 저하를 뽑는다. 학습 정답 라벨도 F 에서 나온다(truth).
숫자는 전부 가정값(문헌 경향: 작업 시간·점심 뒤·밤에 졸림↑, CO₂ 1000 ppm 넘으면 인지 저하, 휴식으로 회복)이고
사람마다 범위 안에서 뽑는다 — 실측으로 맞춘 값이 아니다.

동역학(1 초)
  책상에서 일함   dF = k_task · m · (1 - F)        m = 1 + CO₂ 항 + 시각 항 + 나쁜 자세 항 + 어두움 항 + 더움 항
  자리를 비움     dF = -k_rest · F                  (휴식·식사·외출)
  졸고 있음       dF = 0                            (미세수면은 회복이 아니다)
  일시 각성 A     깨면(개입·스스로) A 가 오르고 tau 로 감쇠한다. 행동에 쓰는 졸림 S = F · (1 - A)
행동
  졸음 시작   초당 확률 = 최대율 · sigmoid((S - s50) / width), 타이핑 중이면 typing_factor 배
              → drowsy(또는 S 가 아주 높으면 가끔 face_down) 구간을 끼워 넣는다. 끄덕임 세기·간격·미세수면은 S 에서.
  스스로 깸   졸음 구간 길이를 S 로 뽑는다(지수분포). 구간이 끝나면 스스로 깬 것으로 보고 작은 각성.
  자세 무너짐 S 가 s_from 넘으면 거북목·턱괴기·숙임을 짧게 끼워 넣는다.
  키 입력     idle_ratio·flight_cv·correction_rate 에 S × keys_gain 을 더한다.
정답(truth)   책상에 없음: 비운 지 idle_after_s 전 rest, 그 뒤 idle / 있음: 졸음·엎드림이거나 S ≥ truth_threshold 면 fatigue,
              아니면 focus. 클래스는 ml 계약(focus·fatigue·rest·idle)과 같다.
"""
from __future__ import annotations

import datetime as _dt
import math
import random
from typing import Any

SLEEPY = {"drowsy", "face_down"}
POOR_POSTURE = {"turtle", "chin_rest", "lean_forward", "recline", "elbows"}
SLUMP_FROM = {"upright", "mouse_work"}


def _pick(rnd: random.Random, v: Any) -> float:
    """[lo, hi] 면 사람마다 하나 뽑고, 숫자면 그대로."""
    if isinstance(v, (list, tuple)):
        return float(rnd.uniform(float(v[0]), float(v[1])))
    return float(v)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


class FatigueModel:
    def __init__(self, cfg: dict[str, Any] | None, *, seed: int, t0: float):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", False))
        self.rnd = random.Random(seed + 41)
        c, r = self.c, self.rnd
        # 사람마다 뽑는 계수
        self.k_task = math.log(2.0) / (3600.0 * _pick(r, c.get("task_half_h", [0.9, 1.8])))
        self.k_rest = math.log(2.0) / (60.0 * _pick(r, c.get("rest_half_min", [10, 20])))
        co2 = c.get("co2") or {}
        self.co2_from = float(co2.get("from_ppm", 800))
        self.co2_gain = _pick(r, co2.get("per_1000ppm", [0.4, 0.9]))
        circ = c.get("circadian") or {}
        self.dip_hours = tuple(circ.get("dip_hours", [13, 16]))
        self.dip_gain = _pick(r, circ.get("dip_gain", [0.3, 0.7]))
        self.night_from = float(circ.get("night_from_h", 23.0))
        self.night_gain = _pick(r, circ.get("night_gain", [0.8, 1.6]))
        self.posture_gain = float(c.get("posture_gain", 0.2))
        dim, hot = c.get("dim") or {}, c.get("hot") or {}
        self.dim_lux, self.dim_gain = float(dim.get("below_lux", 200)), float(dim.get("gain", 0.3))
        self.hot_c, self.hot_gain = float(hot.get("above_c", 27.0)), float(hot.get("gain", 0.3))
        al = c.get("alert") or {}
        self.wake_boost = _pick(r, al.get("wake_boost", [0.35, 0.6]))
        self.self_wake_boost = _pick(r, al.get("self_wake_boost", [0.1, 0.25]))
        self.tau_alert = 60.0 * _pick(r, al.get("tau_min", [6, 12]))
        self.startle_drop = float(al.get("startle_drop", 0.05))
        dr = c.get("drowsy") or {}
        self.s50 = _pick(r, dr.get("s50", [0.58, 0.72]))
        self.width = float(dr.get("width", 0.05))
        self.onset_max = float(dr.get("max_rate_per_min", 0.5)) / 60.0
        self.typing_factor = float(dr.get("typing_factor", 0.5))
        self.ep_min = tuple(float(x) for x in dr.get("episode_mean_min", [3, 8]))
        self.face_down_p = float(dr.get("face_down_p", 0.25))
        sl = c.get("slump") or {}
        self.slump_from = float(sl.get("s_from", 0.35))
        self.slump_max = float(sl.get("max_rate_per_min", 0.15)) / 60.0
        self.slump_s = tuple(float(x) for x in sl.get("seconds", [60, 240]))
        self.slump_kinds = list(sl.get("kinds", ["turtle", "chin_rest", "lean_forward"]))
        self.keys_gain = tuple(float(x) for x in c.get("keys_gain", [0.35, 0.35, 0.08]))
        self.truth_threshold = float(c.get("truth_threshold", 0.6))
        self.idle_after_s = float(c.get("idle_after_s", 600))
        # 상태
        hour = self._hour(t0)
        self.F = _clip(_pick(r, c.get("f0", [0.10, 0.30])) + (0.15 if self._night(hour) else 0.0))
        self.A = 0.0
        self.in_episode = False
        self.woken = False
        self.n_onsets = self.n_self_wakes = self.n_wakes = self.n_slumps = 0
        self.log: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- 상태
    @property
    def S(self) -> float:
        """행동에 쓰는 졸림 = 피로 × (1 − 일시 각성)."""
        return self.F * (1.0 - self.A)

    @staticmethod
    def _hour(t: float) -> float:
        d = _dt.datetime.fromtimestamp(t)
        return d.hour + d.minute / 60.0

    def _night(self, hour: float) -> bool:
        return hour >= self.night_from or hour < 5.0

    def params(self) -> dict[str, float]:
        """이 사람에게 뽑힌 계수(요약·재현 확인용)."""
        return {"task_half_h": round(math.log(2.0) / self.k_task / 3600.0, 2),
                "rest_half_min": round(math.log(2.0) / self.k_rest / 60.0, 1), "co2_gain": round(self.co2_gain, 2),
                "dip_gain": round(self.dip_gain, 2), "night_gain": round(self.night_gain, 2), "s50": round(self.s50, 3),
                "wake_boost": round(self.wake_boost, 2), "tau_alert_min": round(self.tau_alert / 60.0, 1)}

    def step(self, now: float, *, kind: str, co2: float, lux: float, felt_c: float) -> None:
        """1 초. kind = 지금 사람 구간 종류(away·drowsy·upright ...)."""
        if not self.enabled:
            return
        self.A *= math.exp(-1.0 / self.tau_alert)
        if kind == "away":
            self.F -= self.k_rest * self.F
        elif kind not in SLEEPY:
            hour = self._hour(now)
            m = 1.0 + self.co2_gain * max(0.0, co2 - self.co2_from) / 1000.0
            if self.dip_hours[0] <= hour < self.dip_hours[1]:
                m += self.dip_gain
            if self._night(hour):
                m += self.night_gain
            if kind in POOR_POSTURE:
                m += self.posture_gain
            if lux < self.dim_lux:
                m += self.dim_gain
            if felt_c > self.hot_c:
                m += self.hot_gain
            self.F += self.k_task * m * (1.0 - self.F)
        self.F = _clip(self.F)

    def on_wake(self, now: float, *, source: str) -> None:
        """개입 반응으로 깼다(하네스 wake). 큰 일시 각성 + 놀람으로 피로 조금 내림."""
        if not self.enabled:
            return
        self.A = _clip(self.A + self.wake_boost)
        self.F = _clip(self.F - self.startle_drop)
        self.woken = True
        self.n_wakes += 1
        self.log.append({"t": now, "what": "wake", "source": source, "F": round(self.F, 3)})

    # ---------------------------------------------------------------- 행동
    def decide(self, now: float, *, kind: str, typing: bool) -> list[dict[str, Any]]:
        """이번 초에 끼워 넣을 구간(src.override 인자)들. 대부분 빈 목록."""
        if not self.enabled:
            return []
        out: list[dict[str, Any]] = []
        S = self.S
        if self.in_episode and kind not in SLEEPY:          # 졸음 구간이 끝났다 — 개입으로 깬 게 아니면 스스로 깸
            self.in_episode = False
            if not self.woken:
                self.A = _clip(self.A + self.self_wake_boost)
                self.n_self_wakes += 1
                self.log.append({"t": now, "what": "self_wake", "F": round(self.F, 3)})
            self.woken = False
        if kind in SLEEPY or kind == "away":     # 깬 직후는 따로 막지 않는다 — 일시 각성 A 가 S 를 낮춘다
            return out
        rate = self.onset_max / (1.0 + math.exp(-(S - self.s50) / self.width))
        if typing:
            rate *= self.typing_factor
        if self.rnd.random() < rate:
            lo, hi = self.ep_min
            mean_s = 60.0 * (lo + (hi - lo) * _clip((S - 0.5) / 0.4))
            seconds = float(min(1200.0, max(60.0, self.rnd.expovariate(1.0 / mean_s))))
            face_down = S > 0.8 and self.rnd.random() < self.face_down_p
            amp_lo = _clip(12.0 + 45.0 * (S - 0.5), 12.0, 40.0)
            spec = {} if face_down else {"nods": {
                "amp_deg": [round(amp_lo, 1), round(amp_lo + 15.0, 1)],
                "interval_s": [round(max(3.0, 14.0 - 20.0 * (S - 0.5)), 1), round(max(6.0, 24.0 - 32.0 * (S - 0.5)), 1)],
                "micro_sleep_p": round(_clip(0.2 + (S - 0.5), 0.15, 0.75), 2),
                "hold_s": [4.0, round(8.0 + 20.0 * max(0.0, S - 0.5), 1)], "first_s": [5.0, 20.0]}}
            self.in_episode, self.woken = True, False
            self.n_onsets += 1
            what = "face_down" if face_down else "drowsy"
            self.log.append({"t": now, "what": f"onset_{what}", "S": round(S, 3), "seconds": round(seconds)})
            out.append({"kind": what, "seconds": seconds, "name": f"fatigue_{what}", "typing": False, "spec": spec,
                        "transition_s": 2.0})
            return out
        if kind in SLUMP_FROM and S > self.slump_from:
            if self.rnd.random() < self.slump_max * _clip((S - self.slump_from) / (1.0 - self.slump_from)):
                k = self.rnd.choice(self.slump_kinds)
                self.n_slumps += 1
                out.append({"kind": k, "seconds": self.rnd.uniform(*self.slump_s), "name": "fatigue_slump",
                            "typing": typing and k != "chin_rest", "transition_s": 2.0})
        return out

    def keys(self, base: tuple[float, float, float]) -> tuple[float, float, float]:
        """피곤할수록 쉬는 비율·타건 간격 흩어짐·정정이 는다."""
        if not self.enabled:
            return base
        S = self.S
        return (min(1.0, base[0] + S * self.keys_gain[0]), min(1.5, base[1] + S * self.keys_gain[1]),
                min(0.5, base[2] + S * self.keys_gain[2]))

    def truth(self, *, kind: str, absent_s: float | None) -> str:
        """ml 계약 클래스(focus·fatigue·rest·idle) 정답."""
        if kind == "away":
            return "rest" if (absent_s or 0.0) < self.idle_after_s else "idle"
        if kind in SLEEPY or self.S >= self.truth_threshold:
            return "fatigue"
        return "focus"

    def summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        cls = [r.get("truth_class") for r in rows if r.get("truth_class")]
        Ss = [r["fatigue_S"] for r in rows if r.get("fatigue_S") is not None and r.get("truth_present")]
        return {"params": self.params(), "truth_min": {k: round(cls.count(k) / 60.0, 1) for k in ("focus", "fatigue", "rest", "idle")},
                "onsets": self.n_onsets, "self_wakes": self.n_self_wakes, "wakes": self.n_wakes, "slumps": self.n_slumps,
                "S_mean_present": round(sum(Ss) / len(Ss), 3) if Ss else None,
                "S_max": round(max(Ss), 3) if Ss else None, "F_end": round(self.F, 3), "log": self.log[:200]}
