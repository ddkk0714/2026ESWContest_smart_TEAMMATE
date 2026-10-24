"""CO₂ 환기 타이밍 규칙(life_sim 제안) — CO₂ 가 높고 사람이 깨어 책상에 있을 때 창문 환기 카드를 먼저 띄운다. FSM 과 따로 돈다.

왜 FSM 밖인가: FSM 의 환경 개입(ACTION_ENV)은 피로가 확정된 뒤에야 나온다. 10-23 v8 시뮬에서 환기 카드는 세 각본 모두 사람이 이미
졸고 있을 때 떠서 응답이 없었고(창문 열림 0 분, 수험생 각본 CO₂ ≥1400 이 112 분), CO₂ 는 피로의 결과가 아니라 원인 쪽이라
졸기 전에 내려야 한다. 그래서 공기청정기 규칙(purifier.py)처럼 CO₂ 만 보는 타이밍 규칙을 따로 둔다.
설정은 config/devices.yaml `vent_control`(기본 enabled: false), 숫자는 전부 가정값.

입력은 hub 가 보는 값만: SCD41 측정 CO₂(UART 0x10, valid 일 때) · 책상 재실 판정 · 자세 모델 판정 · FSM 상태 · 날씨 토픽(창문 가능 여부).
출력은 화면 카드 요청 {"minutes": 창문 열 시간, "why": [...]} — 하네스가 vent_suggest 카드로 띄우고, 수락하면 창문이 열린다.

타이밍 규칙
  켜기 조건  CO₂ 평균(avg_s) ≥ on_ppm 이 hold_s 이어지고, 책상 재실 · 깨어 있음(자세 모델이 drowsy/face_down 아님 awake_s) ·
             창문 닫힘 · 날씨가 창문 환기를 허용(비·눈·바깥 더움·바깥 PM 나쁨이면 제외 — DeviceWorld.ventilation_choice 와 같은 규칙).
  몰입 배려  FOCUS_* 중이면 focus_defer_s 까지 몰입이 끝나기를 기다린다. 단 평균 ≥ high_ppm 이면 기다리지 않는다.
  재시도     카드에 응답이 없거나 거절하면 retry_s / reject_retry_s 뒤에 다시. 창문이 닫힌 뒤에는 처음부터 다시 잰다.
"""
from __future__ import annotations

from collections import deque
from typing import Any

FOCUS_STATES = {"FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC"}
SLEEPY_LABELS = {"drowsy", "face_down"}


