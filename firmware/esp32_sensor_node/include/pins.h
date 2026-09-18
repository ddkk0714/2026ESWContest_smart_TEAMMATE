#pragma once

#include <Arduino.h>

#ifndef DESKMATE_UART2_TX
#define DESKMATE_UART2_TX 1
#endif

#ifndef DESKMATE_FIRMWARE_VERSION
#define DESKMATE_FIRMWARE_VERSION 0x0110
#endif

namespace deskmate {

constexpr uint32_t kUsbBaud = 115200;
constexpr uint32_t kC1001Baud = 115200;
constexpr uint32_t kPi4Baud = 115200;

constexpr int kC1001RxPin = 16;
constexpr int kC1001TxPin = 17;
constexpr int kPi4TxPin = 25;
constexpr int kPi4RxPin = 26;
constexpr int kEnvironmentSdaPin = 21;
constexpr int kEnvironmentSclPin = 22;
constexpr int kDht22DataPin = 27;

constexpr uint8_t kBh1750Address = 0x23;

constexpr uint32_t kMmwavePeriodMs = 1000;
constexpr uint32_t kEnvironmentPeriodMs = 5000;
constexpr uint32_t kScd41ReadyPollMs = 500;
constexpr uint32_t kBh1750ReadIntervalMs = 1000;
constexpr uint32_t kDht22ReadIntervalMs = 2000;
constexpr uint32_t kEnvironmentSensorRetryMs = 10000;
constexpr uint32_t kEnvironmentStaleAfterMs = 15000;

// 졸음 판정 임계값은 실측 근거 주석과 함께 sensors/c1001/DrowsyDetector.h 에 있다.
// 시연용으로 확정 시간만 줄이려면 -DDESKMATE_DROWSY_HOLD_SCALE_PCT=<퍼센트>.

}  // namespace deskmate
