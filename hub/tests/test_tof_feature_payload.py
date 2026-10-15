"""Compile the real feature serializer and feed its output into the hub."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config
from deskmate_hub.ingest.protocol import parse_sensor_message
from deskmate_hub.inference import State


@pytest.fixture(scope="module")
def encoder(tmp_path_factory):
    compiler = shutil.which("g++")
    command = [compiler] if compiler else [sys.executable, "-m", "ziglang", "c++"]
    if not compiler and importlib.util.find_spec("ziglang") is None:
        pytest.skip("C++ host compiler required (g++ or ziglang)")
    root = Path(__file__).resolve().parents[2]
    source = root / "firmware/esp32_sensor_node/src"
    directory = tmp_path_factory.mktemp("tof-feature-payload")
    driver = directory / "main.cpp"
    driver.write_text(r'''
#include <cstdio>
#include <limits>
#include <string>
#include "preprocess/tof/TofFeaturePayload.h"
int main(int argc, char **argv) {
  using namespace deskmate;
  const std::string mode = argc > 1 ? argv[1] : "normal";
  TofFeatures f;
  f.present = true; f.posture = TofPosture::LeanForward;
  f.posture_confidence = 0.9f; f.motion_score = 0.2f;
  f.baseline_ready = true; f.geometry_valid = true; f.baseline_deviation = 0.6f;
  f.grid_width = 8; f.grid_height = 8; f.valid_zones = 40; f.coverage_ratio = 0.5f;
  if (mode == "warming") f.baseline_ready = false;
  if (mode == "nan") f.motion_score = std::numeric_limits<float>::quiet_NaN();
  if (mode == "invalid-deviation") f.baseline_deviation = -1.0f;
  if (mode == "bad-grid") f.valid_zones = 65;
  if (mode == "no-valid-zones") f.valid_zones = 0;
  if (mode == "away") { f.present = false; f.posture = TofPosture::Away; }
  char buf[kTofFeaturePayloadMax];
  const size_t cap = mode == "small" ? 8 : sizeof(buf);
  const size_t n = formatTofFeaturePayload(buf, cap, f, mode != "failed-read");
  if (mode == "small") {
    std::printf("%zu %d", n, static_cast<int>(buf[0]));
  } else if (n > 0) {
    std::fwrite(buf, 1, n, stdout);
  } else { return 1; }
}
''', encoding="utf-8")
    binary = directory / "encoder"
    subprocess.run(command + ["-std=c++17", "-Wall", "-Wextra", "-I", str(source),
                    str(driver), str(source / "preprocess/tof/TofFeaturePayload.cpp"),
                    str(source / "preprocess/tof/TofPosture.cpp"), "-o", str(binary)],
                   check=True, capture_output=True, text=True)

    def run(mode="normal"):
        return subprocess.run([str(binary), mode], check=True, capture_output=True, text=True).stdout
    return run


def test_cpp_feature_payload_matches_hub_mapping_without_raw(encoder):
    data = json.loads(encoder())
    assert set(data) == {"present", "valid", "posture", "posture_confidence", "motion_score",
                         "baseline_ready", "baseline_deviation", "grid_width", "grid_height",
                         "valid_zones", "coverage_ratio", "head_delta_mm", "nod_rate_hz"}
    assert data["head_delta_mm"] is None and data["nod_rate_hz"] is None
    parsed = parse_sensor_message("deskmate/sensor/tof/desk",
                                  json.dumps({"ts": 100, "data": data}).encode(), 100)
    assert parsed is not None
    cache = SensorCache()
    cache.put(*parsed)
    cfg = load_ingest_config()
    cfg["tof"]["enabled"] = True
    frame = build_frame(cache.snapshot(), 100, cfg, SessionTracker(), fsm_state=State.IDLE)
    assert frame.present and frame.signals["posture"].available
    assert frame.signals["posture"].phi == pytest.approx(0.2)
    assert frame.signals["posture"].delta == pytest.approx(0.6)


@pytest.mark.parametrize("mode", ["failed-read", "nan", "invalid-deviation", "bad-grid", "no-valid-zones"])
def test_failed_or_invalid_sensor_update_cannot_reuse_old_features(encoder, mode):
    data = json.loads(encoder(mode))
    assert data["valid"] is False and data["present"] is False
    assert data["posture"] == "unknown" and data["baseline_ready"] is False
    assert data["motion_score"] is None and data["baseline_deviation"] is None
    cache = SensorCache()
    for now, sample in ((100, json.loads(encoder())), (101, data)):
        parsed = parse_sensor_message("deskmate/sensor/tof/desk",
                                      json.dumps({"ts": now, "data": sample}).encode(), now)
        assert parsed is not None
        cache.put(*parsed)
    cfg = load_ingest_config()
    cfg["tof"]["enabled"] = True
    frame = build_frame(cache.snapshot(), 101, cfg, SessionTracker(), fsm_state=State.IDLE)
    assert not frame.present and not frame.signals["posture"].available


def test_baseline_warmup_does_not_publish_fake_zero_deviation(encoder):
    data = json.loads(encoder("warming"))
    assert data["valid"] and not data["baseline_ready"]
    assert data["baseline_deviation"] is None


def test_away_is_not_a_fatigue_label(encoder):
    data = json.loads(encoder("away"))
    assert data["valid"] and not data["present"] and data["posture"] == "away"


def test_short_output_buffer_is_empty_and_cannot_be_sent_as_json(encoder):
    assert encoder("small") == "0 0"
