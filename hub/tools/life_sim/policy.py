"""§B-2 간이 개인화 정책 — feat/personalize-policy(컨텍스트 밴딧 패키지, 미push) 가 오기 전의 대역.

hub 의 FSM/게이트는 건드리지 않는다. hub 가 ACTION_* 에 들어와 "개입하겠다" 고 하면, 이 정책이
  (1) 세션 상한·쿨다운·거절 재제안 금지 를 적용해 개입을 통과/보류하고,
  (2) 채널(화면/무드등/TTS, 선풍기/조명/창문/공기청정기)을 프로필 점수로 고르고,
  (3) 자동 문턱(되돌리기마다 +0.05, 2 회 뒤 자동→제안) 으로 hub 게이트를 **낮추기만** 하며(올리지 않는다),
  (4) 결과(수락/무응답/거절/되돌리기 + 10 분 피로 추세)로 점수를 갱신하고 "왜 이 개입인지" 한 줄을 만든다.
프로필은 dict(JSON) 하나: 계정 A 는 합성 30 일 학습 프로필(synth_profile), 계정 B 는 전역 기본값. 모든 수치는 policy_simple.yaml.
"""
from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass, field
from typing import Any

GROUP_OF = {"posture_auto": "posture", "posture_suggest": "posture", "break_auto": "break", "break_suggest": "break",
            "env_auto": "env", "env_suggest": "env", "rest_forced": "break"}


@dataclass
class Decision:
    allow: bool
    gate: str                      # auto | suggest | none (hub 게이트를 낮춘 결과)
    channels: list[str]            # 지금 사람에게 닿는 채널(자동이면 실행 채널, 제안이면 카드)
    why: str
    accept_channels: list[str] = field(default_factory=list)   # 제안을 수락했을 때 닿는 채널
    reason_codes: list[str] = field(default_factory=list)
    kind: str = ""
    group: str = ""
    channel: str = ""
    t: float = 0.0
    outcome: str | None = None     # accept | none | reject | undo
    reward: float = 0.0
    effect: float | None = None
    c_fatigue_at: float = 0.0


