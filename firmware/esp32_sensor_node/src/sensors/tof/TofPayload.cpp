#include "sensors/tof/TofPayload.h"

#include <math.h>

namespace deskmate {
namespace {

uint8_t pct(float v, float limit = 100.0f) {
  if (!(v == v)) return 0;  // NaN
  float scaled = v * 100.0f;
  if (scaled < 0.0f) scaled = 0.0f;
  if (scaled > limit) scaled = limit;
  return static_cast<uint8_t>(lroundf(scaled));
}

uint16_t u16(float v) {
  if (!(v == v) || v < 0.0f) return 0;
  if (v > 65535.0f) return 65535;
  return static_cast<uint16_t>(lroundf(v));
}

uint8_t u8(float v) {
  if (!(v == v) || v < 0.0f) return 0;
  if (v > 255.0f) return 255;
  return static_cast<uint8_t>(lroundf(v));
}

int16_t i16(float v) {
  if (!(v == v)) return 0;
  if (v < -32000.0f) return -32000;
  if (v > 32000.0f) return 32000;
  return static_cast<int16_t>(lroundf(v));
}

void putU16(uint8_t *out, uint16_t v) {
  out[0] = static_cast<uint8_t>(v & 0xFF);
  out[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
}

void putI16(uint8_t *out, int16_t v) { putU16(out, static_cast<uint16_t>(v)); }

}  // namespace

void encodeTofFeaturePayload(const TofFeatures &f, uint8_t sensor_mask,
                             uint8_t out[kTofFeaturePayloadSize]) {
  out[0] = f.present ? 1 : 0;
  out[1] = static_cast<uint8_t>(f.posture);
  out[2] = pct(f.posture_confidence);
  out[3] = pct(f.motion_score);
  putI16(out + 4, f.head_delta_valid ? i16(f.head_delta_mm) : kTofNullI16);
  putU16(out + 6, f.nod_rate_valid ? u16(f.nod_rate_hz * 1000.0f) : kTofNullU16);
  putU16(out + 8, f.valid_zones);
  out[10] = f.grid_width;
  out[11] = f.grid_height;
  out[12] = pct(f.coverage_ratio);
  putU16(out + 13, f.presence_count);
  putU16(out + 15, f.geometry_valid ? u16(f.centroid_depth_mm) : kTofNullU16);
  out[17] = f.geometry_valid ? pct(f.head_row_index) : kTofNullU8;
  putI16(out + 18, f.shoulder_tilt_valid ? i16(f.shoulder_tilt_mm) : kTofNullI16);
  putU16(out + 20, u16(f.motion_indicator_mm_s));
  out[22] = u8(f.posture_change_rate_per_min);
  out[23] = pct(f.baseline_deviation, 255.0f);
  uint8_t flags = 0;
  if (f.baseline_ready) flags |= kTofFlagBaselineReady;
  if (f.drowsy) flags |= kTofFlagDrowsy;
  if (f.geometry_valid) flags |= kTofFlagGeometryValid;
  if (sensor_mask & 0x01) flags |= kTofFlagSensorA;
  if (sensor_mask & 0x02) flags |= kTofFlagSensorB;
  out[24] = flags;
  out[25] = f.nods_in_window;
}

void encodeTofPosturePayload(const TofFeatures &f, uint8_t out[kTofPosturePayloadSize]) {
  out[0] = f.present ? 1 : 0;
  out[1] = static_cast<uint8_t>(f.posture);
  out[2] = pct(f.posture_confidence);
  out[3] = f.drowsy ? 1 : 0;
}

uint16_t encodeTofSensorFrame(const int16_t *dist, const uint8_t *status, uint8_t grid_w, uint8_t grid_h,
                              uint8_t sensor, uint8_t sensors, uint8_t frame_id, uint8_t sensor_mask,
                              uint8_t flags, uint8_t out[kTofGridMaxPayloadSize]) {
  const uint16_t n = static_cast<uint16_t>(grid_w) * grid_h;
  if (n == 0 || n > kTofGridMaxZones || sensors == 0 || sensor >= 8 || !((sensor_mask >> sensor) & 1) ||
      dist == nullptr || status == nullptr) {
    return 0;
  }
  out[0] = grid_w;
  out[1] = grid_h;
  out[2] = sensor;
  out[3] = sensors;
  out[4] = frame_id;
  out[5] = sensor_mask;
  out[6] = flags;
  out[7] = 0;
  uint8_t *d = out + kTofGridHeaderSize;
  for (uint16_t i = 0; i < n; ++i) putI16(d + 2 * i, dist[i]);
  uint8_t *s = d + 2 * n;
  for (uint16_t i = 0; i < n; ++i) s[i] = status[i];
  return static_cast<uint16_t>(kTofGridHeaderSize + 3 * n);
}

}  // namespace deskmate
