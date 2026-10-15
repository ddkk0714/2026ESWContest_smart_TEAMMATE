"""Real ESP32 feature/frame encoders and L8 driver, with a fake I2C sensor."""
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config
from deskmate_hub.ingest.uart_frame import TYPE_TOF_FEATURES, Frame, FrameError, decode_frame, decode_payload, encode_payload
from deskmate_hub.ingest.uart_source import RawUartReader, UartLineSource, UartStats, ingest_frame
from deskmate_hub.inference import State
from deskmate_hub.live import frame_to_dict
from deskmate_hub.personalization.contract import feature_values


@pytest.fixture(scope="module")
def firmware(tmp_path_factory):
    compiler = shutil.which("g++")
    command = [compiler] if compiler else [sys.executable, "-m", "ziglang", "c++"]
    if not compiler and importlib.util.find_spec("ziglang") is None:
        pytest.skip("C++ host compiler required (g++ or ziglang)")
    root = Path(__file__).resolve().parents[2] / "firmware/esp32_sensor_node"
    source = root / "src"
    directory = tmp_path_factory.mktemp("tof-uart")
    (directory / "Arduino.h").write_text(r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <vector>
extern uint32_t clock_ms;
inline uint32_t millis() { return clock_ms; }
struct Stream {
  std::vector<uint8_t> bytes;
  size_t write(const uint8_t *p, size_t n) { bytes.insert(bytes.end(), p, p+n); return n; }
  size_t write(uint8_t b) { bytes.push_back(b); return 1; }
};
''', encoding="utf-8")
    (directory / "Wire.h").write_text(r'''
#pragma once
#include "Arduino.h"
extern int probe_error;
struct TwoWire {
  void beginTransmission(int) {}
  int endTransmission() { return probe_error; }
};
''', encoding="utf-8")
    (directory / "vl53l8cx.h").write_text(r'''
#pragma once
#include "Wire.h"
#define VL53L8CX_STATUS_OK 0
#define VL53L8CX_RESOLUTION_8X8 64
#define VL53L8CX_TARGET_ORDER_CLOSEST 1
#define VL53L8CX_RANGING_MODE_CONTINUOUS 1
#define VL53L8CX_NB_TARGET_PER_ZONE 1
extern int init_error, check_error, read_error, init_calls;
extern bool data_ready, empty_targets;
struct VL53L8CX_ResultsData {
  uint8_t nb_target_detected[64], target_status[64];
  int16_t distance_mm[64];
};
struct VL53L8CX {
  VL53L8CX(TwoWire *, int) {}
  void begin() {}
  int init() { ++init_calls; return init_error; }
  int set_resolution(int) { return 0; }
  int set_ranging_frequency_hz(int) { return 0; }
  int set_target_order(int) { return 0; }
  int set_ranging_mode(int) { return 0; }
  int start_ranging() { return 0; }
  int check_data_ready(uint8_t *v) { *v = data_ready; return check_error; }
  int get_ranging_data(VL53L8CX_ResultsData *r) {
    for (int i=0; i<64; ++i) {
      r->nb_target_detected[i] = empty_targets ? 0 : 1;
      r->target_status[i] = 5; r->distance_mm[i] = 600;
    }
    return read_error;
  }
};
''', encoding="utf-8")
    (directory / "main.cpp").write_text(r'''
#include <cassert>
#include <cstdio>
#include <limits>
#include <string>
#include "sensors/tof/TofSensor.h"
#include "preprocess/tof/TofFeaturePayload.h"
#include "transport/frame.h"
uint32_t clock_ms = 0;
int probe_error=0, init_error=0, check_error=0, read_error=0, init_calls=0;
bool data_ready=true, empty_targets=false;
int main(int argc, char **argv) {
  using namespace deskmate;
  std::string mode = argc > 1 ? argv[1] : "normal";
  TwoWire wire;
  TofSensor sensor(wire);
  TofFeatures f;
  bool valid = true;
  if (mode.find("sensor-") == 0) {
    if (mode == "sensor-missing") probe_error = 1;
    if (mode == "sensor-init-fail") init_error = 1;
    bool event = sensor.poll(clock_ms);
    if (probe_error || init_error) {
      assert(event && !sensor.valid());
      assert(!sensor.poll(9999));
      if (probe_error) assert(init_calls == 0);
    } else {
      assert(!event);
      for (clock_ms=67; clock_ms<13000; clock_ms+=67) {
        assert(sensor.poll(clock_ms));
      }
      assert(sensor.valid() && sensor.features().baseline_ready);
      if (mode == "sensor-check-fail") check_error=1;
      if (mode == "sensor-read-fail") read_error=1;
      if (mode == "sensor-empty") empty_targets=true;
      if (mode == "sensor-stale" || mode == "sensor-reconnect") {
        data_ready=false;
        assert(!sensor.poll(clock_ms));
        clock_ms = sensor.timestampMs() + DESKMATE_TOF_STALE_MS - 1;
        assert(!sensor.poll(clock_ms));
        ++clock_ms;
      }
      assert(sensor.poll(clock_ms));
      if (mode == "sensor-empty") {
        for (int i=0; i<4; ++i) { clock_ms+=67; assert(sensor.poll(clock_ms)); }
        assert(!sensor.valid());
      }
      if (mode != "sensor-empty") assert(!sensor.features().baseline_ready);
      if (mode == "sensor-reconnect") {
        data_ready=true;
        clock_ms += DESKMATE_TOF_RETRY_MS;
        assert(!sensor.poll(clock_ms));
        clock_ms += 67;
        assert(sensor.poll(clock_ms) && sensor.valid());
        assert(!sensor.features().baseline_ready);
      }
    }
    f = sensor.features(); valid = sensor.valid();
  } else {
    f.present=true; f.posture=TofPosture::LeanForward;
    f.posture_confidence=0.9f; f.motion_score=0.2f;
    f.baseline_ready=true; f.geometry_valid=true; f.baseline_deviation=0.6f;
    f.grid_width=f.grid_height=8; f.valid_zones=40; f.coverage_ratio=0.5f;
    if (mode == "warming") f.baseline_ready=false;
    if (mode == "away") { f.present=false; f.posture=TofPosture::Away; }
    if (mode == "optional") { f.head_delta_valid=f.nod_rate_valid=true;
      f.head_delta_mm=-80; f.nod_rate_hz=0.2f; }
    if (mode == "nan") f.motion_score=std::numeric_limits<float>::quiet_NaN();
    if (mode == "failed") valid=false;
    if (mode == "rollover") {
      clock_ms=0xFFFFFF00; assert(!sensor.poll(clock_ms));
      clock_ms+=67; assert(sensor.poll(clock_ms) && sensor.valid());
      data_ready=false; clock_ms+=DESKMATE_TOF_STALE_MS;
      assert(sensor.poll(clock_ms) && !sensor.valid());
    }
  }
  uint8_t payload[kTofFeatureBinarySize];
  assert(encodeTofFeatureBinary(nullptr, sizeof(payload), f, valid) == 0);
  assert(encodeTofFeatureBinary(payload, sizeof(payload)-1, f, valid) == 0);
  assert(encodeTofFeatureBinary(payload, sizeof(payload), f, valid) == sizeof(payload));
  Stream output;
  assert(writeUartFrame(output, kFrameTypeTofFeatures, 7, clock_ms, payload, sizeof(payload)));
  for (auto b : output.bytes) std::printf("%02x", static_cast<unsigned>(b));
}
''', encoding="utf-8")
    binary = directory / ("firmware.exe" if sys.platform == "win32" else "firmware")
    subprocess.run(command + ["-std=c++17", "-Wall", "-Wextra", "-DDESKMATE_TOF_MAX_ZONES=64",
                   "-I", str(directory), "-I", str(source), "-I", str(root / "include"),
                   str(directory / "main.cpp"), str(source / "sensors/tof/TofSensor.cpp"),
                   str(source / "preprocess/tof/TofFeaturePayload.cpp"),
                   str(source / "preprocess/tof/TofPosture.cpp"), str(source / "transport/frame.cpp"),
                   "-o", str(binary)], check=True, capture_output=True, text=True)

    def run(mode="normal"):
        output = subprocess.run([str(binary), mode], check=True, capture_output=True, text=True)
        return bytes.fromhex(output.stdout)
    return run


def model_frame(cache, now=100, enabled=True):
    cfg = load_ingest_config()
    cfg["tof"]["enabled"] = enabled
    return build_frame(cache.snapshot(), now, cfg, SessionTracker(), fsm_state=State.IDLE)


def test_real_firmware_wire_reaches_cache_and_personalization(firmware):
    wire = firmware()
    frame = decode_frame(wire)
    assert frame.type == TYPE_TOF_FEATURES and frame.seq == 7 and len(frame.payload) == 32
    cache = SensorCache()
    assert ingest_frame(cache, frame, UartStats(), received=100) == "tof"
    result = model_frame(cache)
    assert result.present and result.signals["posture"].available
    assert feature_values(frame_to_dict(result))[3:6] == pytest.approx([0.2, 0.6, 1.0])
    assert "d" not in cache.tof.data and "st" not in cache.tof.data
    assert not model_frame(cache, enabled=False).signals["posture"].available
    assert not model_frame(cache, now=106).signals["posture"].available


def test_native_bridge_and_fragmented_serial_use_same_features(firmware):
    frame = decode_frame(firmware())
    cache = SensorCache()
    source = UartLineSource(cache)
    assert source.feed_line("UART\t" + json.dumps({"type": frame.type, "seq": frame.seq,
        "ts_ms": frame.ts_ms, "payload_hex": frame.payload_hex})) == "tof"
    other = SensorCache()
    reader = RawUartReader(other)
    wire = firmware()
    assert reader.feed_bytes(wire[:5]) == []
    assert reader.feed_bytes(wire[5:]) == ["tof"]
    assert other.tof.data == cache.tof.data
    bad = bytearray(wire)
    bad[-3] ^= 0x80
    assert reader.feed_bytes(bytes(bad)) == [] and reader.stats.dropped == 1


@pytest.mark.parametrize("mode", ["failed", "nan", "sensor-missing", "sensor-init-fail",
    "sensor-check-fail", "sensor-read-fail", "sensor-stale", "sensor-empty"])
def test_sensor_failure_invalidates_previous_evidence(firmware, mode):
    cache, stats = SensorCache(), UartStats()
    ingest_frame(cache, decode_frame(firmware()), stats, received=100)
    ingest_frame(cache, decode_frame(firmware(mode)), stats, received=101)
    assert cache.tof.data["valid"] is False
    assert cache.tof.data["baseline_deviation"] is None
    assert not model_frame(cache, now=101).signals["posture"].available
    assert not model_frame(cache, now=101).present


@pytest.mark.parametrize("mode", ["warming", "away", "sensor-reconnect"])
def test_unavailable_baseline_or_absence_is_not_fatigue(firmware, mode):
    cache = SensorCache()
    ingest_frame(cache, decode_frame(firmware(mode)), UartStats(), received=100)
    assert not model_frame(cache).signals["posture"].available


def test_optional_features_and_millis_rollover(firmware):
    data = decode_payload(TYPE_TOF_FEATURES, decode_frame(firmware("optional")).payload)
    assert data["head_delta_mm"] == -80 and data["nod_rate_hz"] == pytest.approx(0.2)
    firmware("rollover")  # Real driver assertions cover wrap-safe staleness.


@pytest.mark.parametrize("offset,value", [(0, 2), (1, 0x80), (2, 255), (3, 0), (5, 1), (6, 65)])
def test_malformed_payload_is_dropped(firmware, offset, value):
    payload = bytearray(decode_frame(firmware()).payload)
    payload[offset] = value
    cache, stats = SensorCache(), UartStats()
    assert ingest_frame(cache, Frame(TYPE_TOF_FEATURES, 1, 0, payload), stats, received=100) is None
    assert cache.tof is None and stats.dropped == 1


def test_nonfinite_and_wrong_length_payloads_rejected(firmware):
    payload = decode_frame(firmware()).payload
    for malformed in (payload[:-1], payload + b"\0", payload[:12] + struct.pack("<f", float("nan")) + payload[16:]):
        with pytest.raises(FrameError):
            decode_payload(TYPE_TOF_FEATURES, malformed)


def test_missing_sensor_never_creates_presence():
    assert not model_frame(SensorCache()).present


@pytest.mark.parametrize("mode", ["normal", "warming", "away", "optional", "nan", "failed", "sensor-missing"])
def test_pc_reference_encoder_matches_real_firmware(firmware, mode):
    payload = decode_frame(firmware(mode)).payload
    assert encode_payload(TYPE_TOF_FEATURES, decode_payload(TYPE_TOF_FEATURES, payload)) == payload


@pytest.mark.parametrize("changes", [
    {"valid": "true"}, {"present": 1}, {"posture": "invented"},
    {"baseline_ready": "false"}, {"grid_width": 256}, {"valid_zones": 65},
    {"motion_score": float("nan")}, {"coverage_ratio": None},
    {"baseline_deviation": -1}, {"head_delta_mm": float("inf")},
])
def test_pc_encoder_rejects_unusable_feature_input(firmware, changes):
    data = decode_payload(TYPE_TOF_FEATURES, decode_frame(firmware()).payload)
    with pytest.raises(FrameError):
        encode_payload(TYPE_TOF_FEATURES, dict(data, **changes))


def test_cli_tof_vector_matches_firmware(firmware):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "tools/uart_frame_tool.py"),
        "encode", "tof", "--seq", "7", "--ts", "1000"],
        check=True, capture_output=True, text=True)
    wire = bytes.fromhex(result.stdout)
    assert wire.hex() == "05a5010307022003e8030106010702080802280d6666663fcdcc4c3e9a99193f0101023f0101010101010103c4c100"
    assert decode_frame(wire).payload == decode_frame(firmware()).payload


def test_pc_encoder_never_serializes_raw_or_unknown_fields(firmware):
    payload = decode_frame(firmware()).payload
    data = decode_payload(TYPE_TOF_FEATURES, payload)
    data.update(d=[600] * 64, st=[5] * 64, raw_depth=[600] * 64)
    assert encode_payload(TYPE_TOF_FEATURES, data) == payload
