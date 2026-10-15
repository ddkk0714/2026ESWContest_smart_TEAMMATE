#pragma once

#include <stddef.h>

#include "TofPosture.h"

namespace deskmate {

constexpr size_t kTofFeaturePayloadMax = 512;
constexpr size_t kTofFeatureBinarySize = 32;

// v1: version, flags, posture, width, height, reserved, zones u16, six float32 LE.
size_t encodeTofFeatureBinary(uint8_t *buf, size_t cap, const TofFeatures &features, bool valid);

// Scalar JSON data only. The transport supplies its own timestamp and envelope.
// valid must include the sensor read/update result, not just the last estimator state.
// Returns the length without a newline, or 0 with an empty buffer on overflow.
size_t formatTofFeaturePayload(char *buf, size_t cap, const TofFeatures &features, bool valid);

}  // namespace deskmate
