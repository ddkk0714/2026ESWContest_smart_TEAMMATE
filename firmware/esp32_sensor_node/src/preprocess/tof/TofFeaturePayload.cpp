#include "TofFeaturePayload.h"

#include <cmath>
#include <cstdio>
#include <cstring>

namespace deskmate {
namespace {

bool unit(float value) {
  return std::isfinite(value) && value >= 0.0f && value <= 1.0f;
}

bool usable(const TofFeatures &f, bool valid) {
  const unsigned zones = static_cast<unsigned>(f.grid_width) * f.grid_height;
  return valid && zones > 0 && zones <= kTofMaxZones && f.valid_zones > 0 &&
         f.valid_zones <= zones && unit(f.posture_confidence) && unit(f.motion_score) &&
         unit(f.coverage_ratio) && static_cast<unsigned>(f.posture) <= 7 &&
         (!(f.baseline_ready && f.geometry_valid) ||
          (std::isfinite(f.baseline_deviation) && f.baseline_deviation >= 0.0f));
}

void putFloat(uint8_t *out, float value) {
  static_assert(sizeof(float) == 4, "float32 required");
  uint32_t bits;
  std::memcpy(&bits, &value, sizeof(bits));
  for (unsigned i = 0; i < 4; ++i) out[i] = static_cast<uint8_t>(bits >> (8 * i));
}

const char *number(char *buf, float value, bool valid) {
  if (!valid || !std::isfinite(value)) return "null";
  std::snprintf(buf, 32, "%.6g", static_cast<double>(value));
  return buf;
}

}  // namespace

size_t encodeTofFeatureBinary(uint8_t *buf, size_t cap, const TofFeatures &f, bool valid) {
  if (buf == nullptr || cap < kTofFeatureBinarySize) return 0;
  valid = usable(f, valid);
  const bool baseline = valid && f.baseline_ready && f.geometry_valid;
  const bool head = valid && f.head_delta_valid && std::isfinite(f.head_delta_mm);
  const bool nod = valid && f.nod_rate_valid && std::isfinite(f.nod_rate_hz) && f.nod_rate_hz >= 0;
  std::memset(buf, 0, kTofFeatureBinarySize);
  buf[0] = 1;
  buf[1] = (valid ? 1 : 0) | (valid && f.present ? 2 : 0) | (baseline ? 4 : 0) |
           (head ? 8 : 0) | (nod ? 16 : 0);
  buf[2] = valid ? static_cast<uint8_t>(f.posture) : 0;
  buf[3] = f.grid_width;
  buf[4] = f.grid_height;
  buf[6] = static_cast<uint8_t>(f.valid_zones);
  buf[7] = static_cast<uint8_t>(f.valid_zones >> 8);
  putFloat(buf + 8, valid ? f.posture_confidence : 0);
  putFloat(buf + 12, valid ? f.motion_score : 0);
  putFloat(buf + 16, baseline ? f.baseline_deviation : 0);
  putFloat(buf + 20, valid ? f.coverage_ratio : 0);
  putFloat(buf + 24, head ? f.head_delta_mm : 0);
  putFloat(buf + 28, nod ? f.nod_rate_hz : 0);
  return kTofFeatureBinarySize;
}

size_t formatTofFeaturePayload(char *buf, size_t cap, const TofFeatures &f, bool valid) {
  if (buf == nullptr || cap == 0) return 0;
  valid = usable(f, valid);
  const bool baseline = valid && f.baseline_ready && f.geometry_valid;
  char confidence[32], motion[32], deviation[32], head[32], nod[32], coverage[32];
  const int n = std::snprintf(
      buf, cap,
      "{\"present\":%s,\"valid\":%s,\"posture\":\"%s\","
      "\"posture_confidence\":%s,\"motion_score\":%s,"
      "\"baseline_ready\":%s,\"baseline_deviation\":%s,"
      "\"grid_width\":%u,\"grid_height\":%u,\"valid_zones\":%u,"
      "\"coverage_ratio\":%s,\"head_delta_mm\":%s,\"nod_rate_hz\":%s}",
      valid && f.present ? "true" : "false", valid ? "true" : "false",
      valid ? tofPostureName(f.posture) : "unknown",
      number(confidence, f.posture_confidence, valid), number(motion, f.motion_score, valid),
      valid && baseline ? "true" : "false", number(deviation, f.baseline_deviation, valid && baseline),
      f.grid_width, f.grid_height, f.valid_zones, number(coverage, f.coverage_ratio, valid),
      number(head, f.head_delta_mm, valid && f.head_delta_valid),
      number(nod, f.nod_rate_hz, valid && f.nod_rate_valid && f.nod_rate_hz >= 0.0f));
  if (n < 0 || static_cast<size_t>(n) >= cap) {
    buf[0] = '\0';
    return 0;
  }
  return static_cast<size_t>(n);
}

}  // namespace deskmate
