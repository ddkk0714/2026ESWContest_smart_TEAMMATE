"""밴딧이 고른 '기기 없는' arm(자세 카드·알림음·음성 안내·휴식 제안)을 실제로 사람에게 전달한다 (2026-10-26).

10-26 보드: ACTION_POSTURE 에서 밴딧이 sound_cue·posture_card 를 골랐는데 AUTO 게이트라 카드가 안 나갔고(제안 카드는 SUGGEST 때만),
대신 control.yaml 기본 램프가 나갔다 — 개인화가 '기록만' 하는 셈이었다. 이제 기기 없는 arm 은
  - AUTO: 안내 카드(`interaction/request`, 상태와 같은 kind, `auto: true`, options [dismiss], arm·arm_label·channel·explanation·text)
  - SUGGEST: 제안 카드(live._maybe_request)가 이미 arm 을 싣고 나가므로 카드는 더 내지 않는다
  - channel voice → `control/cmd speaker speak {text, lang}` (rest_notice 와 같은 계약), channel sound → `speaker chime {pattern, volume}`
  사용자 첫 설정 sound_pref 가 off 면 speak·chime 은 보내지 않는다(카드만). 기본 램프(control.yaml actions)는 정책 결정이 아예 없을
  때(fallback·fsm_fixed·정책 꺼짐)만 나간다 — live.py. 설정 control.yaml `arm_notice:`. 표준 라이브러리만.
"""
from __future__ import annotations

from typing import Any, Callable

SPEAK_TARGET = "speaker"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "expires_in_s": 60,
    "lang": "ko",
    "chime_pattern": "double",
    "voice_text": {
        "posture_voice": "자세를 바로 해 볼까요? 허리를 펴고 고개를 들어 주세요.",
        "default": "{label}",
    },
    "card_text": {
        "posture_card": "자세를 바로 해 볼까요?",
        "sound_cue": "잠깐, 자세와 집중을 확인해 주세요.",
        "posture_voice": "자세를 바로 해 볼까요?",
        "break_short": "5분만 쉬어 가요.",
        "break_long": "조금 길게 쉬어 가요.",
        "ventilate_suggest": "창문을 잠깐 열어 환기해요.",
        "default": "{label}",
    },
}


class ArmDeliverer:
    def __init__(self, cfg: dict[str, Any] | None, *, boot_id: str,
                 publish_request: Callable[[dict[str, Any]], None],
                 publish_control: Callable[[dict[str, Any]], None] | None,
                 on_log: Callable[[str], None] | None = None) -> None:
        self.c = dict(DEFAULTS, **(cfg or {}))
        for key in ("voice_text", "card_text"):
            self.c[key] = dict(DEFAULTS[key], **((cfg or {}).get(key) or {}))
        self.enabled = bool(self.c.get("enabled", True))
        self.boot_id = boot_id
        self.publish_request = publish_request
        self.publish_control = publish_control
        self._log = on_log or (lambda m: None)
        self.open_request_id: str | None = None
        self.counts = {"cards": 0, "speaks": 0, "chimes": 0, "dismissed": 0}
        self.history: list[dict[str, Any]] = []

    def _text(self, table: str, arm: str, label: str) -> str:
        template = (self.c[table].get(arm) or self.c[table].get("default") or "{label}")
        return str(template).replace("{label}", label)

    def deliver(self, *, state: str, kind: str, prompt_code: str, gate: str, arm: str, label: str, channel: str,
                explanation: str | None, cause: str | None, now: float, seq: int, sound_pref: str | None,
                c_fatigue: float | None = None, expires_in_s: float | None = None) -> list[dict[str, Any]]:
        """기기 없는 arm 하나를 전달한다. 낸 payload 목록을 돌려준다(카드·speak·chime)."""
        if not self.enabled:
            return []
        out: list[dict[str, Any]] = []
        expires = float(expires_in_s if expires_in_s is not None else self.c.get("expires_in_s", 60))
        text = self._text("card_text", arm, label)
        sound_ok = str(sound_pref or "").lower() != "off" and self.publish_control is not None
        if gate == "auto":
            request_id = f"{self.boot_id}-{seq}-arm"
            card = {
                "schema_version": "1.0", "ts": now, "node": "hub", "boot_id": self.boot_id, "seq": seq,
                "data": {
                    "request_id": request_id, "kind": kind, "prompt_code": prompt_code, "auto": True,
                    "options": ["dismiss"], "expires_in_s": expires,
                    "arm": arm, "arm_label": label, "channel": channel, "explanation": explanation, "text": text,
                    "cause": cause, "evidence": [f"arm:{arm}"],
                    "c_fatigue": None if c_fatigue is None else round(float(c_fatigue), 4),
                    "speak": channel == "voice" and sound_ok, "chime": channel == "sound" and sound_ok,
                },
            }
            self.publish_request(card)
            self.open_request_id = request_id
            self.counts["cards"] += 1
            out.append(card)
        if channel == "voice" and sound_ok:
            cmd = {
                "command_id": f"{self.boot_id}-{seq}-speak", "target_id": SPEAK_TARGET, "operation": "speak",
                "value": {"text": self._text("voice_text", arm, label), "lang": str(self.c.get("lang", "ko"))},
                "origin_state": state, "cause": cause, "gate": gate, "requires_confirmation": False,
                "expires_ts_ms": int((now + expires) * 1000),
            }
            self.publish_control(cmd)
            self.counts["speaks"] += 1
            out.append(cmd)
        elif channel == "sound" and sound_ok:
            volume = str(sound_pref).lower() if str(sound_pref or "").lower() in ("low", "mid", "high") else "mid"
            cmd = {
                "command_id": f"{self.boot_id}-{seq}-chime", "target_id": SPEAK_TARGET, "operation": "chime",
                "value": {"pattern": str(self.c.get("chime_pattern", "double")), "volume": volume},
                "origin_state": state, "cause": cause, "gate": gate, "requires_confirmation": False,
                "expires_ts_ms": int((now + expires) * 1000),
            }
            self.publish_control(cmd)
            self.counts["chimes"] += 1
            out.append(cmd)
        self.history.append({"ts": now, "arm": arm, "channel": channel, "gate": gate,
                             "delivered": [p.get("operation") or "card" for p in out]})
        self._log(f"[arm_notice] {state} {gate} {arm}({channel}) → " + ", ".join(p.get("operation") or "card" for p in out)
                  + ("" if sound_ok or channel not in ("voice", "sound") else " (sound_pref off → 소리 없음)"))
        return out

    def owns(self, request_id: Any) -> bool:
        return bool(request_id) and str(request_id).endswith("-arm")

    def on_feedback(self, verdict: str, request_id: Any, now: float) -> None:
        if request_id == self.open_request_id:
            self.open_request_id = None
        if verdict in ("dismiss", "accept", "reject"):
            self.counts["dismissed"] += 1

    def snapshot(self) -> dict[str, Any]:
        last = self.history[-1] if self.history else None
        return {"enabled": self.enabled, "counts": dict(self.counts), "last": last}
