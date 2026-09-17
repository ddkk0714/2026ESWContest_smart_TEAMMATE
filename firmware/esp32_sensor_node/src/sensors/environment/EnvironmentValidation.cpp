#include "EnvironmentValidation.h"

#include <cmath>
#include <limits>

namespace deskmate::environment_validation {
namespace {

bool inRange(float value, float minimum, float maximum) {
  return std::isfinite(value) && value >= minimum && value <= maximum;
}

}  // namespace

bool validateScd41Co2(float co2_ppm) {
  return inRange(co2_ppm, 0.0F, 40000.0F);
}

Dht22Validity validateDht22(float temp_c, float humidity_pct) {
  return {
      inRange(temp_c, -40.0F, 80.0F),
      inRange(humidity_pct, 0.0F, 100.0F),
  };
}

bool validateBh1750(float lux) {
  return inRange(lux, 0.0F, 54612.5F);
}

uint32_t sampleAgeMs(uint32_t now_ms, uint32_t last_success_ms,
                     bool has_success) {
  if (!has_success) return std::numeric_limits<uint32_t>::max();
  return now_ms - last_success_ms;
}

bool isStale(uint32_t now_ms, uint32_t last_success_ms, bool has_success,
             uint32_t stale_after_ms) {
  return !has_success || (now_ms - last_success_ms) > stale_after_ms;
}

}  // namespace deskmate::environment_validation