class VentRule:
    def __init__(self, cfg: dict[str, Any], *, t0: float):
        self.c = dict(cfg or {})
        self.enabled = bool(self.c.get("enabled", False))
        self.samples: deque[tuple[float, float]] = deque()
        self.avg: float | None = None
        self.high_since: float | None = None
        self.awake_since: float | None = None
        self.focus_wait_since: float | None = None
        self.next_allowed = t0
        self.excluded_logged = False
        self.log: list[dict[str, Any]] = []

    def observe(self, now: float, co2_meas: float | None, *, present: bool, posture: str | None, state: str | None,
                window_open: bool, vent: dict[str, Any]) -> dict[str, Any] | None:
        """1 초마다 부른다. 카드를 띄울 때만 {"minutes", "why"} 를 돌려준다. vent = DeviceWorld.ventilation_choice(...)."""
        if not self.enabled:
            return None
        c = self.c
        if co2_meas is not None:
            self.samples.append((now, float(co2_meas)))
        while self.samples and self.samples[0][0] < now - float(c.get("avg_s", 60)):
            self.samples.popleft()
        if not self.samples:
            return None
        self.avg = sum(v for _, v in self.samples) / len(self.samples)
        awake = present and (posture not in SLEEPY_LABELS)
        self.awake_since = (self.awake_since if self.awake_since is not None else now) if awake else None
        high = self.avg >= float(c.get("on_ppm", 1000))
        self.high_since = (self.high_since if self.high_since is not None else now) if (high and not window_open) else None
        if window_open or self.high_since is None or now < self.next_allowed:
            self.focus_wait_since = None
            return None
        if now - self.high_since < float(c.get("hold_s", 300)):
            return None
        if self.awake_since is None or now - self.awake_since < float(c.get("awake_s", 60)):
            return None
        if vent.get("choice") != "window":
            if not self.excluded_logged:                    # 날씨로 창문 제외 — 카드 없음(선풍기는 FSM 쪽 ACTION_ENV 가 맡는다)
                self.log.append({"t": now, "kind": "excluded", "co2_avg": round(self.avg), "why": vent.get("why")})
                self.excluded_logged = True
            return None
        self.excluded_logged = False
        urgent = self.avg >= float(c.get("high_ppm", 1400))
        if state in FOCUS_STATES and not urgent:
            if self.focus_wait_since is None:
                self.focus_wait_since = now
            if now - self.focus_wait_since < float(c.get("focus_defer_s", 600)):
                return None
        waited = 0.0 if self.focus_wait_since is None else now - self.focus_wait_since
        self.focus_wait_since = None
        self.next_allowed = now + float(c.get("retry_s", 1200))
        why = ["co2_rule", f"co2_avg={self.avg:.0f}"] + (["urgent"] if urgent else []) + (
            ["after_focus_wait"] if waited > 0 else []) + list(vent.get("why") or [])
        rec = {"minutes": float(vent.get("minutes", 15)), "why": why}
        self.log.append({"t": now, "kind": "card", "co2_avg": round(self.avg), "state": state, "focus_wait_s": round(waited), **rec})
        return rec

    def on_feedback(self, verdict: str, now: float) -> None:
        """카드 거절이면 더 오래 쉰다(응답 없음은 retry_s 그대로)."""
        if verdict == "reject":
            self.next_allowed = max(self.next_allowed, now + float(self.c.get("reject_retry_s", 2400)))


def vent_metrics(rows: list[dict[str, Any]], log: list[dict[str, Any]], feedbacks: list[dict[str, Any]], *,
                 on_ppm: float = 1000.0, after_s: float = 900.0) -> dict[str, Any]:
    """카드마다 CO₂(실제 방 값) 카드 시점 → after_s 뒤, 수락 여부와 창문 열린 분. 재실 중 CO₂ 높음 분."""
    by_t = {int(r["t"]): r for r in rows}
    cards = []
    for e in (x for x in log if x["kind"] == "card"):
        t = int(e["t"])
        r0, r1 = by_t.get(t), by_t.get(t + int(after_s))
        fb = next((f for f in feedbacks if f.get("event") == "vent_suggest" and 0 <= f["t"] - e["t"] <= 200), None)
        cards.append({"t_rel_min": None, "co2_avg": e["co2_avg"], "focus_wait_s": e["focus_wait_s"],
                      "verdict": (fb or {}).get("verdict", "none"),
                      "co2_at": None if r0 is None else round(r0["env"]["co2_ppm"]),
                      "co2_after": None if r1 is None else round(r1["env"]["co2_ppm"])})
    t0 = rows[0]["t"] if rows else 0.0
    for c, e in zip(cards, (x for x in log if x["kind"] == "card")):
        c["t_rel_min"] = round((e["t"] - t0) / 60, 1)
    present = [r for r in rows if r.get("present")]
    return {
        "cards": len(cards), "accepted": sum(1 for c in cards if c["verdict"] == "accept"),
        "rejected": sum(1 for c in cards if c["verdict"] == "reject"),
        "excluded": [x for x in log if x["kind"] == "excluded"][:3],
        "card_log": cards,
        "co2_high_present_min": round(sum(1 for r in present if r["env"]["co2_ppm"] >= on_ppm) / 60, 1),
        "co2_mean_present": round(sum(r["env"]["co2_ppm"] for r in present) / max(1, len(present))),
    }