class SimplePolicy:
    def __init__(self, cfg: dict[str, Any], *, profile: dict[str, Any] | None = None, seed: int = 0, name: str = "B_global"):
        self.c = cfg
        self.name = name
        self.rnd = random.Random(seed)
        self.scores: dict[str, dict[str, float]] = {g: dict(v) for g, v in (cfg.get("channel_scores") or {}).items()}
        self.auto_threshold = float((cfg.get("gate") or {}).get("auto_threshold", 0.75))
        self.undos = 0
        self.rejected_groups: set[str] = set()
        self.last_t: dict[str, float] = {}
        self.count = 0
        self.decisions: list[Decision] = []
        self.log: list[dict[str, Any]] = []
        self.profile = dict(profile or {})
        if self.profile:
            for g, table in (self.profile.get("channel_scores") or {}).items():
                self.scores.setdefault(g, {}).update({k: float(v) for k, v in table.items()})
            self.auto_threshold = float(self.profile.get("auto_threshold", self.auto_threshold))
            self.rejected_groups |= set(self.profile.get("blocked_groups") or [])

    # ---------------------------------------------------------------- 결정
    def decide(self, ev: dict[str, Any], now: float, hub_channels: list[str], *, drowsy_episodes: int,
               failed_interventions: int) -> Decision:
        kind = ev["kind"]
        group = GROUP_OF.get(kind, kind.split("_")[0])
        gate = str(ev.get("gate") or "none")
        codes: list[str] = []
        g = self.c.get("gate") or {}
        # 1) 상한·쿨다운·거절 재제안 금지
        if self.count >= int(self.c.get("per_session_cap", 99)):
            return self._deny(ev, now, group, "session_cap")
        cd = float(self.c.get("cooldown_s", 0))
        if group in self.last_t and now - self.last_t[group] < cd:
            return self._deny(ev, now, group, f"cooldown({now - self.last_t[group]:.0f}s<{cd:.0f}s)")
        if (self.c.get("reject_block") or {}).get("enabled") and group in self.rejected_groups:
            return self._deny(ev, now, group, "rejected_before_no_resuggest")
        # 2) 자동 문턱: 되돌리기 누적으로 올라간 문턱보다 피로가 낮으면 자동 → 제안
        c_fat = float(ev.get("c_fatigue") or 0.0)
        if gate == "auto":
            if self.undos >= int(g.get("demote_after_undos", 2)):
                gate, codes = "suggest", codes + ["auto_demoted_after_undos"]
            elif c_fat < self.auto_threshold:
                gate, codes = "suggest", codes + [f"below_auto_threshold({self.auto_threshold:.2f})"]
        # 3) 채널 선택(프로필 점수, 가끔 탐색)
        table = self.scores.get(group) or {}
        if hub_channels and not table:
            channel, score = hub_channels[0], 0.5
        elif table:
            if self.rnd.random() < float(self.c.get("explore_p", 0.0)):
                channel, codes = self.rnd.choice(list(table)), codes + ["explore"]
            else:
                channel = max(table, key=table.get)
            score = table[channel]
        else:
            channel, score = "", 0.5
        # 채널 묶음: 화면은 항상, 학습된 채널 하나를 더한다(전역 기본값은 점수가 같아 화면만 남는다 → 개인화 효과가 여기서 난다).
        # env 는 hub 가 명령(선풍기·조명)을 내므로 hub 채널을 그대로 두고, 선택 채널은 환기 카드/공기청정기 결정에만 쓴다.
        if kind == "rest_forced":                      # 강제 휴식 권유(hub 상태 전이, 카드 아님): hub 매핑 + 학습 채널
            extra = [channel] if channel and channel not in hub_channels and channel not in ("screen_notice", "screen_card") else []
            channels, accept = list(hub_channels) + extra, []
        elif group in ("posture", "break"):
            extra = [channel] if channel and channel not in ("screen_notice", "screen_card") else []
            if gate == "auto":
                channels, accept = ["screen_notice"] + extra, []
            else:
                channels, accept = ["screen_card"], ["screen_notice"] + extra
        else:
            channels, accept = list(hub_channels), []
        reason = ev.get("cause") or group
        why = str(self.c.get("why_template", "{kind}: {reason}")).format(kind=kind, reason=reason, profile=self.name,
                                                                           channel=channel or ",".join(channels),
                                                                           score=score, threshold=self.auto_threshold)
        d = Decision(True, gate, channels, why, reason_codes=codes, accept_channels=accept, kind=kind, group=group, channel=channel, t=now,
                     c_fatigue_at=c_fat)
        self.count += 1
        self.last_t[group] = now
        self.decisions.append(d)
        self.log.append({"t": now, "decision": "allow", "kind": kind, "gate_in": ev.get("gate"), "gate_out": gate,
                         "channel": channel, "why": why, "codes": codes})
        return d

    def _deny(self, ev, now, group, code) -> Decision:
        d = Decision(False, "none", [], f"{ev['kind']}: 보류 — {code} (프로필 {self.name})", reason_codes=[code], kind=ev["kind"], group=group, t=now)
        self.log.append({"t": now, "decision": "deny", "kind": ev["kind"], "codes": [code]})
        return d

    # ---------------------------------------------------------------- 보상
    def outcome(self, d: Decision, outcome: str, now: float) -> None:
        r = self.c.get("reward") or {}
        d.outcome = outcome
        d.reward += float(r.get(outcome, 0.0))
        if outcome == "undo":
            self.undos += 1
            g = self.c.get("gate") or {}
            self.auto_threshold = min(float(g.get("max_auto_threshold", 0.95)), self.auto_threshold + float(g.get("undo_step", 0.05)))
        if outcome == "reject" and (self.c.get("reject_block") or {}).get("enabled"):
            self.rejected_groups.add(d.group)
        self._learn(d)
        self.log.append({"t": now, "outcome": outcome, "kind": d.kind, "channel": d.channel, "reward": round(d.reward, 2),
                         "auto_threshold": round(self.auto_threshold, 2), "undos": self.undos})

    def effect(self, d: Decision, c_fat_start: float, c_fat_end: float, now: float) -> None:
        r = self.c.get("reward") or {}
        eff = max(-1.0, min(1.0, -(c_fat_end - c_fat_start) * float(r.get("effect_scale", 2.0))))
        d.effect = round(eff, 3)
        d.reward += eff
        self._learn(d)
        self.log.append({"t": now, "effect": d.effect, "kind": d.kind, "channel": d.channel, "reward": round(d.reward, 2)})

    def _learn(self, d: Decision) -> None:
        if not d.channel:
            return
        lr = float(self.c.get("learning_rate", 0.3))
        table = self.scores.setdefault(d.group, {})
        cur = table.get(d.channel, 0.5)
        target = 0.5 + 0.25 * max(-2.0, min(2.0, d.reward))
        table[d.channel] = round(cur + lr * (target - cur), 4)

    # ---------------------------------------------------------------- 취침 권유 규칙
    def bedtime_due(self, now: float, c_fat: float, drowsy_episodes: int, failed_interventions: int) -> tuple[bool, str]:
        b = self.c.get("bedtime") or {}
        if not b.get("enabled"):
            return False, "disabled"
        hh, mm = (int(x) for x in str(self.profile.get("usual_bedtime", b.get("usual_bedtime", "23:30"))).split(":")[:2])
        local = _dt.datetime.fromtimestamp(now)
        after = (local.hour, local.minute) >= (hh, mm) or local.hour < 4
        conds = {"after_bedtime": after, "fatigue_high": c_fat >= float(b.get("fatigue_min", 0.7)),
                 "drowsy_repeated": drowsy_episodes >= int(b.get("drowsy_episodes_min", 2)),
                 "prior_failed": failed_interventions >= int(b.get("failed_interventions_min", 2))}
        ok = all(conds.values())
        return ok, ",".join(k for k, v in conds.items() if v) or "none"

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "auto_threshold": round(self.auto_threshold, 3), "undos": self.undos,
                "rejected_groups": sorted(self.rejected_groups), "count": self.count,
                "scores": {g: {k: round(v, 3) for k, v in t.items()} for g, t in self.scores.items()}}


