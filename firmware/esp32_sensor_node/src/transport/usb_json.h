#pragma once

#include <Arduino.h>

#include "sensors/c1001/C1001Passive.h"
#include "sensors/c1001/DrowsyDetector.h"
#include "sensors/environment/EnvironmentSample.h"
#include "preprocess/tof/TofPosture.h"

namespace deskmate {

void writeMmwaveJson(Stream& output, uint32_t now_ms, const MmwaveSample& sample,
                     DrowsyState drowsy_state);
void writeEnvironmentJson(Stream& output, uint32_t now_ms,
                          const EnvironmentSample& sample);
// {"t":"tof_feature",...} 1 Hz. 수집 모드의 {"t":"tof"} 줄(원본 zone)과 이름을 달리 한다.
// sensor_mask: b0 = 센서 A, b1 = 센서 B 동작 중.
void writeTofJson(Stream& output, uint32_t now_ms, const TofFeatures& f, uint8_t sensor_mask);

}  // namespace deskmate
