#include "TofFeaturePayload.h"

#include <cmath>
#include <cstdio>

namespace deskmate {
namespace {

bool unit(float value) {
  return std::isfinite(value) && value >= 0.0f && value <= 1.0f;
}

const char *number(char *buf, float value, bool valid) {
  if (!valid || !std::isfinite(value)) return "null";
  std::snprintf(buf, 32, "%.6g", static_cast<double>(value));
  return buf;
}

}  // namespace

size_t formatTofFeaturePayload(char *buf, size_t cap, const TofFeatures &f, bool valid) {
  if (buf == nullptr || cap == 0) return 0;
  const unsigned zones = static_cast<unsigned>(f.grid_width) * f.grid_height;
  valid = valid && zones > 0 && zones <= kTofMaxZones && f.valid_zones > 0 && f.valid_zones <= zones &&
          unit(f.posture_confidence) && unit(f.motion_score) && unit(f.coverage_ratio);
  const bool baseline = valid && f.baseline_ready && f.geometry_valid;
  if (baseline && (!std::isfinite(f.baseline_deviation) || f.baseline_deviation < 0.0f)) {
    valid = false;
  }
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
