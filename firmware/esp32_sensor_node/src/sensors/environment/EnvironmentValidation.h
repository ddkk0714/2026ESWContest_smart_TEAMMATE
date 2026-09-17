#pragma once

#include <stdint.h>

namespace deskmate::environment_validation {

struct Dht22Validity {
  bool temp_valid;
  bool humidity_valid;

  bool allValid() const { return temp_valid && humidity_valid; }
  bool anyValid() const { return temp_valid || humidity_valid; }
};

bool validateScd41Co2(float co2_ppm);
Dht22Validity validateDht22(float temp_c, float humidity_pct);
bool validateBh1750(float lux);
uint32_t sampleAgeMs(uint32_t now_ms, uint32_t last_success_ms,
                     bool has_success);
bool isStale(uint32_t now_ms, uint32_t last_success_ms, bool has_success,
             uint32_t stale_after_ms);

}  // namespace deskmate::environment_validation
