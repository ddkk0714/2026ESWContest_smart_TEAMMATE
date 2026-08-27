"""세션 로그·작업 리포트 — FSM 실행을 관찰해 요약을 만든다. 담당: 박소연.

엔진을 순수하게 두기 위해 관찰자(observer)로 분리했다. 각 tick 의 (frame, result)를
observe() 로 먹이면 상태 체류시간·피로 에피소드·개입 결과·ESM 라벨을 누적하고,
finalize() 로 SessionReport 를 낸다. 사용자에게 보여줄 '작업 패턴 리포트'의 기반이며,
ESM 라벨은 2단계 분류기(조명희) 학습 데이터로도 쓰인다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .engine import FSMEngine
from .states import FOCUS_STATES, State
from .types import SensorFrame, TickResult

_FOCUS = {s.value for s in FOCUS_STATES}
_ACTIONS = {State.ACTION_ENV.value, State.ACTION_POSTURE.value, State.ACTION_BREAK.value}


@dataclass
class Intervention:
    t: float
    cause: str | None
    action: str
    outcome: str | None = None      # recovered / escalated / rejected / None(진행중)
    accepted: bool | None = None    # 휴식 권유 수락 여부


@dataclass
class FatigueEpisode:
    t_onset: float
    peak_fatigue: float = 0.0
    t_resolved: float | None = None
    interventions: list[Intervention] = field(default_factory=list)


@dataclass
class SessionReport:
    t_start: float | None = None
    t_end: float | None = None
    ticks: int = 0
    durations: dict[str, float] = field(default_factory=dict)   # 상태 → 체류 초
    episodes: list[FatigueEpisode] = field(default_factory=list)
    interventions: list[Intervention] = field(default_factory=list)
    esm_labels: list[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.t_start is None or self.t_end is None:
            return 0.0
        return self.t_end - self.t_start

    @property
    def focus_time(self) -> float:
        return sum(v for k, v in self.durations.items() if k in _FOCUS)


class SessionRecorder:
    """tick 스트림을 관찰해 SessionReport 를 누적한다."""

    def __init__(self) -> None:
        self.r = SessionReport()
        self._pstate: str | None = None
        self._pt: float | None = None
        self._episode: FatigueEpisode | None = None
        self._iv: Intervention | None = None

    def observe(self, frame: SensorFrame, result: TickResult) -> None:
        now = frame.now
        st = result.state.value
        self.r.ticks += 1

        # 직전 상태 체류시간 누적
        if self._pstate is not None and self._pt is not None:
            self.r.durations[self._pstate] = self.r.durations.get(self._pstate, 0.0) + (now - self._pt)

        if st == "START" and self.r.t_start is None:
            self.r.t_start = now
        if st == "END":
            self.r.t_end = now

        if st != self._pstate:
            if st == "FATIGUE" and self._episode is None:
                self._episode = FatigueEpisode(t_onset=now)
                self.r.episodes.append(self._episode)
            elif st in _ACTIONS:
                self._iv = Intervention(t=now, cause=result.cause, action=st)
                self.r.interventions.append(self._iv)
                if self._episode is not None:
                    self._episode.interventions.append(self._iv)
            elif st == "ESCALATE" and self._iv is not None and self._iv.outcome is None:
                self._iv.outcome = "escalated"
            elif self._pstate == "RECOVERY" and st in _FOCUS:      # 회복 승인
                if self._iv is not None and self._iv.outcome is None:
                    self._iv.outcome = "recovered"
                if self._episode is not None:
                    self._episode.t_resolved = now
                self._episode = None

        if self._episode is not None:
            self._episode.peak_fatigue = max(self._episode.peak_fatigue, result.scores.c_fatigue)

        # ESM 라벨 (휴식 수락 → REST / 거절)
        if "rest_timer.start" in result.actions:
            self.r.esm_labels.append({"t": now, "label": "break_accept"})
            if self._iv is not None:
                self._iv.accepted = True
        if "esm_label:reject" in result.actions:
            self.r.esm_labels.append({"t": now, "label": "break_reject"})
            if self._iv is not None:
                self._iv.accepted = False
                if self._iv.outcome is None:
                    self._iv.outcome = "rejected"

        self._pstate = st
        self._pt = now

    def finalize(self) -> SessionReport:
        return self.r


def build_report(
    frames: Iterable[SensorFrame], cfg: dict | None = None, engine: FSMEngine | None = None
) -> SessionReport:
    engine = engine or FSMEngine(cfg)
    rec = SessionRecorder()
    for f in frames:
        rec.observe(f, engine.tick(f))
    return rec.finalize()


def format_session_report(r: SessionReport) -> str:
    lines = ["=== 세션 리포트 ==="]
    dur = r.duration
    lines.append(f"작업 시간: {dur:.0f}s ({dur / 60:.1f}분)")
    if dur > 0:
        ft = r.focus_time
        lines.append(f"몰입 시간: {ft:.0f}s ({100 * ft / dur:.0f}%)")
    lines.append(f"피로 에피소드: {len(r.episodes)}회")
    for i, ep in enumerate(r.episodes, 1):
        res = f"resolved={ep.t_resolved:.0f}" if ep.t_resolved is not None else "미해소"
        lines.append(
            f"  #{i} onset={ep.t_onset:.0f} peak={ep.peak_fatigue:.2f} {res}"
            f" (개입 {len(ep.interventions)}회)"
        )
    lines.append(f"개입: {len(r.interventions)}건")
    for iv in r.interventions:
        lines.append(f"  - t={iv.t:.0f} {iv.cause} → {iv.action} → {iv.outcome or '진행중'}")
    lines.append(f"ESM 라벨: {len(r.esm_labels)}건")
    if r.durations:
        top = sorted(r.durations.items(), key=lambda kv: -kv[1])
        lines.append("상태별 체류: " + ", ".join(f"{k} {v:.0f}s" for k, v in top))
    return "\n".join(lines)
