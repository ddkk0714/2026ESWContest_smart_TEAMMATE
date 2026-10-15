#pragma once

#include <Wire.h>
#include <vl53l8cx.h>

#include "preprocess/tof/TofPosture.h"

// Explicit build opt-in; hub ToF mapping and personalization consent remain separate.
#ifndef DESKMATE_TOF_ENABLED
#define DESKMATE_TOF_ENABLED 0
#endif
#ifndef DESKMATE_TOF_FLIP_ROWS
#define DESKMATE_TOF_FLIP_ROWS 0
#endif
#ifndef DESKMATE_TOF_FLIP_COLS
#define DESKMATE_TOF_FLIP_COLS 1
#endif
#ifndef DESKMATE_TOF_RETRY_MS
#define DESKMATE_TOF_RETRY_MS 10000
#endif
#ifndef DESKMATE_TOF_STALE_MS
#define DESKMATE_TOF_STALE_MS 1000
#endif

namespace deskmate {

// Shares the environment I2C bus at its existing clock. Raw zones live only in RAM.
class TofSensor {
 public:
  explicit TofSensor(TwoWire &wire) : wire_(wire), sensor_(&wire, -1) {}
  // True means a new measurement or an invalidation must be transmitted.
  bool poll(uint32_t now_ms);
  const TofFeatures &features() const { return estimator_.features(); }
  bool valid() const { return valid_; }
  uint32_t timestampMs() const { return timestamp_ms_; }

 private:
  bool start();
  bool invalidate(uint32_t now_ms);
  TwoWire &wire_;
  VL53L8CX sensor_;
  TofPostureEstimator estimator_;
  VL53L8CX_ResultsData results_{};
  int16_t distance_[64]{};
  uint8_t status_[64]{};
  bool attempted_ = false;
  bool ranging_ = false;
  bool valid_ = false;
  uint32_t last_attempt_ms_ = 0;
  uint32_t last_poll_ms_ = 0;
  uint32_t last_frame_ms_ = 0;
  uint32_t timestamp_ms_ = 0;
};

}  // namespace deskmate
