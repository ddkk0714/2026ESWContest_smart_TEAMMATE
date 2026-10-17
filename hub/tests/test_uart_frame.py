"""UART2 프레임 코덱(COBS·CRC·구조체)과 UART 라인 소스. 실보드 없이 검증한다."""
from __future__ import annotations

import json

import pytest

from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.uart_frame import (
    TYPE_ENV, TYPE_HEARTBEAT, TYPE_MMWAVE, TYPE_TOF_FEATURES, TYPE_TOF_GRID, TYPE_TOF_POSTURE, Frame, FrameError,
    TofGridAssembler, cobs_decode, cobs_encode, crc16_ccitt_false, decode_frame, decode_payload, encode_frame,
    encode_payload, encode_tof_sensor_frames, split_stream,
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
# TYPE 0x05 골든 (펌웨어 kGoldenGridPart0Hex/1Hex 와 같은 값): zone i(0..127, 센서 0 = 0..63, 센서 1 = 64..127)는
# i%11==0 이면 타깃 없음(-1/255), 아니면 dist 450+3i, status (5,9,6,0)[i%4]. frame_id 0x2A, sensor_mask 0x03,
# flags 0x02(flip_cols). 센서마다 8×8 프레임 하나, 각 200 B.
GRID_GOLDEN_PART_HEX = (
    "080800022a030200ffffc501c801cb01ce01d101d401d701da01dd01e001ffffe601e901ec01ef01f201f501f801fb01fe010102"
    "ffff07020a020d0210021302160219021c021f022202ffff28022b022e023102340237023a023d0240024302ffff49024c024f02"
    "5202550258025b025e0261026402ffff6a026d0270027302760279027c027f02ff09060005090600050906ff0509060005090600"
    "0509ff00050906000509060005ff06000509060005090600ff09060005090600050906ff0509060005090600",
    "080801022a03020082028502ffff8b028e029102940297029a029d02a002a302a602ffffac02af02b202b502b802bb02be02c102"
    "c402c702ffffcd02d002d302d602d902dc02df02e202e502e802ffffee02f102f402f702fa02fd020003030306030903ffff0f03"
    "1203150318031b031e032103240327032a03ffff30033303360339033c033f030509ff00050906000509060005ff060005090600"
    "05090600ff09060005090600050906ff05090600050906000509ff00050906000509060005ff060005090600",
)


def golden_grid(n=128):
    dist = [-1 if i % 11 == 0 else 450 + 3 * i for i in range(n)]
    status = [255 if i % 11 == 0 else (5, 9, 6, 0)[i % 4] for i in range(n)]
    return dist, status


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


# ── TYPE 0x05 원본 8×8 센서 프레임 (Pi 4 자세 모델 입력, 2026-10-17 표준: 센서별 8×8) ─────────────────

def golden_sensor_frames():
    dist, status = golden_grid()
    return {0: (dist[:64], status[:64]), 1: (dist[64:], status[64:])}


def test_tof_grid_golden_sensor_frames():
    frames = encode_tof_sensor_frames(golden_sensor_frames(), frame_id=0x2A, flags=2)
    assert [p.hex() for p in frames] == list(GRID_GOLDEN_PART_HEX) and all(len(p) == 200 for p in frames)
    asm = TofGridAssembler()
    first = decode_payload(TYPE_TOF_GRID, bytes.fromhex(GRID_GOLDEN_PART_HEX[0]))
    assert (first["grid_width"], first["grid_height"], first["sensor"], first["sensors"]) == (8, 8, 0, 2)
    assert first["frame_id"] == 0x2A and first["sensors_ok"] == 3 and first["flags"] == 2 and first["distance_mm"][0] == -1
    assert asm.push(first) is None
    full = asm.push(decode_payload(TYPE_TOF_GRID, bytes.fromhex(GRID_GOLDEN_PART_HEX[1])))
    dist, status = golden_grid()
    assert full is not None and full["sensors"][0]["distance_mm"] == dist[:64] and full["sensors"][1]["status"] == status[64:]
    assert (full["grid_width"], full["grid_height"], full["frame_id"], full["sensors_ok"], full["flags"]) == (8, 8, 0x2A, 3, 2)
    # 한 프레임은 LEN 상한(256 B, 펌웨어 kFrameMaxPayload·Pi 4 uart_rx) 안에서 프레임이 된다
    wire = encode_frame(Frame(TYPE_TOF_GRID, 1, 1000, frames[0]))
    assert decode_frame(wire).payload == frames[0]


def test_tof_grid_single_sensor_and_bad_headers():
    f = golden_sensor_frames()
    (only_b,) = encode_tof_sensor_frames({1: f[1]}, frame_id=7)           # B 만 살아 있음: 물리 번호 1, sensors 1, mask 0x02
    d = decode_payload(TYPE_TOF_GRID, only_b)
    assert (d["sensor"], d["sensors"], d["sensors_ok"]) == (1, 1, 2) and len(d["distance_mm"]) == 64
    assert TofGridAssembler().push(d)["sensors"].keys() == {1}
    with pytest.raises(FrameError):
        decode_payload(TYPE_TOF_GRID, only_b[:100])                        # 잘림
    with pytest.raises(FrameError):
        decode_payload(TYPE_TOF_GRID, bytes([16, 8, 0, 1, 0, 1, 0, 0]) + bytes(384))   # 16×8 = 128 zone > 64
    with pytest.raises(FrameError):
        decode_payload(TYPE_TOF_GRID, bytes([8, 8, 1, 1, 0, 1, 0, 0]) + bytes(192))    # mask 에 없는 센서


def test_tof_grid_assembler_drops_incomplete_sample():
    f = golden_sensor_frames()
    a = [decode_payload(TYPE_TOF_GRID, p) for p in encode_tof_sensor_frames(f, frame_id=1)]
    b = [decode_payload(TYPE_TOF_GRID, p) for p in encode_tof_sensor_frames(f, frame_id=2)]
    asm = TofGridAssembler()
    assert asm.push(a[0]) is None
    assert asm.push(b[0]) is None and asm.discarded == 1       # 표본 1 의 센서 B 프레임 유실 → 폐기
    assert asm.push(b[1])["frame_id"] == 2 and asm.completed == 1
    # 같은 센서 프레임을 두 번 받아도 완성으로 치지 않는다
    assert asm.push(a[1]) is None and asm.push(a[1]) is None


class _StubPosture:
    def __init__(self):
        self.samples = []

    def update(self, sample, now):
        self.samples.append(sample)
        return {"posture": "recline", "posture_confidence": 0.8, "baseline_ready": True}


def test_tof_grid_lines_assemble_into_cache_and_feed_posture():
    cache = SensorCache()
    stub = _StubPosture()
    src = UartLineSource(cache, posture=stub)
    lines = ["UART\t" + json.dumps({"type": TYPE_TOF_GRID, "seq": 10 + i, "ts_ms": 500, "payload_hex": h})
             for i, h in enumerate(GRID_GOLDEN_PART_HEX)]
    assert src.feed_line(lines[0]) == "tof_grid_part" and cache.tof_grid is None
    assert src.feed_line(lines[1]) == "tof_grid"
    view = cache.snapshot()
    assert view.tof_grid.data["grid_width"] == 8 and view.tof_grid.data["ts_ms"] == 500 and view.tof_grid.seq == 0x2A
    assert view.tof_grid.data["sensors"][0]["distance_mm"] == golden_grid()[0][:64]
    assert view.tof_mlp.data["posture"] == "recline" and stub.samples[0]["frame_id"] == 0x2A
    assert src.stats.grid_parts == 2 and src.stats.grids == 1 and src.stats.unknown_types == {}
    # 모델이 없어도 표본은 캐시에 남는다
    plain = UartLineSource(SensorCache())
    assert [plain.feed_line(l) for l in lines] == ["tof_grid_part", "tof_grid"] and plain.cache.tof_mlp is None
    # raw 리더도 같은 경로
    reader = RawUartReader(SensorCache(), posture=_StubPosture())
    wire = b"".join(encode_frame(Frame(TYPE_TOF_GRID, i, 500, bytes.fromhex(h))) for i, h in enumerate(GRID_GOLDEN_PART_HEX))
    assert reader.feed_bytes(wire) == ["tof_grid_part", "tof_grid"] and reader.cache.tof_mlp is not None


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
