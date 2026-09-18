#include "EnvironmentSensors.h"

#include <Arduino.h>

#include "EnvironmentValidation.h"
#include "pins.h"

namespace deskmate {
namespace {

constexpr int16_t kNoError = 0;

}  // namespace

EnvironmentSensors::EnvironmentSensors(TwoWire& wire)
    : wire_(wire), bh1750_(kBh1750Address), dht22_(kDht22DataPin, DHT22) {}

void EnvironmentSensors::begin(uint32_t now_ms, EnvironmentSample& sample) {
  wire_.begin(kEnvironmentSdaPin, kEnvironmentSclPin);
  dht22_.begin();
  sample.dht22.initialized = true;

  last_scd41_init_attempt_ms_ = now_ms - kEnvironmentSensorRetryMs;
  last_bh1750_init_attempt_ms_ = now_ms - kEnvironmentSensorRetryMs;
  last_dht22_read_ms_ = now_ms;
}

bool EnvironmentSensors::beginScd41(uint32_t now_ms,
                                    EnvironmentSample& sample) {
  last_scd41_init_attempt_ms_ = now_ms;
  scd41_.begin(wire_, SCD41_I2C_ADDR_62);

  const int16_t error = scd41_.startPeriodicMeasurement();
  scd41_initialized_ = error == kNoError;
  sample.scd41.initialized = scd41_initialized_;
  if (!scd41_initialized_) {
    ++sample.scd41.read_failures;
    Serial.print(F("SCD41 initialization failed, error="));
    Serial.println(error);
  } else {
    Serial.println(F("SCD41 periodic measurement started"));
  }
  return scd41_initialized_;
}

bool EnvironmentSensors::beginBh1750(uint32_t now_ms,
                                     EnvironmentSample& sample) {
  last_bh1750_init_attempt_ms_ = now_ms;
  bh1750_initialized_ = bh1750_.begin(BH1750::CONTINUOUS_HIGH_RES_MODE,
                                      kBh1750Address, &wire_);
  sample.bh1750.initialized = bh1750_initialized_;
  if (!bh1750_initialized_) {
    ++sample.bh1750.read_failures;
    Serial.println(F("BH1750 initialization failed"));
  } else {
    Serial.println(F("BH1750 continuous measurement started at 0x23"));
  }
  return bh1750_initialized_;
}

void EnvironmentSensors::poll(uint32_t now_ms, EnvironmentSample& sample) {
  sample.scd41.updated = false;
  sample.dht22.updated = false;
  sample.bh1750.updated = false;

  if (!scd41_initialized_ &&
      now_ms - last_scd41_init_attempt_ms_ >= kEnvironmentSensorRetryMs) {
    beginScd41(now_ms, sample);
  }
  if (!bh1750_initialized_ &&
      now_ms - last_bh1750_init_attempt_ms_ >= kEnvironmentSensorRetryMs) {
    beginBh1750(now_ms, sample);
  }

  if (scd41_initialized_ &&
      now_ms - last_scd41_poll_ms_ >= kScd41ReadyPollMs) {
    last_scd41_poll_ms_ = now_ms;
    pollScd41(now_ms, sample);
  }
  if (now_ms - last_dht22_read_ms_ >= kDht22ReadIntervalMs) {
    last_dht22_read_ms_ = now_ms;
    pollDht22(now_ms, sample);
  }
  if (bh1750_initialized_ &&
      now_ms - last_bh1750_read_ms_ >= kBh1750ReadIntervalMs) {
    last_bh1750_read_ms_ = now_ms;
    pollBh1750(now_ms, sample);
  }

  updateFreshness(now_ms, sample);
}

void EnvironmentSensors::pollScd41(uint32_t now_ms,
                                   EnvironmentSample& sample) {
  sample.scd41.last_attempt_ms = now_ms;
  bool data_ready = false;
  int16_t error = scd41_.getDataReadyStatus(data_ready);
  if (error != kNoError) {
    scd41_initialized_ = false;
    sample.scd41.initialized = false;
    sample.scd41.valid = false;
    sample.co2_valid = false;
    ++sample.scd41.read_failures;
    Serial.print(F("SCD41 ready check failed, error="));
    Serial.println(error);
    return;
  }
  if (!data_ready) return;

  uint16_t co2_ppm = 0;
  float ignored_temperature_c = 0.0F;
  float ignored_humidity_pct = 0.0F;
  error = scd41_.readMeasurement(co2_ppm, ignored_temperature_c,
                                 ignored_humidity_pct);
  if (error != kNoError) {
    scd41_initialized_ = false;
    sample.scd41.initialized = false;
    sample.scd41.valid = false;
    sample.co2_valid = false;
    ++sample.scd41.read_failures;
    Serial.print(F("SCD41 read failed, error="));
    Serial.println(error);
    return;
  }

  sample.co2_ppm = static_cast<float>(co2_ppm);
  sample.co2_valid = environment_validation::validateScd41Co2(sample.co2_ppm);
  sample.scd41.valid = sample.co2_valid;
  if (!sample.co2_valid) {
    ++sample.scd41.read_failures;
    Serial.println(F("SCD41 CO2 measurement outside sensor range"));
    return;
  }

  sample.scd41.updated = true;
  sample.scd41.has_success = true;
  sample.scd41.last_success_ms = now_ms;
}

void EnvironmentSensors::pollDht22(uint32_t now_ms,
                                   EnvironmentSample& sample) {
  sample.dht22.last_attempt_ms = now_ms;
  const float humidity_pct = dht22_.readHumidity();
  const float temp_c = dht22_.readTemperature();
  const auto validity =
      environment_validation::validateDht22(temp_c, humidity_pct);

  sample.temp_c = temp_c;
  sample.humidity_pct = humidity_pct;
  sample.temp_valid = validity.temp_valid;
  sample.humidity_valid = validity.humidity_valid;
  sample.dht22.valid = validity.allValid();
  sample.dht22.updated = validity.anyValid();
  if (validity.anyValid()) {
    sample.dht22.has_success = true;
    sample.dht22.last_success_ms = now_ms;
  }
  if (!validity.allValid()) {
    ++sample.dht22.read_failures;
    Serial.println(F("DHT22 read failed/out of sensor range"));
  }
}

void EnvironmentSensors::pollBh1750(uint32_t now_ms,
                                    EnvironmentSample& sample) {
  if (!bh1750_.measurementReady()) return;

  sample.bh1750.last_attempt_ms = now_ms;
  sample.lux = bh1750_.readLightLevel();
  sample.lux_valid = environment_validation::validateBh1750(sample.lux);
  sample.bh1750.valid = sample.lux_valid;
  if (!sample.lux_valid) {
    bh1750_initialized_ = false;
    sample.bh1750.initialized = false;
    ++sample.bh1750.read_failures;
    Serial.println(F("BH1750 read failed/out of sensor range"));
    return;
  }

  sample.bh1750.updated = true;
  sample.bh1750.has_success = true;
  sample.bh1750.last_success_ms = now_ms;
}

void EnvironmentSensors::updateFreshness(uint32_t now_ms,
                                         EnvironmentSample& sample) {
  sample.scd41.age_ms = environment_validation::sampleAgeMs(
      now_ms, sample.scd41.last_success_ms, sample.scd41.has_success);
  sample.scd41.stale = environment_validation::isStale(
      now_ms, sample.scd41.last_success_ms, sample.scd41.has_success,
      kEnvironmentStaleAfterMs);
  if (sample.scd41.stale) sample.co2_valid = false;

  sample.dht22.age_ms = environment_validation::sampleAgeMs(
      now_ms, sample.dht22.last_success_ms, sample.dht22.has_success);
  sample.dht22.stale = environment_validation::isStale(
      now_ms, sample.dht22.last_success_ms, sample.dht22.has_success,
      kEnvironmentStaleAfterMs);
  if (sample.dht22.stale) {
    sample.temp_valid = false;
    sample.humidity_valid = false;
  }

  sample.bh1750.age_ms = environment_validation::sampleAgeMs(
      now_ms, sample.bh1750.last_success_ms, sample.bh1750.has_success);
  sample.bh1750.stale = environment_validation::isStale(
      now_ms, sample.bh1750.last_success_ms, sample.bh1750.has_success,
      kEnvironmentStaleAfterMs);
  if (sample.bh1750.stale) sample.lux_valid = false;
}

}  // namespace deskmate
