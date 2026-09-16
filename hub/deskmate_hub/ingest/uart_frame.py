"""ESP32 ↔ Pi 4 UART2 바이너리 프레임 코덱 (잠정 규약 — docs/data-spec.md §13).

    frame = COBS( header | payload | crc16 ) + 0x00
    header = SOF 0xA5 | VER 0x01 | TYPE | SEQ u16 | LEN u16 | TS_MS u32   (little-endian)
    crc16  = CRC-16/CCITT-FALSE over SOF..payload

순수 Python, 표준 라이브러리만 쓴다(Pi 4 제한 Python 에서도 동작). Pi 4 C++ 서비스는 COBS 해제·CRC 검증까지만
하고 `UART\\t{"type","seq","ts_ms","payload_hex"}` 라인을 넘기므로, 여기서는 payload 구조체 해석과
(테스트·PC 도구용) 인코딩/디코딩 전체를 제공한다.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

SOF = 0xA5
VERSION = 0x01
HEADER = struct.Struct("<BBBHHI")          # SOF VER TYPE SEQ LEN TS_MS
CRC_LEN = 2

# TYPE 표 (잠정 — agent-briefing D1 확정 전. 실험 펌웨어의 0x01 은 ToF 디버그 예약과 충돌해 쓰지 않는다)
TYPE_TOF_DEBUG_DEPTH = 0x01
TYPE_TOF_FEATURES = 0x03
TYPE_TOF_POSTURE = 0x04
TYPE_ENV = 0x10
TYPE_MMWAVE = 0x20
TYPE_HEARTBEAT = 0xF0

_MOTION = {0: "none", 1: "still", 2: "active"}
_DROWSY = {0: "NOPERSON", 1: "NOLOCK", 2: "WARMUP", 3: "AWAKE", 4: "DROWSY"}
_MMWAVE = struct.Struct("<BBBHBBBBBB")     # 11 B
_ENV = struct.Struct("<HhHHB")             # 9 B
_HEARTBEAT = struct.Struct("<IHB")         # 7 B


class FrameError(ValueError):
    """프레임 형식·CRC 오류."""


# ---------------------------------------------------------------- CRC / COBS

def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflect, xorout 0. '123456789' → 0x29B1."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    idx = 0
    while True:
        chunk_end = data.find(b"\x00", idx)
        if chunk_end < 0:
            chunk_end = len(data)
        while chunk_end - idx >= 0xFE:
            out.append(0xFF)
            out += data[idx:idx + 0xFE]
            idx += 0xFE
            if idx == len(data):              # 254바이트 블록으로 정확히 끝나면 후행 코드 없음 (표준 COBS)
                return bytes(out)
        out.append(chunk_end - idx + 1)
        out += data[idx:chunk_end]
        if chunk_end >= len(data):
            return bytes(out)
        idx = chunk_end + 1


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    idx = 0
    n = len(data)
    while idx < n:
        code = data[idx]
        if code == 0:
            raise FrameError("COBS: zero byte inside encoded block")
        idx += 1
        block = data[idx:idx + code - 1]
        if len(block) != code - 1:
            raise FrameError("COBS: truncated block")
        out += block
        idx += code - 1
        if code != 0xFF and idx < n:
            out.append(0)
    return bytes(out)


# ---------------------------------------------------------------- frame

@dataclass
class Frame:
    type: int
    seq: int
    ts_ms: int
    payload: bytes

    @property
    def payload_hex(self) -> str:
        return self.payload.hex()


def encode_frame(frame: Frame) -> bytes:
    """헤더+payload+CRC 를 COBS 인코딩하고 0x00 종료자를 붙인 wire bytes."""
    if not 0 <= frame.type <= 0xFF or len(frame.payload) > 0xFFFF:
        raise FrameError("bad type or payload length")
    body = HEADER.pack(SOF, VERSION, frame.type, frame.seq & 0xFFFF, len(frame.payload), frame.ts_ms & 0xFFFFFFFF)
    body += frame.payload
    body += struct.pack("<H", crc16_ccitt_false(body))
    return cobs_encode(body) + b"\x00"


def decode_frame(raw: bytes) -> Frame:
    """0x00 종료자를 제외한 COBS 블록 하나 → Frame. 형식·CRC 오류는 FrameError."""
    if raw.endswith(b"\x00"):
        raw = raw[:-1]
    body = cobs_decode(raw)
    if len(body) < HEADER.size + CRC_LEN:
        raise FrameError("frame too short")
    sof, ver, ftype, seq, length, ts_ms = HEADER.unpack_from(body, 0)
    if sof != SOF or ver != VERSION:
        raise FrameError(f"bad SOF/VER {sof:#04x}/{ver:#04x}")
    end = HEADER.size + length
    if len(body) != end + CRC_LEN:
        raise FrameError(f"LEN mismatch: header {length}, have {len(body) - HEADER.size - CRC_LEN}")
    (crc_rx,) = struct.unpack_from("<H", body, end)
    if crc_rx != crc16_ccitt_false(body[:end]):
        raise FrameError("CRC mismatch")
    return Frame(type=ftype, seq=seq, ts_ms=ts_ms, payload=bytes(body[HEADER.size:end]))


def split_stream(buffer: bytearray) -> list[bytes]:
    """수신 버퍼에서 0x00 으로 끝난 완전한 블록들을 꺼내 돌려준다(버퍼는 잔여만 남긴다)."""
    blocks: list[bytes] = []
    while True:
        cut = buffer.find(b"\x00")
        if cut < 0:
            return blocks
        block = bytes(buffer[:cut])
        del buffer[:cut + 1]
        if block:
            blocks.append(block)


# ---------------------------------------------------------------- payload structs

def _nullable(value: int, sentinel: int) -> int | None:
    return None if value == sentinel else value


def decode_payload(ftype: int, payload: bytes) -> dict[str, Any] | None:
    """TYPE 별 payload → 계약 필드(dict). 모르는 TYPE 은 None."""
    if ftype == TYPE_MMWAVE:
        if len(payload) < _MMWAVE.size:
            raise FrameError("mmwave payload short")
        present, motion, level, dist, resp, resp_ok, heart, heart_ok, drowsy, valid = _MMWAVE.unpack_from(payload)
        return {
            "present": bool(present),
            "motion_state": _MOTION.get(motion, "unknown"),
            "motion_level": int(level),
            "distance_cm": _nullable(dist, 0xFFFF),
            "resp_bpm": _nullable(resp, 0xFF),
            "resp_valid": bool(resp_ok),
            "heart_bpm": _nullable(heart, 0xFF),
            "heart_valid": bool(heart_ok),
            "drowsy_state": _DROWSY.get(drowsy, "NOLOCK"),
            "valid": bool(valid),
        }
    if ftype == TYPE_ENV:
        if len(payload) < _ENV.size:
            raise FrameError("env payload short")
        co2, temp10, hum10, lux, bits = _ENV.unpack_from(payload)
        return {
            "co2_ppm": int(co2) if bits & 1 else None,
            "temp_c": temp10 / 10.0 if bits & 2 else None,
            "humidity_pct": hum10 / 10.0 if bits & 4 else None,
            "lux": float(lux) if bits & 8 else None,
            "co2_valid": bool(bits & 1),
            "temp_valid": bool(bits & 2),
            "humidity_valid": bool(bits & 4),
            "lux_valid": bool(bits & 8),
        }
    if ftype == TYPE_HEARTBEAT:
        if len(payload) < _HEARTBEAT.size:
            raise FrameError("heartbeat payload short")
        uptime, fw, mm_ok = _HEARTBEAT.unpack_from(payload)
        return {"uptime_ms": int(uptime), "fw_version": int(fw), "mmwave_ok": bool(mm_ok)}
    return None


def encode_payload(ftype: int, data: dict[str, Any]) -> bytes:
    """계약 필드(dict) → payload bytes. 테스트·PC 시뮬레이터용(펌웨어 구현의 참조)."""
    if ftype == TYPE_MMWAVE:
        motion = {v: k for k, v in _MOTION.items()}.get(data.get("motion_state", "none"), 0)
        drowsy = {v: k for k, v in _DROWSY.items()}.get(data.get("drowsy_state", "NOLOCK"), 1)
        return _MMWAVE.pack(
            int(bool(data.get("present"))), motion, int(data.get("motion_level") or 0),
            0xFFFF if data.get("distance_cm") is None else int(data["distance_cm"]),
            0xFF if data.get("resp_bpm") is None else int(data["resp_bpm"]), int(bool(data.get("resp_valid"))),
            0xFF if data.get("heart_bpm") is None else int(data["heart_bpm"]), int(bool(data.get("heart_valid"))),
            drowsy, int(bool(data.get("valid", True))),
        )
    if ftype == TYPE_ENV:
        bits = (1 if data.get("co2_valid") else 0) | (2 if data.get("temp_valid") else 0) \
            | (4 if data.get("humidity_valid") else 0) | (8 if data.get("lux_valid") else 0)
        return _ENV.pack(
            int(data.get("co2_ppm") or 0), int(round((data.get("temp_c") or 0.0) * 10)),
            int(round((data.get("humidity_pct") or 0.0) * 10)), int(data.get("lux") or 0), bits,
        )
    if ftype == TYPE_HEARTBEAT:
        return _HEARTBEAT.pack(int(data.get("uptime_ms") or 0), int(data.get("fw_version") or 0),
                               int(bool(data.get("mmwave_ok"))))
    raise FrameError(f"no encoder for type {ftype:#04x}")


KIND_BY_TYPE = {TYPE_MMWAVE: "mmwave", TYPE_ENV: "env"}
