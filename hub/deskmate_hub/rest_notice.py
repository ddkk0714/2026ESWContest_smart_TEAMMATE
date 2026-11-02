"""REST 진입 알림 — FSM 이 어떤 길로든 REST 에 들어가면 사람에게 말한다 (2026-10-26).

10-26 실기: RECOVERY 중 다시 엎드려 강제 휴식(REST)이 됐는데 허브는 state/phase 만 바꾸고 아무 말도 하지 않았다
("rest 라고 뜨면 뜨기만 할 게 아니라 쉬라고 말을 해 줘야 하는 거 아니야?"). 여기서는 REST 진입마다
  1) `deskmate/interaction/request` kind `rest_notice` — 안내 카드(수락·거절 없음, 닫기만). 문구·휴식 분·이유·만료를 싣는다.
  2) `deskmate/control/cmd` target `speaker` operation `speak` value {text, lang} — Pi 5 앱이 스피커(TTS)로 읽는다.
     사용자 첫 설정 sound_pref 가 off 면 카드만 보낸다.
REST 진입 한 번에 한 번(쿨다운), REST 가 reminder_after_sec 넘게 이어지면 한 번 더. 설정은 control.yaml `rest_notice:`
(시연 프로파일은 `control:` 절로 덮는다). 표준 라이브러리만.
"""
from __future__ import annotations

from typing import Any, Callable

KIND = "rest_notice"
PROMPT_CODE = "rest_now"
SPEAK_TARGET = "speaker"
SPEAK_OPERATION = "speak"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "expires_in_s": 60,
    "cooldown_sec": 120,
    "reminder_after_sec": 600,
    "speak": True,
    "speaker_target": SPEAK_TARGET,
    "lang": "ko",
    "messages": {
        "asleep": {"default": "졸음이 이어지고 있어요. {minutes}분만 일어나서 쉬어 가세요.",
                   "study": "졸음이 계속되네요. {minutes}분만 일어나 기지개를 켜고 돌아와요."},
        "escalated": {"default": "피로가 가시지 않아요. {minutes}분 쉬고 다시 시작해요.",
                      "study": "집중이 잘 안 되고 있어요. {minutes}분만 쉬었다가 다시 풀어요."},
        "accepted": {"default": "좋아요, {minutes}분 쉬어요. 끝나면 알려 드릴게요.",
                     "study": "좋아요, {minutes}분 쉬어요. 끝나면 다시 알려 드릴게요."},
        "reminder": {"default": "아직 쉬는 중이에요. 조금만 더 쉬고 돌아와요.",
                     "study": "아직 쉬는 시간이에요. 조금만 더 쉬고 다시 시작해요."},
    },
}


def _minutes(rest_min_sec: float) -> int:
    return max(1, int(round(float(rest_min_sec) / 60.0)))


