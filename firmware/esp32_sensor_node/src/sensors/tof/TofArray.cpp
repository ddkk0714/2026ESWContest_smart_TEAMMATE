#include "sensors/tof/TofArray.h"

namespace deskmate {

TofArray::TofArray(const TofArrayConfig &cfg) : cfg_(cfg) {
  if (cfg_.count < 1) cfg_.count = 1;
  if (cfg_.count > kTofMaxSensors) cfg_.count = kTofMaxSensors;
  for (uint8_t i = 0; i < cfg_.count; ++i) {
    // SPI_I2C_N 은 배선에서 GND 로 묶으므로 i2c_rst 핀은 쓰지 않는다.
    sensor_[i] = new VL53L8CX(cfg_.wire, cfg_.lpn[i]);
  }
  for (uint16_t z = 0; z < kTofArrayZones; ++z) {
    dist_[z] = -1;
    st_[z] = 255;
  }
}

uint8_t TofArray::okCount() const {
  uint8_t n = 0;
  for (uint8_t i = 0; i < cfg_.count; ++i) n += ok_[i] ? 1 : 0;
  return n;
}

bool TofArray::frameReady() {
  const bool ready = frame_ready_;
  frame_ready_ = false;
  return ready;
}

bool TofArray::acks(uint16_t address8) const {
  cfg_.wire->beginTransmission(static_cast<uint8_t>((address8 >> 1) & 0x7F));
  return cfg_.wire->endTransmission() == 0;
}

void TofArray::fail(uint8_t i, const char *where, int code) {
  err_where_ = where;
  err_code_ = code;
  err_sensor_ = i;
  if (i < kTofMaxSensors) {
    sensor_err_where_[i] = where;
    sensor_err_code_[i] = code;
  }
}

void TofArray::stopSensor(uint8_t i) {
  ok_[i] = false;
  fresh_[i] = false;
  read_errors_[i] = 0;
  if (cfg_.lpn[i] >= 0) {
    pinMode(cfg_.lpn[i], OUTPUT);
    digitalWrite(cfg_.lpn[i], LOW);  // 버스에서 떼어 둔다(다른 센서의 주소 변경을 방해하지 않게)
  }
}

bool TofArray::startSensor(uint8_t i, uint32_t now_ms) {
  last_try_ms_[i] = now_ms;
  VL53L8CX &s = *sensor_[i];
  const uint16_t target = cfg_.address[i];

  // begin() 이 LPn 을 LOW→HIGH 로 올리고 주소를 기본값으로 둔다.
  s.begin(VL53L8CX_DEFAULT_I2C_ADDRESS);
  delay(10);
  // 찾는 순서: 목표 주소(소프트 리셋 뒤 이미 바뀐 경우) → 기본 0x52 → 치워 둔 0x56(LPn 이 안 먹어
  // parkStuckSensor() 가 옮긴 경우). 실기 10-16: B 의 LPn 이 효과가 없어도 이렇게 하면 둘 다 올라온다.
  uint16_t current = 0;
  const uint16_t candidates[3] = {target, VL53L8CX_DEFAULT_I2C_ADDRESS, kTofParkedAddress};
  for (uint8_t k = 0; k < 3 && current == 0; ++k) {
    if (k > 0 && candidates[k] == target) continue;
    if (acks(candidates[k])) current = candidates[k];
  }
  if (current == 0) {
    fail(i, "no_ack", 0);
    stopSensor(i);
    return false;
  }
  if (current != VL53L8CX_DEFAULT_I2C_ADDRESS) {
    s.begin(current);
    delay(10);
  }

  uint8_t alive = 0;
  uint8_t st = s.is_alive(&alive);
  if (st != VL53L8CX_STATUS_OK || !alive) {
    fail(i, "is_alive", st != VL53L8CX_STATUS_OK ? st : -1);
    stopSensor(i);
    return false;
  }
  st = s.init();  // 펌웨어 업로드, 1 MHz 에서 약 1~2 s
  if (st != VL53L8CX_STATUS_OK) {
    // 실기 10-16: ESP32 만 리셋되면 센서는 이전 실행의 연속 측거를 계속하고 있어 첫 init 이
    // 코드 3(NACK)으로 떨어지고 두 번째는 된다. 멈추고 한 번 더.
    s.stop_ranging();
    delay(50);
    st = s.init();
  }
  if (st != VL53L8CX_STATUS_OK) {
    fail(i, "init", st);
    stopSensor(i);
    return false;
  }
  // 목표 주소가 비어 있을 때만 옮긴다(치워 둔 센서가 0x52 로 돌아갈 때 다른 센서와 겹치지 않게).
  if (current != target && !acks(target)) {
    if ((st = s.set_i2c_address(target)) != VL53L8CX_STATUS_OK || !acks(target)) {
      fail(i, "set_i2c_address", st);
      stopSensor(i);
      return false;
    }
  }
  addressed_[i] = true;

  if ((st = s.set_resolution(VL53L8CX_RESOLUTION_8X8)) != VL53L8CX_STATUS_OK) {
    fail(i, "set_resolution", st);
    stopSensor(i);
    return false;
  }
  // 주파수 한계는 해상도에 따르므로 해상도 뒤에 둔다.
  if ((st = s.set_ranging_frequency_hz(cfg_.ranging_hz)) != VL53L8CX_STATUS_OK) {
    fail(i, "set_frequency", st);
    stopSensor(i);
    return false;
  }
  // 사람이 배경보다 앞에 있으니 가장 가까운 대상. TofPosture 합성 시험도 Closest 가정.
  if ((st = s.set_target_order(VL53L8CX_TARGET_ORDER_CLOSEST)) != VL53L8CX_STATUS_OK) {
    fail(i, "set_target_order", st);
    stopSensor(i);
    return false;
  }
  if ((st = s.set_ranging_mode(VL53L8CX_RANGING_MODE_CONTINUOUS)) != VL53L8CX_STATUS_OK) {
    fail(i, "set_ranging_mode", st);
    stopSensor(i);
    return false;
  }
  if ((st = s.start_ranging()) != VL53L8CX_STATUS_OK) {
    fail(i, "start_ranging", st);
    stopSensor(i);
    return false;
  }
  ok_[i] = true;
  read_errors_[i] = 0;
  err_where_ = nullptr;
  sensor_err_where_[i] = nullptr;
  sensor_err_code_[i] = 0;
  return true;
}

void TofArray::begin(uint32_t now_ms, Print *log) {
  // 모든 LPn 을 먼저 LOW 로: 두 센서가 같은 기본 주소로 동시에 깨어 있지 않게 한다.
  for (uint8_t i = 0; i < cfg_.count; ++i) stopSensor(i);
  cfg_.wire->begin(cfg_.sda, cfg_.scl, cfg_.i2c_hz);
  delay(10);
  if (log != nullptr) {
    // 배선 진단: LPn 을 전부 LOW / 하나씩만 HIGH 로 두고 0x08~0x77 을 찔러 본다.
    // 전원 없는 모듈이 SDA 를 물고 있으면 모든 주소가 ACK 로 보인다 — 그것도 단서다.
    log->print(F("tof i2c scan (8-bit addr):"));
    for (int8_t on = -1; on < static_cast<int8_t>(cfg_.count); ++on) {
      for (uint8_t i = 0; i < cfg_.count; ++i) {
        if (cfg_.lpn[i] >= 0) digitalWrite(cfg_.lpn[i], i == on ? HIGH : LOW);
      }
      delay(20);
      log->print(on < 0 ? F(" [all LPn low]") : (on == 0 ? F(" [A on]") : F(" [B on]")));
      uint8_t found = 0;
      for (uint8_t a7 = 0x08; a7 <= 0x77 && found < 6; ++a7) {
        cfg_.wire->beginTransmission(a7);
        if (cfg_.wire->endTransmission() == 0) {
          log->print(found ? "," : " ");
          log->print("0x");
          log->print(static_cast<uint8_t>(a7 << 1), HEX);
          ++found;
        }
      }
      if (found == 0) log->print(F(" none"));
      if (found >= 6) log->print(F(",... (bus stuck?)"));
    }
    log->println();
    for (uint8_t i = 0; i < cfg_.count; ++i) stopSensor(i);
    delay(10);
  }
  parkStuckSensor(log);
  // A 를 먼저(주소 변경), 그다음 B(기본 주소).
  for (uint8_t i = 0; i < cfg_.count; ++i) startSensor(i, now_ms);
  rebuildGrid();
}

void TofArray::parkStuckSensor(Print *log) {
  // 모든 LPn 이 LOW 인 상태. 기본 주소가 응답하면 LPn 으로 뗄 수 없는 센서가 하나 있다는 뜻이다.
  // (둘 다 안 떼어지면 어차피 충돌이라 여기서 못 고친다.)
  if (!acks(VL53L8CX_DEFAULT_I2C_ADDRESS)) return;
  if (acks(kTofParkedAddress)) return;  // 이미 치워 둔 센서가 있다(소프트 리셋 뒤)
  VL53L8CX stuck(cfg_.wire, -1);
  stuck.begin(VL53L8CX_DEFAULT_I2C_ADDRESS);
  uint8_t st = stuck.init();
  if (st != VL53L8CX_STATUS_OK) {  // 이전 실행의 측거가 돌고 있으면 첫 init 이 실패한다(startSensor 와 같음)
    stuck.stop_ranging();
    delay(50);
    st = stuck.init();
  }
  if (st == VL53L8CX_STATUS_OK) st = stuck.set_i2c_address(kTofParkedAddress);
  if (log != nullptr) {
    log->print(F("tof: sensor stuck on bus with LPn low -> parked at 0x56, status="));
    log->println(st);
  }
}

bool TofArray::readSensor(uint8_t i) {
  VL53L8CX &s = *sensor_[i];
  uint8_t ready = 0;
  uint8_t st = s.check_data_ready(&ready);
  if (st != VL53L8CX_STATUS_OK) {
    fail(i, "check_data_ready", st);
    if (++read_errors_[i] >= cfg_.max_read_errors) stopSensor(i);
    return false;
  }
  if (!ready) return false;
  if ((st = s.get_ranging_data(&results_)) != VL53L8CX_STATUS_OK) {
    fail(i, "get_ranging_data", st);
    if (++read_errors_[i] >= cfg_.max_read_errors) stopSensor(i);
    return false;
  }
  read_errors_[i] = 0;
  // 센서 순서 → 장면 방향 8×8 (TofLine::remapZones 와 같은 규칙).
  for (uint8_t r = 0; r < kTofSensorH; ++r) {
    const uint8_t sr = cfg_.flip_rows ? static_cast<uint8_t>(kTofSensorH - 1 - r) : r;
    for (uint8_t c = 0; c < kTofSensorW; ++c) {
      const uint8_t sc = cfg_.flip_cols ? static_cast<uint8_t>(kTofSensorW - 1 - c) : c;
      const uint16_t src = static_cast<uint16_t>(sr * kTofSensorW + sc);
      const uint16_t k = static_cast<uint16_t>(VL53L8CX_NB_TARGET_PER_ZONE * src);
      const uint16_t dst = static_cast<uint16_t>(r * kTofSensorW + c);
      if (results_.nb_target_detected[src] > 0) {
        scene_d_[i][dst] = results_.distance_mm[k];
        scene_st_[i][dst] = results_.target_status[k];
      } else {
        scene_d_[i][dst] = -1;
        scene_st_[i][dst] = 255;
      }
    }
  }
  fresh_[i] = true;
  return true;
}

void TofArray::rebuildGrid() {
  uint8_t mask = 0;
  for (uint8_t i = 0; i < cfg_.count; ++i) {
    if (ok_[i]) mask |= static_cast<uint8_t>(1u << i);
  }
  ok_mask_ = mask;
  const uint8_t n = okCount();
  if (n >= 2) {
    w_ = cfg_.stack_rows ? kTofSensorW : static_cast<uint8_t>(kTofSensorW * 2);
    h_ = cfg_.stack_rows ? static_cast<uint8_t>(kTofSensorH * 2) : kTofSensorH;
  } else if (n == 1) {
    w_ = kTofSensorW;
    h_ = kTofSensorH;
  } else {
    w_ = 0;
    h_ = 0;
  }
}

void TofArray::stitch(uint32_t now_ms) {
  const uint8_t n = okCount();
  if (n == 0) return;
  // slot 0 = 왼쪽/위, slot 1 = 오른쪽/아래
  for (uint8_t slot = 0; slot < n; ++slot) {
    uint8_t src = 0;
    if (n >= 2) {
      src = cfg_.swap ? static_cast<uint8_t>(1 - slot) : slot;
    } else {
      src = ok_[0] ? 0 : 1;
    }
    for (uint8_t r = 0; r < kTofSensorH; ++r) {
      for (uint8_t c = 0; c < kTofSensorW; ++c) {
        const uint16_t from = static_cast<uint16_t>(r * kTofSensorW + c);
        const uint16_t to = cfg_.stack_rows
                                ? static_cast<uint16_t>((slot * kTofSensorH + r) * w_ + c)
                                : static_cast<uint16_t>(r * w_ + slot * kTofSensorW + c);
        dist_[to] = scene_d_[src][from];
        st_[to] = scene_st_[src][from];
      }
    }
  }
  TofFrameView view;
  view.width = w_;
  view.height = h_;
  view.distance_mm = dist_;
  view.status = st_;
  estimator_.update(view, now_ms);
  frame_ready_ = true;
  ++frames_;
  for (uint8_t i = 0; i < cfg_.count; ++i) fresh_[i] = false;
}

void TofArray::poll(uint32_t now_ms) {
  const uint8_t before = ok_mask_;
  // 죽은 센서 재시도. A 가 아직 주소를 못 바꿨는데 B 가 0x52 로 살아 있으면 A 를
  // 0x52 로 찌를 수 없으니 건너뛴다(B 가 내려가면 그때 올린다).
  for (uint8_t i = 0; i < cfg_.count; ++i) {
    if (ok_[i] || now_ms - last_try_ms_[i] < cfg_.retry_ms) continue;
    const bool conflict = (i == 0 && !addressed_[0] && cfg_.count > 1 && ok_[1] &&
                           cfg_.address[1] == VL53L8CX_DEFAULT_I2C_ADDRESS);
    if (conflict) {
      last_try_ms_[i] = now_ms;
      continue;
    }
    startSensor(i, now_ms);
  }

  int8_t primary = -1;
  for (uint8_t i = 0; i < cfg_.count; ++i) {
    if (!ok_[i]) continue;
    if (primary < 0) primary = static_cast<int8_t>(i);
    readSensor(i);
  }
  rebuildGrid();
  if (ok_mask_ != before) {
    // 격자가 바뀌면 추정기가 알아서 다시 쌓는다(TofPostureEstimator::update).
    for (uint8_t i = 0; i < cfg_.count; ++i) fresh_[i] = false;
    return;
  }
  if (primary >= 0 && fresh_[primary]) stitch(now_ms);
}

}  // namespace deskmate
