"""Pi 4 native service(C++) 가 넘기는 `UART\\t{...}` 라인 → SensorCache.

C++ 쪽은 COBS 해제·CRC 검증까지만 하고 다음 JSON 을 한 줄로 준다:
    UART\\t{"type":32,"seq":123,"ts_ms":456789,"payload_hex":"01010c..."}
여기서 TYPE 별 payload 를 계약 필드로 풀어 MQTT 경로와 같은 Sample 로 캐시에 넣는다.
PC 개발용으로 시리얼 raw 바이트에서 직접 프레임을 자르는 `RawUartReader` 도 둔다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .cache import Sample, SensorCache
from .uart_frame import (
    KIND_BY_TYPE, TYPE_HEARTBEAT, Frame, FrameError, decode_frame, decode_payload, split_stream,
)


@dataclass
class UartStats:
    frames: int = 0
    dropped: int = 0
    heartbeats: int = 0
    unknown_types: dict[int, int] = field(default_factory=dict)
    last_heartbeat: dict[str, Any] | None = None


def parse_uart_line(line: str) -> Frame | None:
    """`UART\\t{json}` 한 줄 → Frame. 형식이 아니면 None."""
    if not line.startswith("UART\t"):
        return None
    try:
        body = json.loads(line[5:])
        return Frame(type=int(body["type"]), seq=int(body["seq"]), ts_ms=int(body["ts_ms"]),
                     payload=bytes.fromhex(body["payload_hex"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def ingest_frame(cache: SensorCache, frame: Frame, stats: UartStats, received: float | None = None,
                 node: str = "esp32") -> str | None:
    """Frame 을 캐시에 반영. 반영된 kind(또는 'heartbeat') 를 돌려주고, 모르는 TYPE 은 None."""
    received = time.time() if received is None else received
    try:
        data = decode_payload(frame.type, frame.payload)
    except FrameError:
        stats.dropped += 1
        return None
    if frame.type == TYPE_HEARTBEAT:
        stats.heartbeats += 1
        stats.last_heartbeat = {"received": received, "node": node, **(data or {})}
        return "heartbeat"
    kind = KIND_BY_TYPE.get(frame.type)
    if kind is None or data is None:
        stats.unknown_types[frame.type] = stats.unknown_types.get(frame.type, 0) + 1
        return None
    cache.put(kind, Sample(received=received, ts=received, seq=frame.seq, data=data))
    stats.frames += 1
    return kind


class UartLineSource:
    """서비스 브리지 stdin 에서 온 라인을 캐시로 흘린다."""

    def __init__(self, cache: SensorCache, node: str = "esp32") -> None:
        self.cache, self.node, self.stats = cache, node, UartStats()

    def feed_line(self, line: str) -> str | None:
        frame = parse_uart_line(line.rstrip("\n"))
        return None if frame is None else ingest_frame(self.cache, frame, self.stats, node=self.node)


class RawUartReader:
    """PC 개발용: 시리얼 raw 바이트 → 프레임 → 캐시. C++ 서비스와 같은 규칙(CRC 실패는 폐기)."""

    def __init__(self, cache: SensorCache, node: str = "esp32") -> None:
        self.cache, self.node, self.stats = cache, node, UartStats()
        self._buf = bytearray()

    def feed_bytes(self, chunk: bytes) -> list[str]:
        self._buf += chunk
        kinds: list[str] = []
        for block in split_stream(self._buf):
            try:
                frame = decode_frame(block)
            except FrameError:
                self.stats.dropped += 1
                continue
            kind = ingest_frame(self.cache, frame, self.stats, node=self.node)
            if kind:
                kinds.append(kind)
        return kinds
