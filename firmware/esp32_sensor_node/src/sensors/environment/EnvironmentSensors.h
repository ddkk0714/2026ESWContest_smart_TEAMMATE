#pragma once

#include <Arduino.h>

namespace deskmate {

struct EnvironmentSample {
  int co2_ppm = 0;
  float temp_c = 0.0F;
  float humidity_pct = 0.0F;
  float lux = 0.0F;
  bool co2_valid = false;
  bool temp_valid = false;
  bool humidity_valid = false;
  bool lux_valid = false;
};

class EnvironmentSensors {
 public:
  void begin();
  EnvironmentSample read();

 private:
  bool scd41_ready_ = false;
  bool bh1750_ready_ = false;
  bool dht22_ready_ = false;
};

}  // namespace deskmate
