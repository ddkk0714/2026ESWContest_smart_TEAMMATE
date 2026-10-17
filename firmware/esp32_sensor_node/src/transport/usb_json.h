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
// {"t":"tof_grid",...} — 센서별 원본 8×8(장면 방향, UART 0x05 와 같은 값·같은 frame_id). USB 명령 'z' 로 켜는 PC 진단용이며
// 기본은 꺼져 있다(운영 ToF raw 는 Pi 4 UART 로만 간다). 센서 i 가 죽어 있으면 그 항목은 null. 한 줄 ≈1.1 kB, 5 Hz.
//   {"t":"tof_grid","ms":..,"frame":..,"sensors_ok":3,"w":8,"h":8,"flags":2,
//    "s":[{"d":[64 ints, -1 = 타깃 없음],"st":[64 ints, 255 = 타깃 없음]}, {...} | null]}
void writeTofGridJson(Stream& output, uint32_t now_ms, uint8_t frame_id, uint8_t sensor_mask, uint8_t flags,
                      uint8_t w, uint8_t h, uint8_t count, const int16_t* const* dist, const uint8_t* const* status);

}  // namespace deskmate
