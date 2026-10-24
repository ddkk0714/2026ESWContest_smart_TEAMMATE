"""합성 30일 개인화 로그 생성기 — 페르소나 3개. 모든 줄에 simulated=true, persona=<id> 가 찍힌다.

실측 로그가 쌓이기 전에 profile/policy 를 돌려 보기 위한 것이다. 페르소나의 수락 확률·효과는 손으로 적은
가정이지 사용자 데이터가 아니다. 실기 로그와 같은 파일에 섞지 않는다(logs/synth/ 아래).

    gen = SynthGenerator(PERSONAS["night_owl"], seed=1, days=30)
    records = gen.generate()                 # 전역 기본 규칙으로 고른 개입 + 페르소나 응답
    records = gen.generate(policy=policy)    # 밴딧이 고르고, 페르소나가 응답하고, 밴딧이 학습 (온라인)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import load_personalize_config
from .features import POSTURE_LABELS, Situation
from .log import SESSION_SCHEMA, SCHEMA_VERSION, build_episode, build_outcome, local_iso
from .policy import REWARD_ACCEPT, SLEEP_ARM, Decision, InterventionPolicy
from .profile import ProfileSummary, is_after_bedtime

SLUMP = ("recline", "face_down", "chin_rest", "drowsy")


@dataclass
class Persona:
    id: str
    label_ko: str
    session_windows: list[tuple[float, float]]        # (시작 시각 범위 lo, hi) — 시(소수), 24 넘으면 다음날
    session_len_min: tuple[float, float]              # (lo, hi)
    sessions_per_day: tuple[int, int]
    pc_ratio: float
    bedtime_hour: float                               # 평소 취침(25.5 = 01:30)
    fatigue_events_per_hour: float
    cause_weights: dict[str, float]
    co2_base: float
    co2_rise_per_hour: float
    co2_fatigue_ppm: float                            # 이 이상이면 환경성 원인이 커진다
    lux: float
    temp_c: float
    slump_prob: float
    first_slump_min: tuple[float, float]              # (mean, sd)
    accept: dict[str, float]                          # 제안 수락 확률
    undo: dict[str, float] = field(default_factory=dict)       # 자동 실행 되돌리기 확률
    effect: dict[str, float] = field(default_factory=dict)     # 적용 시 C_fatigue 하락 평균
    timeout_prob: float = 0.1
    recovery_after_alert_min: tuple[float, float] = (3.0, 1.0)
    late_bias: dict[str, float] = field(default_factory=dict)  # 취침 뒤 수락 확률 가산


PERSONAS: dict[str, Persona] = {
    "night_owl": Persona(
        id="night_owl", label_ko="올빼미 코더", session_windows=[(20.0, 22.0)], session_len_min=(150, 240),
        sessions_per_day=(1, 1), pc_ratio=0.92, bedtime_hour=25.5, fatigue_events_per_hour=1.4,
        cause_weights={"environment": 0.25, "posture": 0.25, "cognitive": 0.5},
        co2_base=650, co2_rise_per_hour=120, co2_fatigue_ppm=1400, lux=90, temp_c=23.0,
        slump_prob=0.35, first_slump_min=(70, 15),
        accept={"light_cool_bright": 0.85, "air_fan_up": 0.5, "ventilate_suggest": 0.2,
                "break_short": 0.8, "break_long": 0.15, "light_warm_dim": 0.3, "sound_cue": 0.2,
                "posture_card": 0.7, "posture_voice": 0.1, SLEEP_ARM: 0.6},
        undo={"air_fan_up": 0.7, "light_cool_bright": 0.05, "posture_card": 0.05, "posture_voice": 0.6, "sound_cue": 0.5},
        effect={"light_cool_bright": 0.5, "air_fan_up": 0.2, "ventilate_suggest": 0.2, "break_short": 0.4,
                "break_long": 0.3, "light_warm_dim": 0.1, "sound_cue": 0.1, "posture_card": 0.4, "posture_voice": 0.3},
        late_bias={"break_long": 0.1, SLEEP_ARM: 0.2},
    ),
    "afternoon_slumper": Persona(
        id="afternoon_slumper", label_ko="오후 슬럼프 학생", session_windows=[(13.0, 14.5), (16.0, 17.0)],
        session_len_min=(90, 180), sessions_per_day=(1, 2), pc_ratio=0.5, bedtime_hour=23.5,
        fatigue_events_per_hour=1.8, cause_weights={"environment": 0.2, "posture": 0.5, "cognitive": 0.3},
        co2_base=750, co2_rise_per_hour=200, co2_fatigue_ppm=1150, lux=350, temp_c=25.0,
        slump_prob=0.7, first_slump_min=(30, 10),
        accept={"light_cool_bright": 0.4, "air_fan_up": 0.6, "ventilate_suggest": 0.5,
                "break_short": 0.4, "break_long": 0.8, "light_warm_dim": 0.2, "sound_cue": 0.3,
                "posture_card": 0.45, "posture_voice": 0.85, SLEEP_ARM: 0.5},
        undo={"air_fan_up": 0.1, "light_cool_bright": 0.3, "posture_card": 0.2, "posture_voice": 0.05, "sound_cue": 0.3},
        effect={"light_cool_bright": 0.2, "air_fan_up": 0.4, "ventilate_suggest": 0.5, "break_short": 0.2,
                "break_long": 0.6, "light_warm_dim": 0.1, "sound_cue": 0.15, "posture_card": 0.3, "posture_voice": 0.6},
        recovery_after_alert_min=(2.0, 0.8),
    ),
    "fresh_air": Persona(
        id="fresh_air", label_ko="환기 민감", session_windows=[(9.0, 10.0), (14.0, 15.0)], session_len_min=(100, 170),
        sessions_per_day=(1, 2), pc_ratio=0.75, bedtime_hour=23.0, fatigue_events_per_hour=1.5,
        cause_weights={"environment": 0.55, "posture": 0.2, "cognitive": 0.25},
        co2_base=600, co2_rise_per_hour=220, co2_fatigue_ppm=900, lux=420, temp_c=24.0,
        slump_prob=0.3, first_slump_min=(55, 15),
        accept={"light_cool_bright": 0.3, "air_fan_up": 0.85, "ventilate_suggest": 0.9,
                "break_short": 0.6, "break_long": 0.4, "light_warm_dim": 0.2, "sound_cue": 0.2,
                "posture_card": 0.6, "posture_voice": 0.4, SLEEP_ARM: 0.5},
        undo={"air_fan_up": 0.05, "light_cool_bright": 0.5, "posture_card": 0.1, "posture_voice": 0.3, "sound_cue": 0.4},
        effect={"light_cool_bright": 0.1, "air_fan_up": 0.6, "ventilate_suggest": 0.7, "break_short": 0.4,
                "break_long": 0.4, "light_warm_dim": 0.1, "sound_cue": 0.1, "posture_card": 0.4, "posture_voice": 0.3},
        recovery_after_alert_min=(3.5, 1.0),
    ),
}

# 전역 기본 규칙(정책 없이 로그를 만들 때): cause → arm
def default_rule(cause: str, sit: Situation, cfg: dict[str, Any]) -> str:
    if cause == "environment":
        if sit.co2_ppm is not None and sit.co2_ppm >= float(cfg["explain"]["co2_high_ppm"]):
            return "air_fan_up"
        return "light_cool_bright"
    if cause == "posture":
        return "posture_card"
    return "break_long" if sit.session_elapsed_min >= float(cfg["explain"]["session_long_min"]) else "break_short"


class SynthGenerator:
    def __init__(self, persona: Persona, *, seed: int = 0, days: int = 30,
                 start_date: date = date(2026, 9, 21), cfg: dict[str, Any] | None = None):
        self.persona = persona
        self.rng = random.Random(seed)
        self.days = days
        self.start_date = start_date
        self.cfg = cfg if cfg is not None else load_personalize_config()
        self.offset = float(self.cfg.get("local_utc_offset_hours", 9))
        self.tz = timezone(timedelta(hours=self.offset))
        self._eid = 0
        self.profile_id = f"synth-{persona.id}"

    # ── 보조 ───────────────────────────────────────────────────
    def _ts(self, day: date, hour: float) -> float:
        base = datetime(day.year, day.month, day.day, tzinfo=self.tz)
        return (base + timedelta(hours=hour)).timestamp()

    def _weather(self, month: int) -> dict[str, Any]:
        r = self.rng
        outdoor = {1: 0, 2: 2, 3: 8, 4: 14, 5: 19, 6: 24, 7: 28, 8: 29, 9: 24, 10: 16, 11: 8, 12: 2}[month]
        return {"windy": r.random() < 0.2, "rain": r.random() < 0.15, "pm_bad": r.random() < 0.1,
                "outdoor_temp_c": outdoor + r.uniform(-3, 3)}

    def _posture(self, slump: bool, elapsed_min: float) -> tuple[dict[str, float], str, float, float]:
        r = self.rng
        dist = {label: 0.0 for label in POSTURE_LABELS}
        if slump:
            label = r.choice(SLUMP)
            hold = r.uniform(3, 15)
            dist[label] = r.uniform(0.4, 0.7)
            dist["upright"] = 1.0 - dist[label] - 0.1
            dist["forward_head"] = 0.1
            return dist, label, hold, hold
        label = r.choice(["upright", "forward_head", "lean_forward"])
        hold = r.uniform(1, 20) if label != "upright" else r.uniform(5, 40)
        dist[label] = r.uniform(0.5, 0.8)
        dist["upright"] = max(0.0, 1.0 - dist[label]) if label != "upright" else dist["upright"]
        return dist, label, hold, 0.0

    def _after_bedtime(self, hour_abs: float) -> bool:
        return hour_abs >= self.persona.bedtime_hour

    def respond(self, arm: str, sit: Situation, delivered_gate: str) -> tuple[str, int | None]:
        """페르소나 응답: (verdict, response_ms)."""
        p, r = self.persona, self.rng
        if delivered_gate == "auto":
            return ("undo", None) if r.random() < p.undo.get(arm, 0.1) else ("auto_kept", None)
        if r.random() < p.timeout_prob:
            return "timeout", None
        prob = p.accept.get(arm, 0.3)
        if sit.after_bedtime:
            prob += p.late_bias.get(arm, 0.0)
        return ("accept" if r.random() < prob else "reject"), int(r.uniform(1500, 9000))

    def effect(self, arm: str, c_before: float, verdict: str) -> float:
        r = self.rng
        if verdict in ("accept", "auto_kept"):
            drop = self.persona.effect.get(arm, 0.2) * r.uniform(0.7, 1.3)
            return max(0.0, min(1.0, c_before - drop + r.gauss(0.0, 0.03)))
        return max(0.0, min(1.0, c_before + r.uniform(-0.02, 0.06)))

    # ── 생성 ───────────────────────────────────────────────────
    def generate(self, policy: InterventionPolicy | None = None,
                 profile: ProfileSummary | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for d in range(self.days):
            day = self.start_date + timedelta(days=d)
            n_sessions = self.rng.randint(*self.persona.sessions_per_day)
            windows = self.rng.sample(self.persona.session_windows, k=min(n_sessions, len(self.persona.session_windows)))
            for si, (lo, hi) in enumerate(sorted(windows)):
                records += self._session(day, d, si, self.rng.uniform(lo, hi), policy, profile)
        return records

    def _session(self, day: date, d: int, si: int, start_hour: float,
                 policy: InterventionPolicy | None, profile: ProfileSummary | None) -> list[dict[str, Any]]:
        p, r = self.persona, self.rng
        session_id = f"synth-{p.id}-d{d:02d}-s{si}"
        length = r.uniform(*p.session_len_min)
        start_ts = self._ts(day, start_hour)
        end_ts = start_ts + length * 60.0
        weather = self._weather(day.month)
        n_events = max(1, int(round(r.gauss(p.fatigue_events_per_hour * length / 60.0, 0.8))))
        event_min = sorted(r.uniform(20.0, length - 5.0) for _ in range(n_events))
        first_slump = max(5.0, r.gauss(*p.first_slump_min))
        slump_count = 0
        recovery: list[float] = []
        co2_at_fatigue: list[float] = []
        fatigue_by_hour: dict[str, list[float]] = {}
        last_intervention: float | None = None
        drowsy_count = 0
        chain_retry = False
        out: list[dict[str, Any]] = []
        for em in event_min:
            ts = start_ts + em * 60.0
            hour_abs = start_hour + em / 60.0
            hour = hour_abs % 24.0
            dt = datetime.fromtimestamp(ts, self.tz)
            co2 = p.co2_base + p.co2_rise_per_hour * (em / 60.0) + r.gauss(0, 40)
            slump = r.random() < p.slump_prob or em >= first_slump and r.random() < 0.5
            dist, label, hold, slump_hold = self._posture(slump, em)
            if slump:
                slump_count += 1
            late = self._after_bedtime(hour_abs)
            drowsy = (late or em > length * 0.6) and r.random() < 0.5
            drowsy_count = min(4, drowsy_count + (1 if drowsy else 0)) if em - (event_min[0]) < 30 or drowsy else (1 if drowsy else 0)
            c_before = min(0.95, r.uniform(0.70, 0.88) + (0.05 if late else 0.0))
            c_focus = r.uniform(0.2, 0.5)
            after_bed = is_after_bedtime(hour, profile, self.cfg) if profile is not None else late
            sit = Situation(
                ts=ts, hour_local=hour, weekday=dt.weekday(), month=dt.month,
                posture_dist=dist, posture_label=label, posture_hold_min=hold, slump_hold_min=slump_hold,
                present=True, drowsy=drowsy, drowsy_count_30min=drowsy_count,
                co2_ppm=round(co2), lux=p.lux + r.uniform(-30, 30), temp_c=p.temp_c + r.uniform(-1, 1),
                humidity_pct=50 + r.uniform(-8, 8), pc_ratio=p.pc_ratio, typing_active=r.random() < p.pc_ratio,
                c_fatigue=c_before, c_focus=c_focus, session_elapsed_min=em,
                since_last_intervention_min=None if last_intervention is None else (em - last_intervention),
                after_bedtime=after_bed, weather=weather,
            )
            # 원인 — 페르소나 가중치 + 상황 보정
            w = dict(p.cause_weights)
            if co2 >= p.co2_fatigue_ppm:
                w["environment"] += 0.6
            if slump:
                w["posture"] += 0.4
            cause = r.choices(list(w), weights=list(w.values()))[0]
            # 환경성 원인이 CO₂ 때문일 때만(실기에서는 environment 신호 reason co2_high/co2_rise) 민감 문턱 표본
            if cause == "environment" and co2 >= p.co2_fatigue_ppm:
                co2_at_fatigue.append(round(co2))
            retry = chain_retry
            fsm_gate = "suggest" if retry or c_before < 0.75 else "auto"
            hb = str(dt.hour)
            acc = fatigue_by_hour.setdefault(hb, [0.0, 0])
            acc[0] += c_before
            acc[1] += 1

            if policy is not None:
                decision = policy.decide(sit, cause=cause, gate=fsm_gate, session_id=session_id, now=ts)
            else:
                arm = default_rule(cause, sit, self.cfg)
                spec = next(a for a in self.cfg["arms"] if a["id"] == arm)
                delivered = "auto" if fsm_gate == "auto" and spec.get("auto") else "suggest"
                decision = Decision(arm=arm, candidates=[arm], excluded={}, source="rule_default", policy_mode="global",
                                    n_personal=0, scores={}, delivered_gate=delivered, auto_threshold=None,
                                    explanation="전역 기본 규칙(합성)", session_id=session_id, cause=cause,
                                    fsm_gate=fsm_gate, ts=ts, c_fatigue_before=c_before)
            if decision.arm is None:
                continue
            verdict, response_ms = self.respond(decision.arm, sit, decision.delivered_gate)
            c_after = self.effect(decision.arm, c_before, verdict)
            ended = decision.arm == SLEEP_ARM and verdict == "accept"
            measured = (ts + float(self.cfg["bandit"]["effect_window_min"]) * 60.0) <= end_ts and not ended
            r_acc = REWARD_ACCEPT[verdict]
            if policy is not None:
                policy.feedback(decision, verdict, now=ts + 5)
                policy.outcome(decision, c_after if measured else None, status="measured" if measured else "session_ended")
            b = self.cfg["bandit"]
            posture_recovered = None
            if cause == "posture" and verdict in ("accept", "auto_kept"):
                posture_recovered = r.random() < 0.8
                if posture_recovered:
                    recovery.append(max(0.5, r.gauss(*p.recovery_after_alert_min)))
            outcome = build_outcome(
                status="measured" if measured else "session_ended", c_before=c_before,
                c_after=c_after if measured else None, reward_accept=r_acc, full_drop=float(b["effect_full_drop"]),
                weight_accept=float(b["weight_accept"]), weight_effect=float(b["weight_effect"]),
                posture_recovered=posture_recovered,
                next_state="END" if ended else ("RECOVERY" if c_after < c_before - 0.05 else "ESCALATE"))
            self._eid += 1
            out.append(build_episode(
                episode_id=f"synth-{p.id}-pe{self._eid}", session_id=session_id, profile_id=self.profile_id,
                ts=ts, utc_offset_hours=self.offset, situation=sit,
                fsm={"state": {"environment": "ACTION_ENV", "posture": "ACTION_POSTURE", "cognitive": "ACTION_BREAK"}[cause],
                     "cause": cause, "gate": fsm_gate, "retry": retry,
                     "context": "pc" if p.pc_ratio > 0.7 else ("npc" if p.pc_ratio < 0.3 else "mixed"), "guards": []},
                decision=decision,
                feedback={"verdict": verdict, "response_ms": response_ms, "feedback_ts_ms": int((ts + 5) * 1000),
                          "request_id": None if decision.delivered_gate == "auto" else f"synth-rq-{self._eid}"},
                outcome=outcome, simulated=True, persona=p.id))
            last_intervention = em
            chain_retry = outcome["next_state"] == "ESCALATE"
            if ended:
                end_ts = ts + 60.0
                break
        end_hour_abs = start_hour + (end_ts - start_ts) / 3600.0
        out.append({
            "schema_name": SESSION_SCHEMA, "schema_version": SCHEMA_VERSION, "simulated": True, "persona": p.id,
            "profile_id": self.profile_id, "session_id": session_id,
            "ts_ms": int(start_ts * 1000), "local_time": local_iso(start_ts, self.offset),
            "start_hour_local": round(start_hour, 2), "end_hour_local": round(end_hour_abs % 24.0, 2),
            "length_min": round((end_ts - start_ts) / 60.0, 1),
            "first_slump_min": round(first_slump, 1) if first_slump < (end_ts - start_ts) / 60.0 else None,
            "slump_count": slump_count, "recovery_after_alert_min": [round(v, 1) for v in recovery],
            "break_interval_min": [round(r.uniform(40, 90), 1) for _ in range(max(0, len(event_min) - 1))],
            "fatigue_by_hour": {h: [round(s, 3), n] for h, (s, n) in fatigue_by_hour.items()},
            "co2_at_fatigue": co2_at_fatigue,
        })
        return out


def arm_counts(records: list[dict[str, Any]], *, cause: str | None = None, last_days: int | None = None) -> dict[str, int]:
    """에피소드 줄에서 arm 선택 횟수. 테스트·시연 요약용."""
    eps = [r for r in records if r.get("schema_name") == "personalize_episode" and r["decision"].get("arm")]
    if cause:
        eps = [r for r in eps if r["fsm"]["cause"] == cause]
    if last_days:
        dates = sorted({r["local_time"][:10] for r in eps})[-last_days:]
        eps = [r for r in eps if r["local_time"][:10] in dates]
    counts: dict[str, int] = {}
    for r in eps:
        counts[r["decision"]["arm"]] = counts.get(r["decision"]["arm"], 0) + 1
    return counts
