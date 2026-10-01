"""사용자 응답을 라벨로 보존하고 세션 지표를 계산한다.

라벨은 FSM 판정을 바꾸지 않는다. 기록 실패도 라이브 루프에 전파하지 않는다.
"""
from __future__ import annotations

import json
import os
from statistics import median
from typing import Any, Callable, Iterable


def make_label(*, label_id: str, ts: float, verdict: str, request_id: str | None,
               kind: str, predicted_state: str, cause: str | None, gate: str | None,
               c_fatigue: float | None, c_focus: float | None, score_period_sec: float,
               corrected_state: str | None = None, response_ms: int | None = None,
               request_ts: float | None = None) -> dict[str, Any]:
    """질문 응답은 질문~답변, 자발적 정정은 직전 점수 구간을 대상으로 한다."""
    start = request_ts if request_ts is not None else ts - score_period_sec
    return {
        "label_id": label_id, "ts": ts, "source": "display", "request_id": request_id,
        "kind": kind, "verdict": verdict, "predicted_state": predicted_state,
        "cause": cause, "gate": gate, "c_fatigue": c_fatigue, "c_focus": c_focus,
        "confidence": max(c_fatigue, c_focus) if c_fatigue is not None and c_focus is not None else None,
        "corrected_state": corrected_state if verdict == "correct" else None,
        "response_ms": response_ms,
        "answer_code": f"correct:{corrected_state}" if verdict == "correct" else verdict,
        "target_window_start_ms": int(start * 1000), "target_window_end_ms": int(ts * 1000),
    }


def write_label(record: dict[str, Any], path: str, *, on_log: Callable[[str], None] | None = None) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        if on_log is not None:
            on_log(f"[esm] 라벨 기록 실패: {exc}")
        return False


def calculate_metrics(records: Iterable[dict[str, Any]], *, episodes: Iterable[Any] = (),
                      duration_s: float | None = None,
                      fatigue_episodes: Iterable[Any] = ()) -> dict[str, int | float | None]:
    """분모가 없는 비율과 관측할 수 없는 회복 시간은 null 로 둔다."""
    labels = list(records)
    suggestions = [r for r in labels if str(r.get("kind", "")).endswith("_suggest")
                   and r.get("verdict") in ("accept", "reject", "timeout")]
    accepted = sum(r["verdict"] == "accept" for r in suggestions)
    timed_out = sum(r["verdict"] == "timeout" for r in suggestions)
    control = list(episodes)
    auto = [e for e in control if getattr(getattr(e, "gate", None), "value", None) == "auto"
            and getattr(e, "executed", False)]
    corrections = sum(r.get("verdict") == "correct" for r in labels)
    responses = [r["response_ms"] for r in labels if isinstance(r.get("response_ms"), (int, float))]
    recovery = []
    for fatigue in fatigue_episodes:
        if fatigue.t_resolved is None:
            continue
        for intervention in fatigue.interventions:
            if intervention.cause is None:
                continue
            # 제안 뒤 늦게 실행된 환경 제어는 ACTION 진입보다 실제 명령 시각이 기준이다.
            matched = [e.executed_ts for e in control
                       if getattr(e, "executed_ts", None) is not None
                       and e.cause == intervention.cause and e.started_ts >= intervention.t
                       and e.executed_ts <= fatigue.t_resolved]
            executed_at = min(matched) if matched else intervention.t
            if executed_at <= fatigue.t_resolved:
                recovery.append(fatigue.t_resolved - executed_at)
    return {
        "suggest_accept_rate": accepted / len(suggestions) if suggestions else None,
        "timeout_rate": timed_out / len(suggestions) if suggestions else None,
        "auto_undo_rate": sum(e.outcome == "undone" for e in auto) / len(auto) if auto else None,
        "correction_rate": corrections * 3600 / duration_s if duration_s and duration_s > 0 else None,
        "correction_count": corrections,
        "median_response_ms": median(responses) if responses else None,
        "recovery_time_s": median(recovery) if recovery else None,
    }
