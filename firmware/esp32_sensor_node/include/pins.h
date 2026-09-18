#pragma once

#include <Arduino.h>

#ifndef DESKMATE_UART2_TX
#define DESKMATE_UART2_TX 1
#endif

#ifndef DESKMATE_FIRMWARE_VERSION
#define DESKMATE_FIRMWARE_VERSION 0x0100
#endif

#ifndef DESKMATE_HAS_SCD41
#define DESKMATE_HAS_SCD41 0
#endif

#ifndef DESKMATE_HAS_BH1750
#define DESKMATE_HAS_BH1750 0
#endif

#ifndef DESKMATE_HAS_DHT22
#define DESKMATE_HAS_DHT22 0
#endif

namespace deskmate {

constexpr uint32_t kUsbBaud = 115200;
constexpr uint32_t kC1001Baud = 115200;
constexpr uint32_t kPi4Baud = 115200;

constexpr int kC1001RxPin = 16;
constexpr int kC1001TxPin = 17;
constexpr int kPi4TxPin = 25;
constexpr int kPi4RxPin = 26;
constexpr int kI2cSdaPin = 21;
constexpr int kI2cSclPin = 22;
constexpr int kDht22Pin = 27;
constexpr uint8_t kScd41Address = 0x62;
constexpr uint8_t kBh1750Address = 0x23;

constexpr uint32_t kMmwavePeriodMs = 1000;
constexpr uint32_t kEnvironmentPeriodMs = 5000;
constexpr uint32_t kC1001RetryMs = 10000;
// 이만큼 연속으로 응답이 없으면 센서가 죽은 것으로 보고 값싼 probe 재시도로 돌아간다.
constexpr uint8_t kC1001FailStreakMax = 3;
// 재시도가 계속 실패하면 간격을 두 배씩 늘려 이 값까지 간다. begin() 은 센서가
// 응답하다 마는 상태에서 15 s 를 먹으므로 10 s 마다 부르면 루프가 못 돈다.
constexpr uint32_t kC1001RetryMaxMs = 120000;

// Supplied by platformio.ini build flags so tuning does not require source edits.
constexpr uint32_t kWarmupMs = DESKMATE_WARMUP_MS;
constexpr uint32_t kDrowsyStillMs = DESKMATE_DROWSY_STILL_MS;
constexpr uint8_t kStillMotionLevelMax = DESKMATE_STILL_MOTION_LEVEL_MAX;

}  // namespace deskmate
