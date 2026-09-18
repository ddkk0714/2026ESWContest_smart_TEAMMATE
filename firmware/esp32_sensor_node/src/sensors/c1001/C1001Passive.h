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

  // 센서가 살아 있으면 질의 하나가 수십 ms 다. 죽으면 벤더 라이브러리가 질의마다
  // 타임아웃까지 기다리는데 read() 는 질의를 7번 하므로 loop() 가 통째로 굶는다
  // (2026-09-18 실측: 프레임이 1 Hz -> 12 s 주기로 떨어지고 환경값까지 같이 멈췄다).
  // 그래서 예산을 두고, 넘으면 남은 질의를 건너뛰고 '응답 없음'으로 돌려준다.
  static constexpr uint32_t kReadBudgetMs = 600;

  /// 마지막 read() 가 예산을 넘겼는가. 넘겼으면 그 표본은 믿을 것이 못 된다.
  bool lastReadTimedOut() const { return last_read_timed_out_; }

  MmwaveSample read() {
    MmwaveSample sample;
    last_read_timed_out_ = false;
    const uint32_t started = millis();
    const uint16_t presence = sensor_.smHumanData(DFRobot_HumanDetection::eHumanPresence);
    const uint16_t movement = sensor_.smHumanData(DFRobot_HumanDetection::eHumanMovement);
    const uint16_t motion_level = sensor_.smHumanData(DFRobot_HumanDetection::eHumanMovingRange);
    const uint16_t distance = sensor_.smHumanData(DFRobot_HumanDetection::eHumanDistance);

    // 타임아웃 난 질의는 0 을 돌려주므로 값만 보면 '사람 없음' 과 구분되지 않는다.
    // 걸린 시간으로 가른다. 여기서 끊으면 남은 세 질의의 타임아웃도 아낀다.
    if (millis() - started > kReadBudgetMs) {
      last_read_timed_out_ = true;
      return sample;  // valid = false
    }

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
  bool last_read_timed_out_ = false;
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
