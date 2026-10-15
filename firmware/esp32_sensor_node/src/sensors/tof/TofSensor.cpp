#include "TofSensor.h"

namespace deskmate {

bool TofSensor::start() {
  // Probe before the driver's firmware upload to avoid a long init on a missing sensor.
  wire_.beginTransmission(0x29);
  if (wire_.endTransmission() != 0) return false;
  sensor_.begin();
  return sensor_.init() == VL53L8CX_STATUS_OK &&
         sensor_.set_resolution(VL53L8CX_RESOLUTION_8X8) == VL53L8CX_STATUS_OK &&
         sensor_.set_ranging_frequency_hz(15) == VL53L8CX_STATUS_OK &&
         sensor_.set_target_order(VL53L8CX_TARGET_ORDER_CLOSEST) == VL53L8CX_STATUS_OK &&
         sensor_.set_ranging_mode(VL53L8CX_RANGING_MODE_CONTINUOUS) == VL53L8CX_STATUS_OK &&
         sensor_.start_ranging() == VL53L8CX_STATUS_OK;
}

bool TofSensor::invalidate(uint32_t now_ms) {
  valid_ = false;
  ranging_ = false;
  estimator_.reset();
  timestamp_ms_ = now_ms;
  // Back off from the failure, rather than immediately reinitializing a stalled sensor.
  last_attempt_ms_ = now_ms;
  return true;
}

bool TofSensor::poll(uint32_t now_ms) {
  if (!ranging_) {
    if (attempted_ && now_ms - last_attempt_ms_ < DESKMATE_TOF_RETRY_MS) return false;
    attempted_ = true;
    last_attempt_ms_ = now_ms;
    ranging_ = start();
    // Initialization uploads sensor firmware and can take seconds.
    last_frame_ms_ = millis();
    last_poll_ms_ = last_frame_ms_;
    if (!ranging_) return invalidate(last_frame_ms_);
    return false;
  }
  if (now_ms - last_poll_ms_ < 20 &&
      now_ms - last_frame_ms_ < DESKMATE_TOF_STALE_MS) return false;
  last_poll_ms_ = now_ms;
  uint8_t ready = 0;
  if (sensor_.check_data_ready(&ready) != VL53L8CX_STATUS_OK) return invalidate(now_ms);
  if (!ready) {
    return now_ms - last_frame_ms_ >= DESKMATE_TOF_STALE_MS ? invalidate(now_ms) : false;
  }
  if (sensor_.get_ranging_data(&results_) != VL53L8CX_STATUS_OK) return invalidate(now_ms);
  for (uint8_t i = 0; i < 64; ++i) {
    const uint8_t row = DESKMATE_TOF_FLIP_ROWS ? 7 - i / 8 : i / 8;
    const uint8_t col = DESKMATE_TOF_FLIP_COLS ? 7 - i % 8 : i % 8;
    const uint8_t zone = row * 8 + col;
    const unsigned target = i * VL53L8CX_NB_TARGET_PER_ZONE;
    distance_[zone] = results_.nb_target_detected[i] ? results_.distance_mm[target] : -1;
    status_[zone] = results_.nb_target_detected[i] ? results_.target_status[target] : 255;
  }
  timestamp_ms_ = millis();
  last_frame_ms_ = timestamp_ms_;
  TofFrameView frame;
  frame.width = frame.height = 8;
  frame.distance_mm = distance_;
  frame.status = status_;
  valid_ = estimator_.update(frame, timestamp_ms_) && estimator_.features().valid_zones > 0;
  return true;
}

}  // namespace deskmate
