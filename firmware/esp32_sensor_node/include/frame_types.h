#pragma once

#include <Arduino.h>

namespace deskmate {

constexpr uint8_t kFrameSof = 0xA5;
constexpr uint8_t kFrameVersion = 0x01;
constexpr uint8_t kFrameTypeTofFeatures = 0x03;   // tof_feature, payload 는 sensors/tof/TofPayload.h
constexpr uint8_t kFrameTypeTofPosture = 0x04;    // 자세 클래스 요약 1 Hz
constexpr uint8_t kFrameTypeTofGrid = 0x05;       // 원본 거리·상태 격자 조각(Pi 4 자세 모델 입력), TofPayload.h
constexpr uint8_t kFrameTypeEnvironment = 0x10;
constexpr uint8_t kFrameTypeMmwave = 0x20;
constexpr uint8_t kFrameTypeHeartbeat = 0xF0;

constexpr uint16_t kFrameNullU16 = 0xFFFF;
constexpr uint8_t kFrameNullU8 = 0xFF;
constexpr uint16_t kFrameMaxPayload = 256;

// The COBS frame contains this header, payload, and a trailing CRC-16.
constexpr uint8_t kFrameHeaderSize = 11;
constexpr uint8_t kFrameCrcSize = 2;

enum class WireDrowsyState : uint8_t {
  kNoPerson = 0,
  kNoLock = 1,
  kWarmup = 2,
  kAwake = 3,
  kDrowsy = 4,
};

}  // namespace deskmate
