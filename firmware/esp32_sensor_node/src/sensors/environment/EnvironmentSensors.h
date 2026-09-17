#pragma once

#include <BH1750.h>
#include <DHT.h>
#include <SensirionI2cScd4x.h>
#include <Wire.h>

#include "EnvironmentSample.h"

namespace deskmate {

class EnvironmentSensors {
 public:
  explicit EnvironmentSensors(TwoWire& wire);

  void begin(uint32_t now_ms, EnvironmentSample& sample);
  void poll(uint32_t now_ms, EnvironmentSample& sample);

 private:
  bool beginScd41(uint32_t now_ms, EnvironmentSample& sample);
  bool beginBh1750(uint32_t now_ms, EnvironmentSample& sample);
  void pollScd41(uint32_t now_ms, EnvironmentSample& sample);
  void pollDht22(uint32_t now_ms, EnvironmentSample& sample);
  void pollBh1750(uint32_t now_ms, EnvironmentSample& sample);
  void updateFreshness(uint32_t now_ms, EnvironmentSample& sample);

  TwoWire& wire_;
  SensirionI2cScd4x scd41_;
  BH1750 bh1750_;
  DHT dht22_;
  bool scd41_initialized_ = false;
  bool bh1750_initialized_ = false;
  uint32_t last_scd41_poll_ms_ = 0;
  uint32_t last_dht22_read_ms_ = 0;
  uint32_t last_bh1750_read_ms_ = 0;
  uint32_t last_scd41_init_attempt_ms_ = 0;
  uint32_t last_bh1750_init_attempt_ms_ = 0;
};

}  // namespace deskmate