def synth_profile(persona: str, days: int = 30, seed: int = 0) -> dict[str, Any]:
    """계정 A 의 '합성 30 일 학습 프로필' — 페르소나별 선호를 보상 누적으로 흉내 낸 채널 점수(측정이 아니라 가정)."""
    rnd = random.Random(seed)
    base = {"student": {"posture": {"screen_notice": 0.55, "lamp_cool": 0.6, "tts": 0.8}, "env": {"vent_fan": 0.55, "desk_lamp": 0.6,
                                                                                               "window": 0.7, "air_purifier": 0.4},
                        "break": {"screen_notice": 0.5, "tts": 0.6}},
            "developer": {"posture": {"screen_notice": 0.6, "lamp_cool": 0.7, "tts": 0.3}, "env": {"vent_fan": 0.3, "desk_lamp": 0.7,
                                                                                                 "window": 0.6, "air_purifier": 0.6},
                          "break": {"screen_notice": 0.6, "tts": 0.3}},
            "examinee": {"posture": {"screen_notice": 0.5, "lamp_cool": 0.75, "tts": 0.7}, "env": {"vent_fan": 0.5, "desk_lamp": 0.7,
                                                                                                 "window": 0.65, "air_purifier": 0.5},
                         "break": {"screen_notice": 0.6, "tts": 0.4}}}.get(persona) or {}
    scores = {g: {k: round(min(0.95, max(0.05, v + rnd.gauss(0, 0.03))), 3) for k, v in t.items()} for g, t in base.items()}
    return {"persona": persona, "days": days, "channel_scores": scores,
            "auto_threshold": {"student": 0.75, "developer": 0.80, "examinee": 0.75}.get(persona, 0.75),
            "usual_bedtime": {"student": "01:00", "developer": "00:30", "examinee": "23:30"}.get(persona, "23:30"),
            "blocked_groups": ["break"] if persona == "examinee" else []}
