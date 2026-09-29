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

  // 벤더 begin() 은 센서가 없으면 delay(10 s)+5 s 타임아웃으로 15 s 를 통째로
  // 막는다. 그 앞에 값싼 생존 확인을 둔다 - 초기화 상태 질의(0x01/0x83)를 보내고
  // 응답을 기다린다.
  //
  // **아무 바이트나 받아들이면 안 된다.** 전원만 들어오고 응답은 못 하는 상태의
  // C1001 도 선에 무언가를 흘리는데, 그걸 '살아 있다' 로 보면 곧바로 begin() 이
  // 불려 15 s 를 먹는다(2026-09-18 실측: env 주기가 5 s -> 7.9 s 로 늘어졌다).
  // 그래서 프레임 머리 0x53 0x59 ("SY") 가 실제로 올 때만 통과시킨다.
  bool probe(uint32_t probe_ms = 300) {
    static const uint8_t query[] = {0x53, 0x59, 0x01, 0x83, 0x00, 0x01, 0x0F, 0x40, 0x54, 0x43};
    while (serial_.available() > 0) serial_.read();
    serial_.write(query, sizeof(query));
    const uint32_t start = millis();
    uint8_t previous = 0;
    while (millis() - start < probe_ms) {
      while (serial_.available() > 0) {
        const uint8_t current = static_cast<uint8_t>(serial_.read());
        if (previous == 0x53 && current == 0x59) return true;
        previous = current;
      }
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

    // 질의 **하나마다** 예산을 본다. 넷을 다 부른 뒤에 한 번만 보면 죽은 센서에서
    // 타임아웃 네 번을 그대로 물어, 한 tick 이 수 초가 된다(실측: env 주기가
    // 5 s -> 11.8 s). 첫 질의에서 끊으면 비용이 타임아웃 한 번으로 끝난다.
    const uint16_t presence = sensor_.smHumanData(DFRobot_HumanDetection::eHumanPresence);
    if (overBudget(started)) return sample;
    const uint16_t movement = sensor_.smHumanData(DFRobot_HumanDetection::eHumanMovement);
    if (overBudget(started)) return sample;
    const uint16_t motion_level = sensor_.smHumanData(DFRobot_HumanDetection::eHumanMovingRange);
    if (overBudget(started)) return sample;
    const uint16_t distance = sensor_.smHumanData(DFRobot_HumanDetection::eHumanDistance);
    if (overBudget(started)) return sample;

    if (presence > 1 || movement > 2 || motion_level > 100) return sample;

    sample.present = presence == 1;
    sample.motion_state = static_cast<MotionState>(movement);
    sample.motion_level = static_cast<uint8_t>(motion_level);
    sample.distance_valid = sample.present && distance <= 1000;
    sample.distance_cm = distance;

    const uint8_t breathe_state = sensor_.getBreatheState();
    if (overBudget(started)) return sample;
    const uint8_t breathe = sensor_.getBreatheValue();
    if (overBudget(started)) return sample;
    const uint8_t heart = sensor_.getHeartRate();
    if (overBudget(started)) return sample;
    const bool measurement_lock = breathe_state >= 1 && breathe_state <= 3;
    sample.resp_valid = sample.present && measurement_lock && breathe > 0;
    sample.heart_valid = sample.present && measurement_lock && heart > 0;
    sample.resp_bpm = breathe;
    sample.heart_bpm = heart;
    sample.valid = true;
    return sample;
  }

 private:
  /// 예산을 넘겼으면 '응답 없음' 으로 표시하고 true. 호출부는 바로 빠져나간다.
  bool overBudget(uint32_t started) {
    if (millis() - started <= kReadBudgetMs) return false;
    last_read_timed_out_ = true;
    return true;
  }

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
