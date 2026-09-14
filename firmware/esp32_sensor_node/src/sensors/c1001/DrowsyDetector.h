#pragma once

#include <Arduino.h>

#include "pins.h"
#include "C1001Passive.h"

namespace deskmate {

enum class DrowsyState : uint8_t { kNoPerson, kNoLock, kWarmup, kAwake, kDrowsy };

class DrowsyDetector {
 public:
  DrowsyState update(const MmwaveSample& sample, uint32_t now_ms) {
    if (!sample.valid || !sample.present) {
      presence_since_ms_ = 0;
      still_since_ms_ = 0;
      return DrowsyState::kNoPerson;
    }
    if (presence_since_ms_ == 0) presence_since_ms_ = now_ms;
    if (!sample.resp_valid) {
      still_since_ms_ = 0;
      return DrowsyState::kNoLock;
    }
    if (now_ms - presence_since_ms_ < kWarmupMs) return DrowsyState::kWarmup;

    const bool still = sample.motion_state != MotionState::kActive &&
                       sample.motion_level <= kStillMotionLevelMax;
    if (!still) {
      still_since_ms_ = 0;
      return DrowsyState::kAwake;
    }
    if (still_since_ms_ == 0) still_since_ms_ = now_ms;
    return now_ms - still_since_ms_ >= kDrowsyStillMs ? DrowsyState::kDrowsy
                                                       : DrowsyState::kAwake;
  }

 private:
  uint32_t presence_since_ms_ = 0;
  uint32_t still_since_ms_ = 0;
};

inline const char* drowsyStateName(DrowsyState state) {
  switch (state) {
    case DrowsyState::kNoLock:
      return "NOLOCK";
    case DrowsyState::kWarmup:
      return "WARMUP";
    case DrowsyState::kAwake:
      return "AWAKE";
    case DrowsyState::kDrowsy:
      return "DROWSY";
    default:
      return "NOPERSON";
  }
}

}  // namespace deskmate
