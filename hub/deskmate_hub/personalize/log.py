"""개인화 로그 JSONL — docs/personalization-log.md.

에피소드는 결정 → 피드백 → 10 분 뒤 결과까지 메모리에 들고 있다가 한 줄로 쓴다.
세션 요약(personalize_session)은 END 때 한 줄. 기록 실패는 라이브 루프에 전파하지 않는다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .features import Situation
from .policy import Decision, reward_effect

EPISODE_SCHEMA = "personalize_episode"
SESSION_SCHEMA = "personalize_session"
SCHEMA_VERSION = "1.0"


def local_iso(ts: float, utc_offset_hours: float) -> str:
    tz = timezone(timedelta(hours=utc_offset_hours))
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


def build_episode(*, episode_id: str, session_id: str, profile_id: str, ts: float, utc_offset_hours: float,
                  situation: Situation, fsm: dict[str, Any], decision: Decision | dict[str, Any],
                  feedback: dict[str, Any] | None = None, outcome: dict[str, Any] | None = None,
                  simulated: bool = False, persona: str | None = None) -> dict[str, Any]:
    return {
        "schema_name": EPISODE_SCHEMA, "schema_version": SCHEMA_VERSION,
        "simulated": simulated, "persona": persona,
        "episode_id": episode_id, "session_id": session_id, "profile_id": profile_id,
        "ts_ms": int(ts * 1000), "local_time": local_iso(ts, utc_offset_hours),
        "features": situation.to_dict(),
        "fsm": dict(fsm),
        "decision": decision.to_dict() if isinstance(decision, Decision) else dict(decision),
        "feedback": feedback, "outcome": outcome,
    }


def build_outcome(*, status: str, c_before: float, c_after: float | None, reward_accept: float,
                  full_drop: float, weight_accept: float, weight_effect: float,
                  posture_recovered: bool | None = None, next_state: str | None = None) -> dict[str, Any]:
    measured = status == "measured" and c_after is not None
    r_eff = reward_effect(c_before, c_after, full_drop) if measured else None
    reward = weight_accept * reward_accept + (weight_effect * r_eff if r_eff is not None else 0.0)
    return {
        "status": status, "c_fatigue_before": round(c_before, 4),
        "c_fatigue_after": round(c_after, 4) if measured else None,
        "trend": round(c_after - c_before, 4) if measured else None,
        "posture_recovered": posture_recovered, "next_state": next_state,
        "reward_accept": reward_accept, "reward_effect": round(r_eff, 4) if r_eff is not None else None,
        "reward": round(reward, 4),
    }


class PersonalizeLogger:
    def __init__(self, path: str, *, boot_id: str, cfg: dict[str, Any], profile_id: str | None = None,
                 simulated: bool = False, persona: str | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.path = path
        self.boot_id = boot_id
        self.cfg = cfg
        self.profile_id = profile_id or cfg.get("profile_id", "default")
        self.simulated = simulated
        self.persona = persona
        self.on_log = on_log
        self.offset = float(cfg.get("local_utc_offset_hours", 9))
        self._seq = 0
        self._open: dict[str, dict[str, Any]] = {}
        self._decisions: dict[str, Decision] = {}

    # ── 에피소드 ───────────────────────────────────────────────
    def open_episode(self, *, session_id: str, situation: Situation, fsm: dict[str, Any],
                     decision: Decision, ts: float) -> str:
        self._seq += 1
        eid = f"{self.boot_id}-pe{self._seq}"
        self._open[eid] = build_episode(
            episode_id=eid, session_id=session_id, profile_id=self.profile_id, ts=ts,
            utc_offset_hours=self.offset, situation=situation, fsm=fsm, decision=decision,
            simulated=self.simulated, persona=self.persona)
        self._decisions[eid] = decision
        return eid

    def feedback(self, episode_id: str, verdict: str, *, ts: float, response_ms: int | None = None,
                 request_id: str | None = None) -> None:
        rec = self._open.get(episode_id)
        if rec is None:
            return
        rec["feedback"] = {"verdict": verdict, "response_ms": response_ms,
                           "feedback_ts_ms": int(ts * 1000), "request_id": request_id}

    def close(self, episode_id: str, *, status: str, c_fatigue_after: float | None = None,
              reward_accept: float = 0.0, posture_recovered: bool | None = None,
              next_state: str | None = None) -> dict[str, Any] | None:
        rec = self._open.pop(episode_id, None)
        self._decisions.pop(episode_id, None)
        if rec is None:
            return None
        b = self.cfg["bandit"]
        rec["outcome"] = build_outcome(
            status=status, c_before=float(rec["features"]["c_fatigue"]), c_after=c_fatigue_after,
            reward_accept=reward_accept, full_drop=float(b["effect_full_drop"]),
            weight_accept=float(b["weight_accept"]), weight_effect=float(b["weight_effect"]),
            posture_recovered=posture_recovered, next_state=next_state)
        self._write(rec)
        return rec

    def decision_of(self, episode_id: str) -> Decision | None:
        return self._decisions.get(episode_id)

    def due(self, now: float) -> list[str]:
        """effect_window_min 이 지난 열린 에피소드 id (결과를 닫을 차례)."""
        window = float(self.cfg["bandit"]["effect_window_min"]) * 60.0
        return [eid for eid, rec in self._open.items() if now - rec["ts_ms"] / 1000.0 >= window]

    def flush(self, status: str = "missing") -> int:
        n = 0
        for eid in list(self._open):
            self.close(eid, status=status)
            n += 1
        return n

    # ── 세션 요약 ──────────────────────────────────────────────
    def session_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        rec = {"schema_name": SESSION_SCHEMA, "schema_version": SCHEMA_VERSION,
               "simulated": self.simulated, "persona": self.persona, "profile_id": self.profile_id, **record}
        self._write(rec)
        return rec

    def _write(self, record: dict[str, Any]) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except OSError as exc:
            if self.on_log:
                self.on_log(f"[personalize] 로그 기록 실패: {exc}")
            return False


def read_records(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_records(path: str, records: Iterable[dict[str, Any]]) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n
