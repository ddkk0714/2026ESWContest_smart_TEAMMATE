#pragma once

#include <stdint.h>

namespace deskmate {

enum class MotionState : uint8_t { kNone = 0, kStill = 1, kActive = 2 };

/// UART/USB 로 나가는 한 틱의 mmWave 관측값. 전송 계약(transport/)이 이 모양에
/// 의존하므로 필드를 바꾸면 hub/deskmate_hub/ingest/uart_frame.py 도 같이 본다.
struct MmwaveSample {
  bool present = false;
  MotionState motion_state = MotionState::kNone;
  uint8_t motion_level = 0;
  bool distance_valid = false;
  uint16_t distance_cm = 0;
  bool resp_valid = false;
  uint8_t resp_bpm = 0;
  bool heart_valid = false;
  uint8_t heart_bpm = 0;
  bool valid = false;
};

inline const char* motionStateName(MotionState state) {
  switch (state) {
    case MotionState::kStill:
      return "still";
    case MotionState::kActive:
      return "active";
    default:
      return "none";
  }
}

}  // namespace deskmate
