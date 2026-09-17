#include "EnvironmentPayload.h"

#include <cmath>

namespace deskmate {
namespace {

void putU16Le(uint8_t* output, uint16_t value) {
  output[0] = static_cast<uint8_t>(value & 0xFF);
  output[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

}  // namespace

void encodeEnvironmentPayload(const EnvironmentSample& sample,
                              uint8_t (&payload)[kEnvironmentPayloadSize]) {
  const uint16_t co2 = sample.co2_valid
                           ? static_cast<uint16_t>(sample.co2_ppm)
                           : 0;
  const int16_t temp_x10 = sample.temp_valid
                               ? static_cast<int16_t>(std::lround(sample.temp_c * 10.0F))
                               : 0;
  const uint16_t humidity_x10 =
      sample.humidity_valid
          ? static_cast<uint16_t>(std::lround(sample.humidity_pct * 10.0F))
          : 0;
  const uint16_t lux = sample.lux_valid ? static_cast<uint16_t>(sample.lux) : 0;

  putU16Le(payload, co2);
  putU16Le(payload + 2, static_cast<uint16_t>(temp_x10));
  putU16Le(payload + 4, humidity_x10);
  putU16Le(payload + 6, lux);
  payload[8] = (sample.co2_valid ? kEnvironmentValidCo2 : 0) |
               (sample.temp_valid ? kEnvironmentValidTemp : 0) |
               (sample.humidity_valid ? kEnvironmentValidHumidity : 0) |
               (sample.lux_valid ? kEnvironmentValidLux : 0);
}

}  // namespace deskmate
