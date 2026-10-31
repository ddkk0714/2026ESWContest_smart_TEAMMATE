"""빠른 시연 모드 — 화면 버튼(`deskmate/demo/mode` {"speed": "fast"|"normal"})으로 FSM 피로 유지 타이머를 실행 중에 줄였다 되돌린다.

왜: 시연 프로파일(demo-b)에서도 엎드림 → 무드등 명령이 60~69 s 걸린다(10 s tick + 의심 유지 20 s + 확정 유지 20 s + 원인·실행 tick).
심사위원 앞에서 1분을 세우기 어려워, 판정 경로(자세 서비스 → 점수 → FSM 상태 순서)는 그대로 두고 "얼마나 오래 이어져야 확정하나"만 줄인다.

설정은 fsm.yaml `demo_fast:` (프로파일 fsm: 절로 덮을 수 있다).
  enabled     false 면 토픽을 받아도 무시한다(운영 기본).
  timers      fast 일 때 덮을 fsm timers 키와 값. 아는 키만 쓴다. normal 로 돌아가면 켜기 전 값으로 되돌린다.
  max_on_sec  fast 로 둔 채 잊어도 이 시간 뒤 normal 로 돌아간다(0 이면 끄지 않음).

지금 모드는 state/phase sensor_summary.demo_mode 로 낸다 — 화면이 "빠른 시연" 배지를 띄운다.
제한 Python(보드)에서 돈다 — 표준 라이브러리만.
"""
from __future__ import annotations

from typing import Any, Callable

SPEEDS = ("normal", "fast")


class DemoSpeed:
    def __init__(self, cfg: dict[str, Any] | None, timers: dict[str, Any],
                 on_log: Callable[[str], None] | None = None) -> None:
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.max_on_sec = float(cfg.get("max_on_sec", 0) or 0)
        self._timers = timers                       # FSMEngine.tm 과 같은 dict — 바꾸면 다음 tick 부터 엔진이 읽는다
        self._fast = {k: float(v) for k, v in (cfg.get("timers") or {}).items() if k in timers}
        self._normal: dict[str, Any] = {}
        self.speed = "normal"
        self.since: float | None = None
        self.last_reason: str | None = None
        self._seen: float | None = None             # 이미 반영한 토픽 표본(received)
        self._log = on_log or (lambda m: None)

    def observe(self, sample: Any, now: float) -> None:
        """tick 마다 — 새 토픽 표본이면 반영하고, fast 가 max_on_sec 을 넘었으면 normal 로 돌린다."""
        if sample is not None and sample.received != self._seen:
            self._seen = sample.received
            speed = str((sample.data or {}).get("speed", "")).lower()
            if speed in SPEEDS:
                self.set(speed, now, "request")
            else:
                self._log(f"[demo_fast] 알 수 없는 speed: {speed!r}")
        if (self.speed == "fast" and self.max_on_sec > 0 and self.since is not None
                and now - self.since >= self.max_on_sec):
            self.set("normal", now, "max_on")

    def set(self, speed: str, now: float, reason: str) -> None:
        if speed == "fast" and not self.enabled:
            self._log("[demo_fast] fsm.yaml demo_fast.enabled 가 false 라 빠른 시연을 켜지 않는다")
            return
        if speed == self.speed:
            return
        if speed == "fast":
            self._normal = {k: self._timers[k] for k in self._fast}
            self._timers.update(self._fast)
        else:
            self._timers.update(self._normal)
        self.speed, self.since, self.last_reason = speed, now, reason
        changed = ", ".join(f"{k} {self._normal[k]}→{self._timers[k]}" for k in self._fast)
        self._log(f"[demo_fast] {speed} ({reason}) {changed}")

    def snapshot(self, now: float) -> dict[str, Any]:
        view: dict[str, Any] = {"speed": self.speed, "enabled": self.enabled}
        if self.speed == "fast" and self.since is not None:
            view["since"] = round(self.since, 3)
            view["timers"] = {k: self._timers[k] for k in self._fast}
            if self.max_on_sec > 0:
                view["remaining_s"] = max(0, int(self.max_on_sec - (now - self.since)))
        return view