class RestNotifier:
    def __init__(self, cfg: dict[str, Any] | None, *, fsm_cfg: dict[str, Any], boot_id: str,
                 publish_request: Callable[[dict[str, Any]], None], publish_control: Callable[[dict[str, Any]], None] | None,
                 variant: str = "default", on_log: Callable[[str], None] | None = None) -> None:
        self.c = dict(DEFAULTS, **(cfg or {}))
        messages = dict(DEFAULTS["messages"])
        for key, value in ((cfg or {}).get("messages") or {}).items():
            messages[key] = dict(DEFAULTS["messages"].get(key, {}), **(value if isinstance(value, dict) else {"default": str(value)}))
        self.c["messages"] = messages
        self.enabled = bool(self.c.get("enabled", True))
        self.fsm_cfg = fsm_cfg
        self.boot_id = boot_id
        self.publish_request = publish_request
        self.publish_control = publish_control
        self.variant = variant if variant in ("default", "study") else "default"
        self._log = on_log or (lambda m: None)
        self.last_notice_ts: float | None = None
        self.rest_entered_ts: float | None = None
        self.reminded = False
        self.open_request_id: str | None = None
        self.history: list[dict[str, Any]] = []            # session/report 용 (reason·ts·spoken)
        self.counts = {"notices": 0, "speaks": 0, "reminders": 0, "suppressed": 0, "dismissed": 0}

    # ---------------------------------------------------------------- 판단
    def reason_for(self, result, frame, cause: str | None = None) -> str:
        """REST 에 들어온 이유 코드: accepted(휴식 제안 수락) · asleep(아직 졸음·엎드림) · escalated(개입 실패 뒤 강제 휴식)."""
        actions = list(getattr(result, "actions", []) or [])
        if "rest_timer.start" in actions:
            return "accepted"
        if cause is None:
            cause = getattr(result, "cause", None)
        th = (self.fsm_cfg.get("thresholds") or {}).get("still_asleep_delta")
        sig = frame.get("posture") if hasattr(frame, "get") else None
        asleep = bool(sig is not None and getattr(sig, "available", False) and th is not None
                      and float(getattr(sig, "delta", 0.0)) >= float(th))
        if asleep or cause == "posture":
            return "asleep"
        return "escalated"

    def text_for(self, reason: str, minutes: int) -> str:
        table = self.c["messages"].get(reason) or self.c["messages"]["escalated"]
        template = table.get(self.variant) or table.get("default") or ""
        return template.replace("{minutes}", str(minutes))

    # ---------------------------------------------------------------- tick
    def on_tick(self, result, prev_state, frame, now: float, *, seq: int, sound_pref: str | None,
                c_fatigue: float | None = None, cause: str | None = None) -> list[dict[str, Any]]:
        """REST 진입·유지에 맞춰 알림을 낸다. 낸 것(notice·speak payload)을 돌려준다(테스트·로그용).

        cause: 이번 휴식의 원인(없으면 result.cause). ESCALATE→REST tick 의 TickResult.cause 는 비어 있어 엔진의 마지막 원인을 넘긴다.
        """
        if not self.enabled:
            return []
        state = getattr(result.state, "value", result.state)
        prev = getattr(prev_state, "value", prev_state)
        if cause is None:
            cause = getattr(result, "cause", None)
        emitted: list[dict[str, Any]] = []
        if state != "REST":
            self.rest_entered_ts = None
            self.reminded = False
            return emitted
        if prev != "REST":
            self.rest_entered_ts = now
            self.reminded = False
            cooldown = float(self.c.get("cooldown_sec", 0) or 0)
            if self.last_notice_ts is not None and now - self.last_notice_ts < cooldown:
                self.counts["suppressed"] += 1
                self._log(f"[rest_notice] REST 재진입 {now - self.last_notice_ts:.0f}s 만 — 쿨다운({cooldown:.0f}s) 안이라 알리지 않음")
                return emitted
            reason = self.reason_for(result, frame, cause)
            emitted += self._emit(reason, now, seq=seq, sound_pref=sound_pref, cause=cause, c_fatigue=c_fatigue)
            return emitted
        remind_after = self.c.get("reminder_after_sec")
        if (remind_after is not None and not self.reminded and self.rest_entered_ts is not None
                and now - self.rest_entered_ts >= float(remind_after)):
            self.reminded = True
            self.counts["reminders"] += 1
            emitted += self._emit("reminder", now, seq=seq, sound_pref=sound_pref, cause=cause, c_fatigue=c_fatigue)
        return emitted

    def _emit(self, reason: str, now: float, *, seq: int, sound_pref: str | None, cause: str | None,
              c_fatigue: float | None) -> list[dict[str, Any]]:
        rest_min_sec = float((self.fsm_cfg.get("timers") or {}).get("rest_min_sec", 300))
        minutes = _minutes(rest_min_sec)
        text = self.text_for(reason, minutes)
        spoken = bool(self.c.get("speak", True)) and str(sound_pref or "").lower() != "off" and self.publish_control is not None
        expires = float(self.c.get("expires_in_s", 60))
        request_id = f"{self.boot_id}-{seq}-rest"
        notice = {
            "schema_version": "1.0", "ts": now, "node": "hub", "boot_id": self.boot_id, "seq": seq,
            "data": {
                "request_id": request_id, "kind": KIND, "prompt_code": PROMPT_CODE,
                "options": ["dismiss"], "expires_in_s": expires,
                "reason": reason, "cause": cause, "text": text, "minutes": minutes, "rest_min_sec": rest_min_sec,
                "speak": spoken, "variant": self.variant, "evidence": [f"rest:{reason}"],
                "c_fatigue": None if c_fatigue is None else round(float(c_fatigue), 4),
            },
        }
        self.publish_request(notice)
        self.open_request_id = request_id
        self.last_notice_ts = now
        self.counts["notices"] += 1
        out = [notice]
        if spoken:
            cmd = {
                "command_id": f"{self.boot_id}-{seq}-speak", "target_id": str(self.c.get("speaker_target", SPEAK_TARGET)),
                "operation": SPEAK_OPERATION, "value": {"text": text, "lang": str(self.c.get("lang", "ko"))},
                "origin_state": "REST", "cause": cause, "gate": "notice", "requires_confirmation": False,
                "expires_ts_ms": int((now + expires) * 1000),
            }
            self.publish_control(cmd)
            self.counts["speaks"] += 1
            out.append(cmd)
        self.history.append({"ts": now, "reason": reason, "cause": cause, "spoken": spoken, "text": text})
        self._log(f"[rest_notice] {reason}: {text}" + (" (음성)" if spoken else " (카드만)"))
        return out

    # ---------------------------------------------------------------- 피드백·요약
    def owns(self, request_id: Any) -> bool:
        return bool(request_id) and str(request_id).endswith("-rest")

    def on_feedback(self, verdict: str, request_id: Any, now: float) -> None:
        if request_id == self.open_request_id:
            self.open_request_id = None
        if verdict in ("dismiss", "accept", "reject"):
            self.counts["dismissed"] += 1

    def snapshot(self) -> dict[str, Any]:
        last = self.history[-1] if self.history else None
        return {"enabled": self.enabled, "variant": self.variant, "counts": dict(self.counts),
                "last": None if last is None else {"ts": last["ts"], "reason": last["reason"], "spoken": last["spoken"]},
                "in_rest_since": self.rest_entered_ts}

    def report(self) -> dict[str, Any]:
        """session/report 의 `rest_notices` — 횟수와 마지막 알림(문구는 넣지 않는다)."""
        last = self.history[-1] if self.history else None
        return {"count": self.counts["notices"], "spoken": self.counts["speaks"], "reminders": self.counts["reminders"],
                "suppressed": self.counts["suppressed"], "dismissed": self.counts["dismissed"],
                "last_reason": None if last is None else last["reason"],
                "last_ts": None if last is None else round(last["ts"], 3)}
