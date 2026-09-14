#pragma once

#include <Arduino.h>

#include "sensors/c1001/C1001Passive.h"
#include "sensors/c1001/DrowsyDetector.h"

namespace deskmate {

void writeMmwaveJson(Stream& output, uint32_t now_ms, const MmwaveSample& sample,
                     DrowsyState drowsy_state);
void writeEnvironmentStubJson(Stream& output, uint32_t now_ms);

}  // namespace deskmate
