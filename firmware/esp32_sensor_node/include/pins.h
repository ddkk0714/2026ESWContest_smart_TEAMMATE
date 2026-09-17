#pragma once

#include <Arduino.h>

#ifndef DESKMATE_UART2_TX
#define DESKMATE_UART2_TX 1
#endif

#ifndef DESKMATE_FIRMWARE_VERSION
#define DESKMATE_FIRMWARE_VERSION 0x0100
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

// Supplied by platformio.ini build flags so tuning does not require source edits.
constexpr uint32_t kWarmupMs = DESKMATE_WARMUP_MS;
constexpr uint32_t kDrowsyStillMs = DESKMATE_DROWSY_STILL_MS;
constexpr uint8_t kStillMotionLevelMax = DESKMATE_STILL_MOTION_LEVEL_MAX;

}  // namespace deskmate
