"""입력 캡처 — 키보드/마우스의 타임스탬프와 '종류'만 수집한다.

프라이버시 원칙(타협 불가):
- 키보드: 물리 키는 dwell 계산을 위해 '메모리 안에서만' 잠깐 식별하고, 기록/전송 값은
  (시간, 종류, press/release)뿐. 종류는 char/space/backspace/enter/other 로만 분류.
- 마우스: 좌표·클릭 버튼·스크롤 내용은 절대 보지 않는다. '이동/클릭/스크롤이 일어났다'는
  사실(시간, 종류)만 기록 → 활동량만 알 수 있고 무엇을 했는지는 복원 불가.
- auto-repeat(키 꾹 누름)는 한 번의 타건으로 취급한다(반복 press 무시).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum

from pynput import keyboard, mouse


class KeyKind(str, Enum):
    CHAR = "char"
    SPACE = "space"
    BACKSPACE = "backspace"
    ENTER = "enter"
    OTHER = "other"


@dataclass(frozen=True)
class KeyEvent:
    t: float
    kind: KeyKind
    down: bool


@dataclass(frozen=True)
class MouseEvent:
    t: float
    kind: str          # "move" | "click" | "scroll" (좌표·버튼은 저장 안 함)


def classify(key) -> KeyKind:
    """pynput 키 객체를 '종류'로만 매핑. 실제 문자 값은 반환하지 않는다."""
    if key == keyboard.Key.space:
        return KeyKind.SPACE
    if key == keyboard.Key.backspace:
        return KeyKind.BACKSPACE
    if key == keyboard.Key.enter:
        return KeyKind.ENTER
    if isinstance(key, keyboard.KeyCode):
        return KeyKind.CHAR
    return KeyKind.OTHER


class InputCapture:
    """백그라운드 스레드로 키보드+마우스 이벤트를 thread-safe 버퍼에 쌓는다."""

    def __init__(self, max_events: int = 20000, move_throttle_s: float = 0.05):
        self._events: deque[KeyEvent] = deque(maxlen=max_events)
        self._mouse: deque[MouseEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._kb: keyboard.Listener | None = None
        self._ms: mouse.Listener | None = None
        self._down_at: dict[object, float] = {}     # 메모리 전용, 외부로 안 나감
        self._move_throttle = move_throttle_s
        self._last_move_t = 0.0

    # ---------- 키보드 ----------
    def _on_press(self, key):
        t = time.monotonic()
        if key in self._down_at:
            return  # auto-repeat: 새 타건 아님 → 무시
        self._down_at[key] = t
        with self._lock:
            self._events.append(KeyEvent(t, classify(key), down=True))

    def _on_release(self, key):
        t = time.monotonic()
        self._down_at.pop(key, None)
        with self._lock:
            self._events.append(KeyEvent(t, classify(key), down=False))

    # ---------- 마우스 (좌표/버튼 무시, 활동만) ----------
    def _on_move(self, x, y):
        t = time.monotonic()
        if t - self._last_move_t < self._move_throttle:
            return
        self._last_move_t = t
        with self._lock:
            self._mouse.append(MouseEvent(t, "move"))

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        with self._lock:
            self._mouse.append(MouseEvent(time.monotonic(), "click"))

    def _on_scroll(self, x, y, dx, dy):
        with self._lock:
            self._mouse.append(MouseEvent(time.monotonic(), "scroll"))

    # ---------- 수명주기 ----------
    def start(self):
        self._kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._ms = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
        )
        self._kb.start()
        self._ms.start()

    def stop(self):
        for lst in (self._kb, self._ms):
            if lst is not None:
                lst.stop()
        self._kb = self._ms = None

    # ---------- 조회 ----------
    def snapshot(self, since_t: float | None = None) -> list[KeyEvent]:
        with self._lock:
            evs = list(self._events)
        return [e for e in evs if e.t >= since_t] if since_t is not None else evs

    def mouse_snapshot(self, since_t: float | None = None) -> list[MouseEvent]:
        with self._lock:
            evs = list(self._mouse)
        return [e for e in evs if e.t >= since_t] if since_t is not None else evs
