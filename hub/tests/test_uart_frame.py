"""UART2 프레임 코덱(COBS·CRC·구조체)과 UART 라인 소스. 실보드 없이 검증한다."""
from __future__ import annotations

import json

import pytest

from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.uart_frame import (
    TYPE_ENV, TYPE_HEARTBEAT, TYPE_MMWAVE, TYPE_TOF_FEATURES, TYPE_TOF_POSTURE, Frame, FrameError, cobs_decode,
    cobs_encode, crc16_ccitt_false, decode_frame, decode_payload, encode_frame, encode_payload, split_stream,
)
from deskmate_hub.ingest.uart_source import RawUartReader, UartLineSource, parse_uart_line

MM = {"present": True, "motion_state": "still", "motion_level": 12, "distance_cm": 55, "resp_bpm": 15,
      "resp_valid": True, "heart_bpm": None, "heart_valid": False, "drowsy_state": "AWAKE", "valid": True}
ENV = {"co2_ppm": 812, "temp_c": 26.4, "humidity_pct": 48.2, "lux": 310.0,
       "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True}
TOF = {"present": True, "posture": "upright", "posture_confidence": 0.83, "motion_score": 0.05,
       "head_delta_mm": -12, "nod_rate_hz": 0.05, "valid_zones": 120, "grid_width": 16, "grid_height": 8,
       "coverage_ratio": 0.42, "presence_count": 54, "centroid_depth_mm": 612, "head_row_index": 0.25,
       "shoulder_tilt_mm": 8, "motion_indicator_mm_s": 15, "posture_change_rate_per_min": 2,
       "baseline_deviation": 0.37, "baseline_ready": True, "drowsy": False, "geometry_valid": True,
       "sensors_ok": 3, "nods_in_window": 1}
# 펌웨어 test/test_tof_payload/test_tof_payload.cpp 의 kGoldenFeatureHex 와 같은 값 — 바꾸면 둘 다 바꾼다
TOF_GOLDEN_HEX = "01015305f4ff3200780010082a360064021908000f0002251d01"


# ── test vectors (펌웨어·C++ 구현이 맞춰야 하는 값) ────────────────────────

def test_crc16_ccitt_false_check_value():
    assert crc16_ccitt_false(b"123456789") == 0x29B1
    assert crc16_ccitt_false(b"") == 0xFFFF


@pytest.mark.parametrize("raw,encoded", [
    (b"\x00", b"\x01\x01"),
    (b"\x00\x00", b"\x01\x01\x01"),
    (b"\x11\x22\x00\x33", b"\x03\x11\x22\x02\x33"),
    (b"\x11\x22\x33\x44", b"\x05\x11\x22\x33\x44"),
    (b"\x11\x00\x00\x00", b"\x02\x11\x01\x01\x01"),
    (bytes(range(1, 255)), b"\xff" + bytes(range(1, 255))),
])
def test_cobs_vectors(raw, encoded):
    assert cobs_encode(raw) == encoded
    assert cobs_decode(encoded) == raw


def test_cobs_roundtrip_long_with_zeros():
    raw = bytes(range(256)) * 3
    assert cobs_decode(cobs_encode(raw)) == raw
    assert b"\x00" not in cobs_encode(raw)


def test_frame_roundtrip_and_known_wire_bytes():
    frame = Frame(type=TYPE_MMWAVE, seq=7, ts_ms=1000, payload=encode_payload(TYPE_MMWAVE, MM))
    wire = encode_frame(frame)
    assert wire.endswith(b"\x00") and wire.count(b"\x00") == 1
    back = decode_frame(wire)
    assert (back.type, back.seq, back.ts_ms) == (TYPE_MMWAVE, 7, 1000)
    assert decode_payload(back.type, back.payload) == MM
    # 고정 벡터 — 펌웨어 frame.cpp 검증용
    assert frame.payload.hex() == "01010c3700" "0f01ff000301"


def test_tof_feature_vector():
    payload = encode_payload(TYPE_TOF_FEATURES, TOF)
    assert payload.hex() == TOF_GOLDEN_HEX and len(payload) == 26
    assert decode_payload(TYPE_TOF_FEATURES, payload) == TOF
    frame = decode_frame(encode_frame(Frame(type=TYPE_TOF_FEATURES, seq=7, ts_ms=1000, payload=payload)))
    assert (frame.type, frame.seq, frame.ts_ms) == (TYPE_TOF_FEATURES, 7, 1000)


def test_tof_feature_nulls_and_single_sensor():
    data = dict(TOF, head_delta_mm=None, nod_rate_hz=None, centroid_depth_mm=None, head_row_index=None,
                shoulder_tilt_mm=None, geometry_valid=False, baseline_ready=False, drowsy=True,
                sensors_ok=2, grid_width=8, grid_height=8)
    back = decode_payload(TYPE_TOF_FEATURES, encode_payload(TYPE_TOF_FEATURES, data))
    assert back == data
    with pytest.raises(FrameError):
        decode_payload(TYPE_TOF_FEATURES, b"\x01" * 10)


def test_tof_posture_summary_payload():
    data = {"present": True, "posture": "face_down", "posture_confidence": 0.61, "drowsy": False}
    payload = encode_payload(TYPE_TOF_POSTURE, data)
    assert payload.hex() == "01043d00"
    assert decode_payload(TYPE_TOF_POSTURE, payload) == data
    assert decode_payload(TYPE_TOF_POSTURE, b"\x00\x63\x00\x00")["posture"] == "unknown"  # 모르는 코드


def test_heartbeat_tof_ok_is_optional():
    old = {"uptime_ms": 5000, "fw_version": 3, "mmwave_ok": True}
    assert decode_payload(TYPE_HEARTBEAT, encode_payload(TYPE_HEARTBEAT, old)) == old
    new = dict(old, tof_ok=3)
    payload = encode_payload(TYPE_HEARTBEAT, new)
    assert len(payload) == 8 and decode_payload(TYPE_HEARTBEAT, payload) == new


def test_tof_frames_land_in_cache():
    cache = SensorCache()
    src = UartLineSource(cache)
    line = "UART\t" + json.dumps({"type": TYPE_TOF_FEATURES, "seq": 3, "ts_ms": 10,
                                   "payload_hex": TOF_GOLDEN_HEX})
    assert src.feed_line(line) == "tof"
    assert cache.tof is not None and cache.tof.data["posture"] == "upright"
    line = "UART\t" + json.dumps({"type": TYPE_TOF_POSTURE, "seq": 4, "ts_ms": 11, "payload_hex": "01043d00"})
    assert src.feed_line(line) == "tof_posture"
    assert cache.snapshot().tof_posture.data["posture"] == "face_down"
    assert src.stats.frames == 2 and src.stats.unknown_types == {}


def test_env_and_heartbeat_payloads():
    env = decode_payload(TYPE_ENV, encode_payload(TYPE_ENV, ENV))
    assert env == ENV
    partial = decode_payload(TYPE_ENV, encode_payload(TYPE_ENV, {"co2_ppm": 900, "co2_valid": True}))
    assert partial["co2_ppm"] == 900 and partial["temp_c"] is None and partial["lux_valid"] is False
    hb = decode_payload(TYPE_HEARTBEAT, encode_payload(TYPE_HEARTBEAT, {"uptime_ms": 5000, "fw_version": 3, "mmwave_ok": True}))
    assert hb == {"uptime_ms": 5000, "fw_version": 3, "mmwave_ok": True}


def test_corrupted_frames_are_rejected():
    wire = bytearray(encode_frame(Frame(TYPE_ENV, 1, 0, encode_payload(TYPE_ENV, ENV))))
    wire[5] ^= 0x01
    with pytest.raises(FrameError):
        decode_frame(bytes(wire))
    with pytest.raises(FrameError):
        decode_frame(b"\x02\xa5\x00")      # 너무 짧음
    with pytest.raises(FrameError):
        decode_frame(encode_frame(Frame(TYPE_ENV, 1, 0, b""))[:-3] + b"\x00")  # 잘림


def test_split_stream_handles_partial_and_garbage():
    a = encode_frame(Frame(TYPE_MMWAVE, 1, 0, encode_payload(TYPE_MMWAVE, MM)))
    b = encode_frame(Frame(TYPE_ENV, 2, 0, encode_payload(TYPE_ENV, ENV)))
    buf = bytearray(b"\x00\x00" + a + b[:5])
    blocks = split_stream(buf)
    assert len(blocks) == 1 and decode_frame(blocks[0]).seq == 1
    buf += b[5:]
    blocks = split_stream(buf)
    assert len(blocks) == 1 and decode_frame(blocks[0]).seq == 2 and not buf


# ── 라인 소스 / raw 리더 ───────────────────────────────────────────────

def test_uart_line_source_feeds_cache():
    cache = SensorCache()
    src = UartLineSource(cache)
    payload = encode_payload(TYPE_MMWAVE, MM).hex()
    line = "UART\t" + json.dumps({"type": TYPE_MMWAVE, "seq": 5, "ts_ms": 123, "payload_hex": payload})
    assert src.feed_line(line) == "mmwave"
    assert cache.snapshot().mmwave.data["drowsy_state"] == "AWAKE" and cache.snapshot().mmwave.seq == 5
    hb = "UART\t" + json.dumps({"type": TYPE_HEARTBEAT, "seq": 6, "ts_ms": 124,
                                "payload_hex": encode_payload(TYPE_HEARTBEAT, {"uptime_ms": 1}).hex()})
    assert src.feed_line(hb) == "heartbeat" and src.stats.last_heartbeat["uptime_ms"] == 1
    assert src.feed_line("STATE\t{}") is None and parse_uart_line("UART\tnot json") is None
    unknown = "UART\t" + json.dumps({"type": 0x01, "seq": 7, "ts_ms": 1, "payload_hex": "00"})
    assert src.feed_line(unknown) is None and src.stats.unknown_types[0x01] == 1


def test_raw_reader_matches_line_source_and_drops_bad_crc():
    cache = SensorCache()
    reader = RawUartReader(cache)
    good = encode_frame(Frame(TYPE_ENV, 9, 0, encode_payload(TYPE_ENV, ENV)))
    bad = bytearray(encode_frame(Frame(TYPE_MMWAVE, 10, 0, encode_payload(TYPE_MMWAVE, MM))))
    bad[-3] ^= 0xFF
    kinds = reader.feed_bytes(good[:4]) + reader.feed_bytes(good[4:] + bytes(bad))
    assert kinds == ["env"] and reader.stats.dropped == 1
    assert cache.snapshot().env.data["co2_ppm"] == 812 and cache.snapshot().mmwave is None
