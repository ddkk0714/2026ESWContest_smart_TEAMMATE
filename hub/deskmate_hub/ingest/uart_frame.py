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
# 펌웨어 TofPosture enum 값 그대로 (firmware/.../preprocess/tof/TofPosture.h)
_POSTURE = {0: "unknown", 1: "upright", 2: "lean_forward", 3: "recline", 4: "face_down",
            5: "chin_rest", 6: "drowsy", 7: "away"}
_MMWAVE = struct.Struct("<BBBHBBBBBB")     # 11 B
_ENV = struct.Struct("<HhHHB")             # 9 B
_HEARTBEAT = struct.Struct("<IHB")         # 7 B (+ tof_ok u8, 펌웨어 0x0120 부터 8 B)
# TYPE 0x03 tof_feature 26 B — 필드 순서·단위는 firmware/.../sensors/tof/TofPayload.h
#   present · posture · confidence_pct · motion_pct · head_delta_mm i16(0x7FFF null) · nod_rate_mhz u16(0xFFFF null)
#   · valid_zones u16 · grid_w · grid_h · coverage_pct · presence_count u16 · centroid_mm u16(0xFFFF null)
#   · head_row_pct u8(0xFF null) · shoulder_tilt_mm i16(0x7FFF null) · motion_mm_s u16 · change_per_min u8
#   · baseline_dev_pct u8 · flags u8 · nods u8
_TOF = struct.Struct("<BBBBhHHBBBHHBhHBBBB")
_TOF_POSTURE = struct.Struct("<BBBB")      # TYPE 0x04: present · posture · confidence_pct · drowsy
TOF_FLAG_BASELINE_READY = 0x01
TOF_FLAG_DROWSY = 0x02
TOF_FLAG_GEOMETRY_VALID = 0x04
TOF_FLAG_SENSOR_A = 0x08
TOF_FLAG_SENSOR_B = 0x10
_TOF_NULL_I16 = 0x7FFF


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
        out = {"uptime_ms": int(uptime), "fw_version": int(fw), "mmwave_ok": bool(mm_ok)}
        if len(payload) > _HEARTBEAT.size:  # 펌웨어 0x0120 부터: ToF 센서 동작 비트(b0 A, b1 B)
            out["tof_ok"] = int(payload[_HEARTBEAT.size])
        return out
    if ftype == TYPE_TOF_FEATURES:
        if len(payload) < _TOF.size:
            raise FrameError("tof payload short")
        (present, posture, conf, motion, head_delta, nod_mhz, valid_zones, grid_w, grid_h, coverage,
         presence_count, centroid, head_row, shoulder, motion_mm_s, change_rate, baseline_dev, flags,
         nods) = _TOF.unpack_from(payload)
        sensors = (1 if flags & TOF_FLAG_SENSOR_A else 0) | (2 if flags & TOF_FLAG_SENSOR_B else 0)
        return {
            "present": bool(present),
            "posture": _POSTURE.get(posture, "unknown"),
            "posture_confidence": conf / 100.0,
            "motion_score": motion / 100.0,
            "head_delta_mm": None if head_delta == _TOF_NULL_I16 else int(head_delta),
            "nod_rate_hz": None if nod_mhz == 0xFFFF else nod_mhz / 1000.0,
            "valid_zones": int(valid_zones),
            "grid_width": int(grid_w),
            "grid_height": int(grid_h),
            "coverage_ratio": coverage / 100.0,
            "presence_count": int(presence_count),
            "centroid_depth_mm": _nullable(centroid, 0xFFFF),
            "head_row_index": None if head_row == 0xFF else head_row / 100.0,
            "shoulder_tilt_mm": None if shoulder == _TOF_NULL_I16 else int(shoulder),
            "motion_indicator_mm_s": int(motion_mm_s),
            "posture_change_rate_per_min": int(change_rate),
            "baseline_deviation": baseline_dev / 100.0,
            "baseline_ready": bool(flags & TOF_FLAG_BASELINE_READY),
            "drowsy": bool(flags & TOF_FLAG_DROWSY),
            "geometry_valid": bool(flags & TOF_FLAG_GEOMETRY_VALID),
            "sensors_ok": sensors,
            "nods_in_window": int(nods),
        }
    if ftype == TYPE_TOF_POSTURE:
        if len(payload) < _TOF_POSTURE.size:
            raise FrameError("tof posture payload short")
        present, posture, conf, drowsy = _TOF_POSTURE.unpack_from(payload)
        return {"present": bool(present), "posture": _POSTURE.get(posture, "unknown"),
                "posture_confidence": conf / 100.0, "drowsy": bool(drowsy)}
    return None


def _pct(value: Any, limit: int = 100) -> int:
    v = int(round(float(value or 0.0) * 100))
    return max(0, min(limit, v))


def _posture_code(name: Any) -> int:
    return {v: k for k, v in _POSTURE.items()}.get(str(name or "unknown"), 0)


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
        out = _HEARTBEAT.pack(int(data.get("uptime_ms") or 0), int(data.get("fw_version") or 0),
                              int(bool(data.get("mmwave_ok"))))
        if data.get("tof_ok") is not None:
            out += bytes([int(data["tof_ok"]) & 0xFF])
        return out
    if ftype == TYPE_TOF_FEATURES:
        flags = ((TOF_FLAG_BASELINE_READY if data.get("baseline_ready") else 0)
                 | (TOF_FLAG_DROWSY if data.get("drowsy") else 0)
                 | (TOF_FLAG_GEOMETRY_VALID if data.get("geometry_valid") else 0)
                 | (TOF_FLAG_SENSOR_A if int(data.get("sensors_ok") or 0) & 1 else 0)
                 | (TOF_FLAG_SENSOR_B if int(data.get("sensors_ok") or 0) & 2 else 0))
        return _TOF.pack(
            int(bool(data.get("present"))), _posture_code(data.get("posture")),
            _pct(data.get("posture_confidence")), _pct(data.get("motion_score")),
            _TOF_NULL_I16 if data.get("head_delta_mm") is None else int(data["head_delta_mm"]),
            0xFFFF if data.get("nod_rate_hz") is None else int(round(float(data["nod_rate_hz"]) * 1000)),
            int(data.get("valid_zones") or 0), int(data.get("grid_width") or 0), int(data.get("grid_height") or 0),
            _pct(data.get("coverage_ratio")), int(data.get("presence_count") or 0),
            0xFFFF if data.get("centroid_depth_mm") is None else int(data["centroid_depth_mm"]),
            0xFF if data.get("head_row_index") is None else _pct(data["head_row_index"]),
            _TOF_NULL_I16 if data.get("shoulder_tilt_mm") is None else int(data["shoulder_tilt_mm"]),
            int(data.get("motion_indicator_mm_s") or 0), int(data.get("posture_change_rate_per_min") or 0),
            _pct(data.get("baseline_deviation"), 255), flags, int(data.get("nods_in_window") or 0),
        )
    if ftype == TYPE_TOF_POSTURE:
        return _TOF_POSTURE.pack(int(bool(data.get("present"))), _posture_code(data.get("posture")),
                                 _pct(data.get("posture_confidence")), int(bool(data.get("drowsy"))))
    raise FrameError(f"no encoder for type {ftype:#04x}")


KIND_BY_TYPE = {TYPE_MMWAVE: "mmwave", TYPE_ENV: "env", TYPE_TOF_FEATURES: "tof",
                TYPE_TOF_POSTURE: "tof_posture"}
