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
// sensor_mask: b0 = 센서 A, b1 = 센서 B 동작 중. tx_grid/tx_feature: 부팅 뒤 보낸 UART 0x05/0x03 프레임 누계
// (1 Hz 줄의 차분이 송신률 — 0x05 는 센서당 5 Hz × 살아 있는 센서 수, 0x03 은 5 Hz 가 설계값).
void writeTofJson(Stream& output, uint32_t now_ms, const TofFeatures& f, uint8_t sensor_mask,
                  uint32_t tx_grid, uint32_t tx_feature);

}  // namespace deskmate
