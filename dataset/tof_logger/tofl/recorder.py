"""줄 소스(시리얼·가짜) → 지시 자세를 붙여 세션으로 저장."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Protocol

from .lines import parse_line
from .protocol import Now, Schedule
from .session import SessionWriter


class LineSource(Protocol):
    def __iter__(self) -> Iterator[tuple[bytes, float]]: ...
    def send(self, cmd: str) -> None: ...
    def close(self) -> None: ...


class SerialSource:
    """ESP32 USB 시리얼. 줄마다 받은 시각(time.time())을 붙인다."""

    def __init__(self, port: str, baud: int = 921600):
        import serial  # pyserial — 가짜 소스만 쓸 때는 없어도 된다

        self._ser = serial.Serial(port, baud, timeout=0.2)
        self._ser.reset_input_buffer()
        self.send("h")  # 부팅 hello 를 놓쳤을 때

    def send(self, cmd: str) -> None:
        self._ser.write(cmd.encode("ascii"))

    def close(self) -> None:
        self._ser.close()

    def __iter__(self) -> Iterator[tuple[bytes, float]]:
        while True:
            raw = self._ser.readline()
            if raw:
                yield raw, time.time()
            else:
                yield b"", time.time()  # 조용해도 상태 표시·종료 판단은 돈다


@dataclass
class Status:
    elapsed_s: float = 0.0
    now: Now | None = None
    rule: dict = field(default_factory=dict)
    frames: int = 0
    fps: float = 0.0
    seq_gaps: int = 0
    errors: int = 0
    last_error: dict | None = None
    step_changed: bool = False


def record(source: Iterable[tuple[bytes, float]], writer: SessionWriter,
           schedule: Schedule | None = None, max_seconds: float | None = None,
           on_status: Callable[[Status], None] | None = None, send: Callable[[str], None] | None = None,
           status_every_s: float = 0.5, flush_every: int = 30,
           should_stop: Callable[[], bool] | None = None,
           on_event: Callable[[dict], None] | None = None) -> Status:
    """프로토콜이 끝나거나 max_seconds 가 지나거나 should_stop() 이 참이면 멈춘다.

    시계는 첫 tof 프레임에서 시작한다(센서 펌웨어 업로드에 수 초가 걸리므로).
    프로토콜 단계에 cmd 가 있으면 그 단계가 시작될 때 한 번 보낸다.
    """
    st = Status()
    start: float | None = None
    last_report = -1e9
    last_key: tuple[int, str] | None = None
    sent_steps: set[int] = set()
    fps_t, fps_n = None, 0
    for raw, t_host in source:
        if should_stop and should_stop():
            break
        obj = parse_line(raw) if raw else None
        if obj is not None and obj["t"] != "tof":
            writer.event(obj["t"], t_host, **{k: v for k, v in obj.items() if k != "t"})
            if obj["t"] == "tof_err":
                st.errors += 1
                st.last_error = obj
            if on_event:
                on_event(obj)
            continue
        if obj is not None and start is None:
            start = t_host
            fps_t = t_host
        if start is None:
            continue
        st.elapsed_s = t_host - start
        now = schedule.at(st.elapsed_s) if schedule else None
        if schedule and now is None:
            break
        if max_seconds is not None and st.elapsed_s >= max_seconds:
            break
        key = (now.index, now.phase) if now else None
        st.step_changed = key != last_key
        last_key = key
        st.now = now
        if now and now.phase == "step" and now.index not in sent_steps:
            sent_steps.add(now.index)
            cmd = schedule.steps[now.index].cmd
            if cmd and send:
                send(cmd)
                writer.event("cmd", t_host, cmd=cmd, step=now.index)
        if obj is not None:
            writer.frame(obj, t_host, now.prompt if now else None, now.phase if now else None,
                         now.index if now else None)
            st.rule = obj["rule"]
            st.frames += 1
            fps_n += 1
            if st.frames % flush_every == 0:
                writer.flush()
        if t_host - fps_t >= 1.0:
            st.fps = fps_n / (t_host - fps_t)
            fps_t, fps_n = t_host, 0
        if on_status and (st.step_changed or t_host - last_report >= status_every_s):
            st.seq_gaps = writer.stats["seq_gaps"]
            on_status(st)
            last_report = t_host
    st.seq_gaps = writer.stats["seq_gaps"]
    return st
