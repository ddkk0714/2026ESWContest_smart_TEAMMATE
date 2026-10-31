"""제어 진행 로그 — `control/cmd` 로 나간 명령 하나(command_id)마다 "왜 → 무엇을 → 어떻게 됐나" 를 한 레코드로 남긴다 (2026-10-31).

시연 중·뒤에 "방금 왜 안 켜졌지" 를 보려고 만든다. 화면 앱·devview 의 명령 로그는 MQTT 줄을 받은 그대로 보여 주고 메모리에만 있어
원인·쿨다운 생략·시간 초과·되돌림이 한 줄로 안 묶인다. 이 로그는 hub 가 아는 단계를 그대로 싣는다.

입력
- 디스패처(ControlDispatcher.on_record): 계획(제안 대기·쿨다운/비가역/게이트 none 생략) → 발행 → 결과·시간 초과 → 거절·만료·되돌림.
- 디스패처를 거치지 않는 명령(rest_notice·arm_notice 의 speaker speak/chime): live.py 가 publish 를 감싸 record_published,
  결과는 on_result, 시간 초과는 tick 이 맡는다.
- 결과의 accepted·executing 은 중간 상태다(event=progress, status 는 executing 그대로, completed_ts 없음).
  레코드의 status=accepted 는 "제안 대기"(디스패처가 아직 안 보낸 명령) 뜻이라 기기의 accepted 를 그 이름으로 싣지 않는다.

출력(레코드가 바뀔 때마다 그 레코드 전체 스냅샷)
- JSONL 파일(control.yaml `control_log.path`, 없으면 logs/control-<boot_id>.jsonl). 같은 command_id 가 여러 줄 — 마지막 줄이 최종.
- sink 콜백(보드: service_bridge 가 `CTLLOG\t<json>` 줄 → C++ 가 최근 N건을 `GET /api/control-log` 로 내준다).
- recent(): 메모리의 최근 max_records 건(같은 command_id 는 한 건).

레코드 필드: v, ts(이 스냅샷 시각), command_id, source(dispatcher|rest_notice|arm_notice), event, status, reason,
state, cause, gate, arm, target_id, operation, value, created_ts, sent_ts, completed_ts, latency_ms,
error_code, node, previous, actual, actual_value, recovery, undo(되돌리기 명령인지), undo_of, undone_by.
표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from typing import Any, Callable

from .dispatcher import IN_PROGRESS

Sink = Callable[[dict[str, Any]], None]

DEFAULT_DIR = "logs"         # path 가 없을 때 쓰는 폴더(esm 로그와 같은 곳). 테스트는 conftest 가 tmp 로 돌린다

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "path": None,            # None = <DEFAULT_DIR>/control-<boot_id>.jsonl
    "max_records": 200,      # 메모리(recent)에 둘 명령 수. C++ 링버퍼도 200
}

TERMINAL = ("succeeded", "failed", "timeout", "cancelled", "skipped")
# control/result 에서 레코드로 옮기는 필드
_RESULT_FIELDS = ("error_code", "node", "previous", "actual", "actual_value", "recovery")


class ControlLog:
    def __init__(self, cfg: dict[str, Any] | None, *, boot_id: str, result_timeout_sec: float = 15.0,
                 sink: Sink | None = None, on_log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.c = dict(DEFAULTS, **(cfg or {}))
        self.enabled = bool(self.c.get("enabled", True))
        self.path = self.c.get("path") or os.path.join(DEFAULT_DIR, f"control-{boot_id}.jsonl")
        self.max_records = max(1, int(self.c.get("max_records") or DEFAULTS["max_records"]))
        self.timeout = float(result_timeout_sec)
        self.sink = sink
        self._log = on_log or (lambda m: None)
        self._clock = clock
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._file_ok = True
        self.counts = {"records": 0, "writes": 0, "write_errors": 0}

    # ---------------------------------------------------------------- 디스패처
    def record_command(self, cmd: Any, event: str, *, reason: str | None = None, undo_of: str | None = None,
                       undone_by: str | None = None, now: float | None = None) -> dict[str, Any] | None:
        """ControlDispatcher 의 ControlCommand 스냅샷(ControlDispatcher on_record 훅).

        event: awaiting_user|sent|progress|result|late_result|timeout|skipped|undone. undone_by = 이 명령을 되돌린 명령 id.
        """
        if not self.enabled:
            return None
        now = self._clock() if now is None else now
        rec = self._get(cmd.command_id, now, source="dispatcher")
        rec.update(
            state=cmd.origin_state, cause=cmd.cause, gate=cmd.gate, target_id=cmd.target_id,
            operation=cmd.operation, value=cmd.value, status=cmd.status, sent_ts=cmd.sent_ts,
            completed_ts=cmd.completed_ts, undo=cmd.gate == "undo",
        )
        if cmd.status == "accepted" and cmd.sent_ts is not None:
            # 보낸 뒤 기기가 accepted 를 알린 명령 — 디스패처는 그 이름을 그대로 두지만 이 로그의 accepted 는 "제안 대기"다
            rec["status"] = "executing"
        if getattr(cmd, "arm", None) is not None:
            rec["arm"] = cmd.arm
        if undone_by is not None:
            rec["undone_by"] = undone_by
        if undo_of is not None:
            rec["undo_of"] = undo_of
        if reason is not None:
            rec["reason"] = reason
        self._merge_result(rec, cmd.result)
        if getattr(cmd, "error_code", None) and cmd.error_code not in ("result_timeout", "result_expired"):
            # 디스패처가 정한 코드(device_state_unconfirmed·actual_value_unconfirmed 등). 시간 초과는 reason 이 말한다
            rec["error_code"] = cmd.error_code
        return self._emit(rec, event, now)

    # ---------------------------------------------------------------- 디스패처 밖 명령
    def record_published(self, payload: dict[str, Any], source: str, *, arm: str | None = None,
                         now: float | None = None) -> dict[str, Any] | None:
        """rest_notice·arm_notice 가 직접 낸 control/cmd payload."""
        if not self.enabled or not payload.get("command_id"):
            return None
        now = self._clock() if now is None else now
        rec = self._get(str(payload["command_id"]), now, source=source)
        rec.update(
            state=payload.get("origin_state"), cause=payload.get("cause"), gate=payload.get("gate"),
            target_id=payload.get("target_id"), operation=payload.get("operation"), value=payload.get("value"),
            status="executing", sent_ts=now, undo=False,
        )
        if arm is not None:
            rec["arm"] = arm
        return self._emit(rec, "sent", now)

    def on_result(self, payload: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
        """`control/result` 수신. 디스패처 명령은 디스패처 훅이 상태를 정하므로 여기서는 디스패처 밖 명령만 다룬다."""
        if not self.enabled:
            return None
        rec = self._records.get(str(payload.get("command_id")))
        if rec is None or rec.get("source") == "dispatcher":
            return None
        now = self._clock() if now is None else now
        status = str(payload.get("status", "")).lower()
        if rec.get("status") in TERMINAL:
            self._merge_result(rec, payload)
            return self._emit(rec, "late_result", now)
        self._merge_result(rec, payload)
        if status in IN_PROGRESS:
            # 기기가 받았다·하는 중 — 끝이 아니다(completed_ts 없음). 최종 결과나 tick 의 시간 초과가 닫는다
            return self._emit(rec, "progress", now)
        rec["status"], rec["completed_ts"] = (status if status in TERMINAL else "failed"), now
        if status not in TERMINAL:
            rec["reason"] = f"unknown_status:{status}"
        return self._emit(rec, "result", now)

    def tick(self, now: float | None = None) -> None:
        """디스패처 밖 명령의 결과 시간 초과(디스패처 명령은 디스패처 tick 이 판정해 훅으로 온다)."""
        if not self.enabled:
            return
        now = self._clock() if now is None else now
        for rec in list(self._records.values()):
            if (rec.get("source") != "dispatcher" and rec.get("status") == "executing"
                    and rec.get("sent_ts") is not None and now - rec["sent_ts"] >= self.timeout):
                rec["status"], rec["completed_ts"] = "timeout", now
                rec["reason"] = "no_result"
                self._emit(rec, "timeout", now)

    # ---------------------------------------------------------------- 조회
    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        items = list(self._records.values())
        return [dict(r) for r in (items[-limit:] if limit else items)]

    def snapshot(self) -> dict[str, Any]:
        """session/report 용 요약 — 상태별 개수와 마지막 실패."""
        by_status: dict[str, int] = {}
        last_failure = None
        for r in self._records.values():
            by_status[r.get("status") or "?"] = by_status.get(r.get("status") or "?", 0) + 1
            if r.get("status") in ("failed", "timeout"):
                last_failure = {k: r.get(k) for k in ("ts", "target_id", "operation", "status", "error_code", "reason")}
        return {"enabled": self.enabled, "path": self.path if self.enabled else None, "by_status": by_status,
                "last_failure": last_failure, **self.counts}

    # ---------------------------------------------------------------- 내부
    def _get(self, command_id: str, now: float, *, source: str) -> dict[str, Any]:
        rec = self._records.get(command_id)
        if rec is None:
            rec = {"v": 1, "command_id": command_id, "source": source, "created_ts": now}
            self._records[command_id] = rec
            self.counts["records"] += 1
            while len(self._records) > self.max_records:
                self._records.popitem(last=False)
        else:
            self._records.move_to_end(command_id)
        return rec

    @staticmethod
    def _merge_result(rec: dict[str, Any], result: dict[str, Any] | None) -> None:
        for key in _RESULT_FIELDS:
            if result and key in result:
                rec[key] = result[key]

    def _emit(self, rec: dict[str, Any], event: str, now: float) -> dict[str, Any]:
        rec["ts"] = now
        rec["event"] = event
        sent, done = rec.get("sent_ts"), rec.get("completed_ts")
        rec["latency_ms"] = int(round((done - sent) * 1000)) if sent is not None and done is not None else None
        out = dict(rec)
        self._write(out)
        if self.sink is not None:
            try:
                self.sink(out)
            except Exception as exc:  # noqa: BLE001 — 로그 때문에 제어가 멈추면 안 된다
                self._log(f"[control_log] sink 실패: {exc}")
        return out

    def _write(self, rec: dict[str, Any]) -> None:
        if not self._file_ok:
            return
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
            self.counts["writes"] += 1
        except OSError as exc:
            # 한 번 실패하면 파일 기록만 끄고(같은 오류를 매 명령마다 찍지 않게) sink·recent 는 계속한다
            self._file_ok = False
            self.counts["write_errors"] += 1
            self._log(f"[control_log] 파일 기록 끔 — {self.path}: {exc}")
