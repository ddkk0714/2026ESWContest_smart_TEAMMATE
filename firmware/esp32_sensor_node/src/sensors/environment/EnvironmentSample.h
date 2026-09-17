#pragma once

#include <stdint.h>

namespace deskmate {

struct SensorMetadata {
  bool initialized = false;
  bool has_success = false;
  bool valid = false;
  bool stale = true;
  bool updated = false;
  uint32_t last_attempt_ms = 0;
  uint32_t last_success_ms = 0;
  uint32_t age_ms = UINT32_MAX;
  uint32_t read_failures = 0;
};

struct EnvironmentSample {
  float co2_ppm = 0.0F;
  float temp_c = 0.0F;
  float humidity_pct = 0.0F;
  float lux = 0.0F;
  bool co2_valid = false;
  bool temp_valid = false;
  bool humidity_valid = false;
  bool lux_valid = false;

  SensorMetadata scd41;
  SensorMetadata dht22;
  SensorMetadata bh1750;
};

}  // namespace deskmate
