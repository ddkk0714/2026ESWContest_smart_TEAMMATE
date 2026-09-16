#pragma once

#include <Arduino.h>
#include <DFRobot_HumanDetection.h>

namespace deskmate {

enum class MotionState : uint8_t { kNone = 0, kStill = 1, kActive = 2 };

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

class C1001Passive {
 public:
  explicit C1001Passive(HardwareSerial& serial) : serial_(serial), sensor_(&serial) {}

  // Cheap presence check (~probe_ms) before the vendor begin(), which blocks ~15 s when no sensor answers:
  // send the "initialisation status" query (0x01/0x83) and see whether any byte comes back.
  bool probe(uint32_t probe_ms = 300) {
    static const uint8_t query[] = {0x53, 0x59, 0x01, 0x83, 0x00, 0x01, 0x0F, 0x40, 0x54, 0x43};
    while (serial_.available() > 0) serial_.read();
    serial_.write(query, sizeof(query));
    const uint32_t start = millis();
    while (millis() - start < probe_ms) {
      if (serial_.available() > 0) return true;
      delay(1);
    }
    return false;
  }

  bool begin() {
    if (!probe()) return false;
    if (sensor_.begin() != 0) return false;
    if (sensor_.configWorkMode(DFRobot_HumanDetection::eSleepMode) != 0) return false;
    return sensor_.getWorkMode() == DFRobot_HumanDetection::eSleepMode;
  }

  MmwaveSample read() {
    MmwaveSample sample;
    const uint16_t presence = sensor_.smHumanData(DFRobot_HumanDetection::eHumanPresence);
    const uint16_t movement = sensor_.smHumanData(DFRobot_HumanDetection::eHumanMovement);
    const uint16_t motion_level = sensor_.smHumanData(DFRobot_HumanDetection::eHumanMovingRange);
    const uint16_t distance = sensor_.smHumanData(DFRobot_HumanDetection::eHumanDistance);

    if (presence > 1 || movement > 2 || motion_level > 100) return sample;

    sample.present = presence == 1;
    sample.motion_state = static_cast<MotionState>(movement);
    sample.motion_level = static_cast<uint8_t>(motion_level);
    sample.distance_valid = sample.present && distance <= 1000;
    sample.distance_cm = distance;

    const uint8_t breathe_state = sensor_.getBreatheState();
    const uint8_t breathe = sensor_.getBreatheValue();
    const uint8_t heart = sensor_.getHeartRate();
    const bool measurement_lock = breathe_state >= 1 && breathe_state <= 3;
    sample.resp_valid = sample.present && measurement_lock && breathe > 0;
    sample.heart_valid = sample.present && measurement_lock && heart > 0;
    sample.resp_bpm = breathe;
    sample.heart_bpm = heart;
    sample.valid = true;
    return sample;
  }

 private:
  HardwareSerial& serial_;
  DFRobot_HumanDetection sensor_;
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
