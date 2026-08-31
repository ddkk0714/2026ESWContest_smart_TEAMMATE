"""로그 리플레이 — JSONL 센서 로그를 FSM 엔진에 흘려보내 전이를 재현한다.

실기기 없이 임계값(config/fsm.yaml)을 튜닝하기 위한 도구다. 한 줄 = 한 tick 의
SensorFrame(JSON). 8월 실측 로그를 이 형식으로 저장하면 반복 실험이 빨라진다.

JSONL 스키마(줄당 하나):
    {"now": 0, "present": true, "pc_ratio": 0.9,
     "signals": {"posture": {"delta": 0.9, "phi": 0.1, "available": true}, ...},
     "touch": true}
누락 필드는 기본값. '#' 로 시작하거나 빈 줄은 주석으로 건너뛴다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .inference import FSMEngine, SensorFrame, Signal


def frame_from_dict(d: dict) -> SensorFrame:
    signals = {k: Signal(**v) for k, v in (d.get("signals") or {}).items()}
    return SensorFrame(
        now=float(d["now"]),
        present=bool(d.get("present", True)),
        pc_ratio=float(d.get("pc_ratio", 0.0)),
        signals=signals,
        touch=bool(d.get("touch", False)),
        end_touch=bool(d.get("end_touch", False)),
        action_done=bool(d.get("action_done", False)),
        break_accepted=d.get("break_accepted"),
    )


def iter_frames(lines: Iterable[str]) -> Iterator[SensorFrame]:
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {i}: invalid JSON: {e}") from e
        yield frame_from_dict(d)


@dataclass
class ReplayResult:
    ticks: int = 0
    transitions: int = 0
    visited: dict[str, int] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)   # 상태가 바뀐 tick 만 기록


def replay(
    frames: Iterable[SensorFrame],
    cfg: dict | None = None,
    engine: FSMEngine | None = None,
) -> ReplayResult:
    engine = engine or FSMEngine(cfg)
    res = ReplayResult()
    prev = engine.state
    for frame in frames:
        r = engine.tick(frame)
        res.ticks += 1
        res.visited[r.state.value] = res.visited.get(r.state.value, 0) + 1
        if r.state is not prev:
            res.transitions += 1
            res.trace.append({
                "t": frame.now,
                "from": prev.value,
                "to": r.state.value,
                "ctx": r.context.value,
                "c_fatigue": round(r.scores.c_fatigue, 3),
                "c_focus": round(r.scores.c_focus, 3),
                "gate": r.gate.value,
                "cause": r.cause,
                "actions": r.actions,
            })
        prev = r.state
    return res


def format_report(res: ReplayResult, quiet: bool = False) -> str:
    lines = ["=== DESKMATE FSM replay ==="]
    lines.append(f"frames: {res.ticks} · transitions: {res.transitions}")
    visited = ", ".join(f"{k}×{v}" for k, v in res.visited.items())
    lines.append(f"상태 방문: {visited}")
    if not quiet:
        lines.append("--- 전이 트레이스 ---")
        for e in res.trace:
            extra = []
            if e["cause"]:
                extra.append(f"cause={e['cause']}")
            if e["gate"] != "none":
                extra.append(f"gate={e['gate']}")
            tail = ("  " + " ".join(extra)) if extra else ""
            acts = (" " + ",".join(e["actions"])) if e["actions"] else ""
            lines.append(
                f"t={e['t']:>6.0f}  {e['from']:>15} → {e['to']:<15}"
                f" [{e['ctx']}] C_fat={e['c_fatigue']:.2f} C_foc={e['c_focus']:.2f}"
                f"{tail}{acts}"
            )
    return "\n".join(lines)
