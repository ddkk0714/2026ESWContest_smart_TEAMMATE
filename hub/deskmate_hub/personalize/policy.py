"""3층 개입 선택 정책 — 컨텍스트 밴딧(arm 별 베이지안 선형 회귀 + Thompson 샘플링). numpy 만 쓴다.

docs/personalization-spec.md §5–§8. FSM(1층)의 cause·gate 를 받아 그 cause 그룹 안의 arm 하나와
전달 게이트(FSM 게이트를 넘지 않음)·설명 한 줄을 돌려준다. engine 은 건드리지 않는다.

    policy = InterventionPolicy()                       # config/personalize.yaml
    d = policy.decide(situation, cause="environment", gate="auto", session_id="s1", now=t)
    policy.feedback(d, "accept", now=t + 5)             # 수용도 축 갱신
    policy.outcome(d, c_fatigue_after=0.52)             # 10 분 뒤 효과 축 갱신
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import load_personalize_config
from .features import DIM, FEATURE_INDEX, Situation, prior_vector, vectorize

AXES = ("accept", "effect")
from .rewards import REWARD_ACCEPT, VERDICTS, reward_effect  # noqa: F401 — 이전 import 경로 유지
SLEEP_ARM = "sleep_suggest"


def _gate_value(gate: Any) -> str:
    return getattr(gate, "value", gate)


@dataclass
class ArmSpec:
    id: str
    label_ko: str
    causes: tuple[str, ...]
    channel: str
    auto: bool
    rule_only: bool = False
    device: dict[str, Any] | None = None


class BayesLinReg:
    """A = λI + Σxxᵀ, b = λθ⁰ + Σrx. 사후 평균 μ = A⁻¹b, Thompson 샘플 ~ N(μ, σ²·scale·A⁻¹)."""

    def __init__(self, theta0: np.ndarray, lam: float, noise_var: float):
        self.theta0 = theta0.astype(float)
        self.lam = float(lam)
        self.noise_var = float(noise_var)
        self.A = np.eye(DIM) * self.lam
        self.b = self.lam * self.theta0
        self.n = 0
        self._inv: np.ndarray | None = None

    def _cov(self) -> np.ndarray:
        if self._inv is None:
            self._inv = np.linalg.inv(self.A)
        return self._inv

    def mean(self) -> np.ndarray:
        return self._cov() @ self.b

    def sample(self, rng: np.random.Generator, scale: float) -> np.ndarray:
        cov = self.noise_var * scale * self._cov()
        cov = 0.5 * (cov + cov.T)
        return rng.multivariate_normal(self.mean(), cov, method="cholesky")

    def update(self, x: np.ndarray, r: float) -> None:
        self.A += np.outer(x, x)
        self.b += float(r) * x
        self.n += 1
        self._inv = None

    def to_dict(self) -> dict[str, Any]:
        return {"A": self.A.tolist(), "b": self.b.tolist(), "n": self.n}

    def load(self, data: dict[str, Any]) -> None:
        A = np.array(data["A"], dtype=float)
        b = np.array(data["b"], dtype=float)
        if A.shape != (DIM, DIM) or b.shape != (DIM,):
            raise ValueError("posterior 차원이 FEATURE_NAMES 와 다르다")
        self.A, self.b, self.n = A, b, int(data.get("n", 0))
        self._inv = None


@dataclass
class Decision:
    """decide() 결과. 로그 `decision` 객체 + 학습에 필요한 x·맥락."""
    arm: str | None
    candidates: list[str]
    excluded: dict[str, str]
    source: str                      # bandit | rule_sleep | fallback
    policy_mode: str                 # global | personal
    n_personal: int
    scores: dict[str, float]
    delivered_gate: str              # auto | suggest | none
    auto_threshold: float | None
    explanation: str
    session_id: str
    cause: str
    fsm_gate: str
    ts: float
    c_fatigue_before: float
    x: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(DIM))
    ventilate_minutes: int | None = None     # arm == ventilate_suggest 일 때 권유 길이(겨울 짧게)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm, "candidates": list(self.candidates), "excluded": dict(self.excluded),
            "source": self.source, "policy_mode": self.policy_mode, "n_personal": self.n_personal,
            "scores": {k: round(float(v), 4) for k, v in self.scores.items()},
            "delivered_gate": self.delivered_gate,
            "auto_threshold": None if self.auto_threshold is None else round(float(self.auto_threshold), 4),
            "explanation": self.explanation,
            "ventilate_minutes": self.ventilate_minutes,
        }




class InterventionPolicy:
    def __init__(self, cfg: dict[str, Any] | None = None, *, rng: np.random.Generator | None = None,
                 profile: Any = None):
        self.cfg = cfg if cfg is not None else load_personalize_config()
        self.rng = rng if rng is not None else np.random.default_rng()
        self.profile = profile          # ProfileSummary | None — 설명 문구·취침 규칙 보조
        self.arms: dict[str, ArmSpec] = {}
        for spec in self.cfg["arms"]:
            self.arms[spec["id"]] = ArmSpec(
                id=spec["id"], label_ko=spec.get("label_ko", spec["id"]), causes=tuple(spec["causes"]),
                channel=spec.get("channel", "display"), auto=bool(spec.get("auto", False)),
                rule_only=bool(spec.get("rule_only", False)), device=spec.get("device"))
        bcfg = self.cfg["bandit"]
        prior = self.cfg.get("prior", {})
        self.models: dict[str, dict[str, BayesLinReg]] = {}
        for arm in self.arms.values():
            if arm.rule_only:
                continue
            self.models[arm.id] = {
                axis: BayesLinReg(prior_vector(prior.get(arm.id, {}).get(axis)),
                                  bcfg["prior_precision"], bcfg["noise_var"])
                for axis in AXES
            }
        # 개인 통계 (arm 별)
        self.stats: dict[str, dict[str, float]] = {
            a: {"n": 0, "accept": 0, "reject": 0, "timeout": 0, "undo": 0, "auto_kept": 0,
                "effect_sum": 0.0, "effect_n": 0}
            for a in self.arms}
        self.undo_penalty: dict[str, float] = {a: 0.0 for a in self.arms}
        self.last_exec: dict[str, float] = {}
        self.n_feedback = 0
        # 세션 상태
        self._session_id: str | None = None
        self._rejected: set[str] = set()
        self._session_count = 0
        self._session_results: list[bool] = []     # 개입별 유효 여부 (시간순)
        self._sleep_done = False
        self._pending_effect: dict[int, bool] = {}

    # ── 상태 ─────────────────────────────────────────────────
    @property
    def n_personal(self) -> int:
        return self.n_feedback

    @property
    def policy_mode(self) -> str:
        return "personal" if self.n_feedback >= int(self.cfg["cold_start"]["personal_min"]) else "global"

    def auto_threshold(self, arm: str) -> float:
        return float(self.cfg["gate"]["conf_auto"]) + self.undo_penalty.get(arm, 0.0)

    def _begin_session(self, session_id: str) -> None:
        if session_id != self._session_id:
            self._session_id = session_id
            self._rejected = set()
            self._session_count = 0
            self._session_results = []
            self._sleep_done = False
            self._pending_effect = {}

    def reset_session(self) -> None:
        self._session_id = None
        self._begin_session("__none__")
        self._session_id = None

    # ── 결정 ─────────────────────────────────────────────────
    def _in_quiet_hours(self, hour: float) -> bool:
        q = self.cfg["bandit"].get("quiet_hours")
        if not q:
            return False
        start, end = float(q["start"]), float(q["end"])
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def _weather_excludes_ventilate(self, sit: Situation) -> str | None:
        w = sit.weather or {}
        if w.get("rain") or w.get("pm_bad"):
            return "weather"
        outdoor = w.get("outdoor_temp_c")
        margin = float(self.cfg["weather"].get("outdoor_hotter_margin_c", 2.0))
        if outdoor is not None and sit.temp_c is not None and float(outdoor) > float(sit.temp_c) + margin:
            return "weather"
        return None

    def ventilate_minutes(self, sit: Situation) -> int:
        """환기 권유 길이. 겨울(weather.winter_months)은 짧게."""
        w = self.cfg["weather"]
        if sit.month in w["winter_months"]:
            return int(w["ventilate_minutes_winter"])
        return int(w["ventilate_minutes_default"])

    def _candidates(self, sit: Situation, cause: str, now: float) -> tuple[list[str], dict[str, str]]:
        cands, excluded = [], {}
        cooldown = float(self.cfg["bandit"]["cooldown_sec"])
        quiet = self._in_quiet_hours(sit.hour_local)
        for arm in self.arms.values():
            if arm.rule_only or cause not in arm.causes:
                continue
            if arm.id in self._rejected:
                excluded[arm.id] = "rejected_in_session"
            elif arm.id in self.last_exec and now - self.last_exec[arm.id] < cooldown:
                excluded[arm.id] = "cooldown"
            elif quiet and arm.channel in ("sound", "voice"):
                excluded[arm.id] = "quiet_hours"
            elif arm.id == "ventilate_suggest" and self._weather_excludes_ventilate(sit):
                excluded[arm.id] = "weather"
            else:
                cands.append(arm.id)
        return cands, excluded

    def _ineffective_tail(self) -> int:
        n = 0
        for ok in reversed(self._session_results):
            if ok:
                break
            n += 1
        return n

    def _sleep_rule(self, sit: Situation) -> bool:
        s = self.cfg["sleep"]
        return (sit.after_bedtime and sit.c_fatigue >= float(s["fatigue_min"])
                and sit.drowsy_count_30min >= int(s["drowsy_min_count"])
                and self._ineffective_tail() >= int(s["ineffective_min"]) and not self._sleep_done)

    def _score(self, arm: str, x: np.ndarray) -> float:
        b = self.cfg["bandit"]
        cs = self.cfg["cold_start"]
        scale = float(cs["explore_scale_personal"] if self.policy_mode == "personal" else cs["explore_scale_global"])
        total = 0.0
        for axis, w in (("accept", b["weight_accept"]), ("effect", b["weight_effect"])):
            model = self.models[arm][axis]
            theta = model.mean() if b.get("algorithm") == "greedy" else model.sample(self.rng, scale)
            total += float(w) * float(theta @ x)
        # 날씨 고정 보정 (spec §7)
        if x[FEATURE_INDEX["windy"]] > 0.5:
            wcfg = self.cfg["weather"]
            if arm == "ventilate_suggest":
                total += float(wcfg["windy_bias_ventilate"])
            elif arm == "air_fan_up":
                total += float(wcfg["windy_bias_air_fan"])
        return total

    def decide(self, sit: Situation, *, cause: str, gate: Any, session_id: str, now: float | None = None) -> Decision:
        gate_s = _gate_value(gate)
        now = sit.ts if now is None else now
        self._begin_session(session_id)
        x = vectorize(sit, self.cfg["features"])
        base = dict(session_id=session_id, cause=cause, fsm_gate=gate_s, ts=now,
                    c_fatigue_before=float(sit.c_fatigue), x=x, policy_mode=self.policy_mode,
                    n_personal=self.n_feedback)
        if gate_s == "none":
            return Decision(arm=None, candidates=[], excluded={}, source="fallback", scores={},
                            delivered_gate="none", auto_threshold=None, explanation="정책 미적용", **base)
        if self._session_count >= int(self.cfg["bandit"]["max_per_session"]):
            return Decision(arm=None, candidates=[], excluded={}, source="fallback", scores={},
                            delivered_gate="none", auto_threshold=None,
                            explanation="세션 개입 상한 도달 · FSM 기본 동작", **base)
        cands, excluded = self._candidates(sit, cause, now)
        if self._sleep_rule(sit):
            self._sleep_done = True
            self._session_count += 1
            return Decision(arm=SLEEP_ARM, candidates=cands, excluded=excluded, source="rule_sleep", scores={},
                            delivered_gate="suggest", auto_threshold=None,
                            explanation=self._explain_sleep(sit), **base)
        if not cands:
            return Decision(arm=None, candidates=[], excluded=excluded, source="fallback", scores={},
                            delivered_gate="none", auto_threshold=None,
                            explanation="후보 없음 · FSM 기본 동작", **base)
        scores = {a: self._score(a, x) for a in cands}
        arm = max(scores, key=scores.get)
        spec = self.arms[arm]
        threshold = self.auto_threshold(arm)
        if gate_s == "auto" and spec.auto and sit.c_fatigue >= threshold:
            delivered = "auto"
        else:
            delivered = "suggest"
        self._session_count += 1
        if delivered == "auto":
            self.last_exec[arm] = now
        return Decision(arm=arm, candidates=cands, excluded=excluded, source="bandit", scores=scores,
                        delivered_gate=delivered, auto_threshold=threshold,
                        explanation=self._explain(sit, arm, delivered, gate_s, excluded),
                        ventilate_minutes=self.ventilate_minutes(sit) if arm == "ventilate_suggest" else None,
                        **base)

    # ── 학습 ─────────────────────────────────────────────────
    def feedback(self, decision: Decision, verdict: str, *, now: float | None = None) -> float:
        """수용도 축 갱신. accept/reject/timeout/undo/auto_kept. 되돌리기는 자동 문턱을 올린다."""
        if verdict not in VERDICTS:
            raise ValueError(f"알 수 없는 verdict: {verdict!r}")
        if decision.arm is None:
            return 0.0
        arm = decision.arm
        r = REWARD_ACCEPT[verdict]
        st = self.stats[arm]
        st["n"] += 1
        st[verdict] += 1
        self.n_feedback += 1
        g = self.cfg["gate"]
        if verdict == "undo":
            self.undo_penalty[arm] = min(float(g["undo_penalty_max"]), self.undo_penalty[arm] + float(g["undo_penalty_step"]))
        elif verdict == "auto_kept":
            self.undo_penalty[arm] = max(0.0, self.undo_penalty[arm] - float(g["undo_penalty_decay"]))
        if verdict == "reject":
            self._rejected.add(arm)
        if verdict in ("accept",) and now is not None:
            self.last_exec[arm] = now
        if verdict in ("reject", "undo", "timeout"):
            self._session_results.append(False)
            self._pending_effect[id(decision)] = False
        else:
            self._pending_effect[id(decision)] = True
        if decision.source == "bandit":
            self.models[arm]["accept"].update(decision.x, r)
        return r

    def outcome(self, decision: Decision, c_fatigue_after: float | None, *, status: str = "measured") -> float | None:
        """효과 축 갱신. status != measured 면 축은 건드리지 않고 None."""
        if decision.arm is None or status != "measured" or c_fatigue_after is None:
            return None
        r = reward_effect(decision.c_fatigue_before, float(c_fatigue_after), float(self.cfg["bandit"]["effect_full_drop"]))
        st = self.stats[decision.arm]
        st["effect_sum"] += r
        st["effect_n"] += 1
        key = id(decision)
        if self._pending_effect.pop(key, True):
            # 피드백이 '무효' 로 이미 적혔으면 그대로, 아니면 효과 부호로 유효 여부 기록
            self._session_results.append(r > 0.0)
        if decision.source == "bandit":
            self.models[decision.arm]["effect"].update(decision.x, r)
        return r

    def combined_reward(self, r_accept: float, r_effect: float | None) -> float:
        b = self.cfg["bandit"]
        if r_effect is None:
            return float(b["weight_accept"]) * r_accept
        return float(b["weight_accept"]) * r_accept + float(b["weight_effect"]) * r_effect

    # ── 설명 ─────────────────────────────────────────────────
    def _label(self, arm: str) -> str:
        return self.arms[arm].label_ko

    def _mode_text(self) -> str:
        return f"{'개인' if self.policy_mode == 'personal' else '기본'} 정책 · 피드백 {self.n_feedback}건"

    def _explain(self, sit: Situation, arm: str, delivered: str, fsm_gate: str, excluded: dict[str, str]) -> str:
        e = self.cfg["explain"]
        parts: list[str] = []
        co2_high = float(e["co2_high_ppm"])
        if self.profile is not None and getattr(self.profile, "co2_sensitivity_ppm", None):
            co2_high = min(co2_high, float(self.profile.co2_sensitivity_ppm))
        if sit.co2_ppm is not None and sit.co2_ppm >= co2_high:
            parts.append(f"CO₂ {int(sit.co2_ppm)} ppm")
            if self.profile is not None and getattr(self.profile, "co2_sensitivity_ppm", None):
                parts.append(f"평소 민감 문턱 {int(self.profile.co2_sensitivity_ppm)} ppm 넘음")
        if sit.lux is not None and sit.lux <= float(self.cfg["features"]["lux_dim_low"]):
            parts.append(f"조도 {int(sit.lux)} lx 어두움")
        if sit.slump_hold_min >= float(e["slump_hold_notable_min"]):
            parts.append(f"{sit.posture_label} {sit.slump_hold_min:.0f}분")
        elif sit.posture_hold_min >= float(e["slump_hold_notable_min"]) and sit.posture_label not in ("upright", "unknown"):
            parts.append(f"{sit.posture_label} {sit.posture_hold_min:.0f}분")
        if sit.drowsy_count_30min >= 1:
            parts.append(f"졸음 {sit.drowsy_count_30min}회")
        if sit.session_elapsed_min >= float(e["session_long_min"]):
            parts.append(f"세션 {sit.session_elapsed_min / 60:.1f} h")
        if sit.after_bedtime:
            parts.append("평소 취침 시각 지남")
        if sit.weather and sit.weather.get("windy") and arm == "ventilate_suggest":
            parts.append("바람 불어 환기 먼저")
        if arm == "ventilate_suggest":
            minutes = self.ventilate_minutes(sit)
            parts.append(f"겨울이라 {minutes}분만" if sit.month in self.cfg["weather"]["winter_months"] else f"환기 {minutes}분")
        st = self.stats[arm]
        answered = st["accept"] + st["reject"] + st["timeout"]
        if answered:
            parts.append(f"{self._label(arm)} 수락 {int(st['accept'])}/{int(answered)}")
        if st["effect_n"]:
            parts.append(f"효과 {st['effect_sum'] / st['effect_n']:+.2f}")
        if fsm_gate == "auto" and delivered == "suggest":
            if st["undo"]:
                parts.append(f"되돌리기 {int(st['undo'])}회라 묻고 진행")
            elif not self.arms[arm].auto:
                parts.append("사용자 행동이 필요해 묻고 진행")
            else:
                parts.append("확신도가 개인 문턱 아래라 묻고 진행")
        if not parts:
            parts.append(self._label(arm))
        parts.append(self._mode_text())
        return " · ".join(parts)

    def _explain_sleep(self, sit: Situation) -> str:
        bed = getattr(self.profile, "usual_bedtime", None) or self.cfg["sleep"]["default_bedtime"]
        return " · ".join([f"평소 취침 {bed} 지남", f"졸음 {sit.drowsy_count_30min}회",
                           f"앞 개입 {self._ineffective_tail()}회 효과 없음", "취침 권유(규칙)", self._mode_text()])

    # ── 저장 ─────────────────────────────────────────────────
    def arm_stats(self) -> dict[str, dict[str, float]]:
        out = {}
        for arm, st in self.stats.items():
            answered = st["accept"] + st["reject"] + st["timeout"]
            out[arm] = {
                **st,
                "accept_rate": st["accept"] / answered if answered else None,
                "undo_rate": st["undo"] / (st["undo"] + st["auto_kept"]) if (st["undo"] + st["auto_kept"]) else None,
                "effect_mean": st["effect_sum"] / st["effect_n"] if st["effect_n"] else None,
                "auto_threshold": self.auto_threshold(arm),
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": "personalize_policy", "schema_version": "1.0",
            "n_feedback": self.n_feedback,
            "stats": self.stats, "undo_penalty": self.undo_penalty,
            "models": {arm: {axis: m.to_dict() for axis, m in axes.items()} for arm, axes in self.models.items()},
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        if data.get("schema_name") != "personalize_policy":
            raise ValueError("personalize_policy 파일이 아니다")
        for arm, axes in data.get("models", {}).items():
            if arm not in self.models:
                continue
            for axis, md in axes.items():
                self.models[arm][axis].load(md)
        for arm, st in data.get("stats", {}).items():
            if arm in self.stats:
                self.stats[arm].update(st)
        for arm, p in data.get("undo_penalty", {}).items():
            if arm in self.undo_penalty:
                self.undo_penalty[arm] = float(p)
        self.n_feedback = int(data.get("n_feedback", 0))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str, cfg: dict[str, Any] | None = None, **kw: Any) -> "InterventionPolicy":
        """손상된 파일이면 전역 prior 로 시작한다(예외를 삼키고 fresh 반환)."""
        policy = cls(cfg, **kw)
        try:
            with open(path, encoding="utf-8") as fh:
                policy.load_dict(json.load(fh))
        except (OSError, ValueError, KeyError, TypeError):
            return cls(cfg, **kw)
        return policy
