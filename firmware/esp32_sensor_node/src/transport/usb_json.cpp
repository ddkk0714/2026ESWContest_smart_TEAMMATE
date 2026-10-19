#include "usb_json.h"

namespace deskmate {
namespace {

void writeBool(Stream& output, bool value) { output.print(value ? F("true") : F("false")); }

void writeNullable(Stream& output, bool valid, uint16_t value) {
  if (valid) {
    output.print(value);
  } else {
    output.print(F("null"));
  }
}

void writeNullableFloat(Stream& output, bool valid, float value, uint8_t digits) {
  if (valid) {
    output.print(value, digits);
  } else {
    output.print(F("null"));
  }
}

}  // namespace

void writeMmwaveJson(Stream& output, uint32_t now_ms, const MmwaveSample& sample,
                     DrowsyState drowsy_state) {
  // Keep `{"t":` at byte zero: the PC bridge intentionally ignores every
  // other line, allowing human-readable Serial diagnostics to coexist.
  output.print(F("{\"t\":\"mmwave\",\"ms\":"));
  output.print(now_ms);
  output.print(F(",\"present\":"));
  writeBool(output, sample.present);
  output.print(F(",\"motion_state\":\""));
  output.print(motionStateName(sample.motion_state));
  output.print(F("\",\"motion_level\":"));
  output.print(sample.motion_level);
  output.print(F(",\"distance_cm\":"));
  writeNullable(output, sample.distance_valid, sample.distance_cm);
  output.print(F(",\"resp_bpm\":"));
  writeNullable(output, sample.resp_valid, sample.resp_bpm);
  output.print(F(",\"resp_valid\":"));
  writeBool(output, sample.resp_valid);
  output.print(F(",\"heart_bpm\":"));
  writeNullable(output, sample.heart_valid, sample.heart_bpm);
  output.print(F(",\"heart_valid\":"));
  writeBool(output, sample.heart_valid);
  output.print(F(",\"drowsy_state\":\""));
  output.print(drowsyStateName(drowsy_state));
  output.print(F("\",\"valid\":"));
  writeBool(output, sample.valid);
  output.println(F("}"));
}

void writeEnvironmentJson(Stream& output, uint32_t now_ms,
                          const EnvironmentSample& sample) {
  output.print(F("{\"t\":\"env\",\"ms\":"));
  output.print(now_ms);
  output.print(F(",\"co2_ppm\":"));
  writeNullableFloat(output, sample.co2_valid, sample.co2_ppm, 0);
  output.print(F(",\"temp_c\":"));
  writeNullableFloat(output, sample.temp_valid, sample.temp_c, 2);
  output.print(F(",\"humidity_pct\":"));
  writeNullableFloat(output, sample.humidity_valid, sample.humidity_pct, 2);
  output.print(F(",\"lux\":"));
  writeNullableFloat(output, sample.lux_valid, sample.lux, 2);
  output.print(F(",\"co2_valid\":"));
  writeBool(output, sample.co2_valid);
  output.print(F(",\"temp_valid\":"));
  writeBool(output, sample.temp_valid);
  output.print(F(",\"humidity_valid\":"));
  writeBool(output, sample.humidity_valid);
  output.print(F(",\"lux_valid\":"));
  writeBool(output, sample.lux_valid);
  output.println(F("}"));
}

void writeTofJson(Stream& output, uint32_t now_ms, const TofFeatures& f, uint8_t sensor_mask,
                  uint32_t tx_grid, uint32_t tx_feature) {
  output.print(F("{\"t\":\"tof_feature\",\"ms\":"));
  output.print(now_ms);
  output.print(F(",\"present\":"));
  writeBool(output, f.present);
  output.print(F(",\"posture\":\""));
  output.print(tofPostureName(f.posture));
  output.print(F("\",\"posture_confidence\":"));
  output.print(f.posture_confidence, 2);
  output.print(F(",\"motion_score\":"));
  output.print(f.motion_score, 2);
  output.print(F(",\"head_delta_mm\":"));
  writeNullableFloat(output, f.head_delta_valid, f.head_delta_mm, 0);
  output.print(F(",\"nod_rate_hz\":"));
  writeNullableFloat(output, f.nod_rate_valid, f.nod_rate_hz, 3);
  output.print(F(",\"valid_zones\":"));
  output.print(f.valid_zones);
  output.print(F(",\"grid_width\":"));
  output.print(f.grid_width);
  output.print(F(",\"grid_height\":"));
  output.print(f.grid_height);
  output.print(F(",\"coverage_ratio\":"));
  output.print(f.coverage_ratio, 2);
  output.print(F(",\"centroid_depth_mm\":"));
  writeNullableFloat(output, f.geometry_valid, f.centroid_depth_mm, 0);
  output.print(F(",\"head_row_index\":"));
  writeNullableFloat(output, f.geometry_valid, f.head_row_index, 2);
  output.print(F(",\"baseline_ready\":"));
  writeBool(output, f.baseline_ready);
  output.print(F(",\"drowsy\":"));
  writeBool(output, f.drowsy);
  output.print(F(",\"nods_in_window\":"));
  output.print(f.nods_in_window);
  output.print(F(",\"sensors_ok\":"));
  output.print(sensor_mask);
  output.print(F(",\"tx_grid\":"));
  output.print(tx_grid);
  output.print(F(",\"tx_feature\":"));
  output.print(tx_feature);
  output.println(F("}"));
}

void writeTofGridJson(Stream& output, uint32_t now_ms, uint8_t frame_id, uint8_t sensor_mask, uint8_t flags,
                      uint8_t w, uint8_t h, uint8_t count, const int16_t* const* dist, const uint8_t* const* status) {
  output.print(F("{\"t\":\"tof_grid\",\"ms\":"));
  output.print(now_ms);
  output.print(F(",\"frame\":"));
  output.print(frame_id);
  output.print(F(",\"sensors_ok\":"));
  output.print(sensor_mask);
  output.print(F(",\"w\":"));
  output.print(w);
  output.print(F(",\"h\":"));
  output.print(h);
  output.print(F(",\"flags\":"));
  output.print(flags);
  output.print(F(",\"s\":["));
  const uint16_t zones = static_cast<uint16_t>(w) * h;
  for (uint8_t i = 0; i < count; ++i) {
    if (i > 0) output.print(',');
    if ((sensor_mask & (1u << i)) == 0) {
      output.print(F("null"));
      continue;
    }
    output.print(F("{\"d\":["));
    for (uint16_t z = 0; z < zones; ++z) {
      if (z > 0) output.print(',');
      output.print(dist[i][z]);
    }
    output.print(F("],\"st\":["));
    for (uint16_t z = 0; z < zones; ++z) {
      if (z > 0) output.print(',');
      output.print(status[i][z]);
    }
    output.print(F("]}"));
  }
  output.println(F("]}"));
}

}  // namespace deskmate
