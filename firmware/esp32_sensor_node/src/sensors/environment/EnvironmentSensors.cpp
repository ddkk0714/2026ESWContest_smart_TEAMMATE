#include "EnvironmentSensors.h"

#include <Wire.h>

#include "pins.h"

#if DESKMATE_HAS_SCD41
#include <SensirionI2CScd4x.h>
#endif
#if DESKMATE_HAS_BH1750
#include <BH1750.h>
#endif
#if DESKMATE_HAS_DHT22
#include <DHT.h>
#endif

namespace deskmate {
namespace {

#if DESKMATE_HAS_SCD41
SensirionI2CScd4x scd41;  // 0.4.0 API: begin(TwoWire&), fixed address 0x62
#endif
#if DESKMATE_HAS_BH1750
BH1750 bh1750;
#endif
#if DESKMATE_HAS_DHT22
DHT dht22(kDht22Pin, DHT22);
#endif

bool finiteFloat(float value) { return !isnan(value) && !isinf(value); }

}  // namespace

void EnvironmentSensors::begin() {
#if DESKMATE_HAS_SCD41 || DESKMATE_HAS_BH1750
  Wire.begin(kI2cSdaPin, kI2cSclPin);
#endif
#if DESKMATE_HAS_SCD41
  scd41.begin(Wire);  // address 0x62 (kScd41Address) is fixed in this library version
  uint16_t error = scd41.stopPeriodicMeasurement();
  if (error == 0) error = scd41.startPeriodicMeasurement();
  scd41_ready_ = error == 0;
#endif
#if DESKMATE_HAS_BH1750
  bh1750_ready_ =
      bh1750.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, kBh1750Address, &Wire);
#endif
#if DESKMATE_HAS_DHT22
  dht22.begin();
  dht22_ready_ = true;
#endif
}

EnvironmentSample EnvironmentSensors::read() {
  EnvironmentSample sample;
#if DESKMATE_HAS_SCD41
  if (scd41_ready_) {
    uint16_t co2_ppm = 0;
    float unused_temp_c = 0.0F;
    float unused_humidity_pct = 0.0F;
    const uint16_t error =
        scd41.readMeasurement(co2_ppm, unused_temp_c, unused_humidity_pct);
    // SCD41 temperature/humidity deliberately stay excluded from this contract.
    if (error == 0 && co2_ppm > 0) {
      sample.co2_ppm = static_cast<int>(co2_ppm);
      sample.co2_valid = true;
    }
  }
#endif
#if DESKMATE_HAS_BH1750
  if (bh1750_ready_) {
    const float lux = bh1750.readLightLevel();
    if (finiteFloat(lux) && lux >= 0.0F) {
      sample.lux = lux;
      sample.lux_valid = true;
    }
  }
#endif
#if DESKMATE_HAS_DHT22
  if (dht22_ready_) {
    const float temp_c = dht22.readTemperature();
    const float humidity_pct = dht22.readHumidity();
    if (finiteFloat(temp_c)) {
      sample.temp_c = temp_c;
      sample.temp_valid = true;
    }
    if (finiteFloat(humidity_pct)) {
      sample.humidity_pct = humidity_pct;
      sample.humidity_valid = true;
    }
  }
#endif
  return sample;
}

}  // namespace deskmate
