#pragma once

#include <stdint.h>

#include "EnvironmentSample.h"

namespace deskmate {

constexpr uint8_t kEnvironmentValidCo2 = 1U << 0;
constexpr uint8_t kEnvironmentValidTemp = 1U << 1;
constexpr uint8_t kEnvironmentValidHumidity = 1U << 2;
constexpr uint8_t kEnvironmentValidLux = 1U << 3;
constexpr uint8_t kEnvironmentPayloadSize = 9;

void encodeEnvironmentPayload(const EnvironmentSample& sample,
                              uint8_t (&payload)[kEnvironmentPayloadSize]);

}  // namespace deskmate
