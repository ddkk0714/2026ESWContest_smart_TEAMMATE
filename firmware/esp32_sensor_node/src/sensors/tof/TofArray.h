#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <vl53l8cx.h>

#include "preprocess/tof/TofPosture.h"

namespace deskmate {

// ============================================================================
//  VL53L8CX 8×8 두 개를 한 I2C 버스에서 읽어 하나의 격자로 이어 붙이고
//  TofPostureEstimator 에 넣는다.
//
//  배선: 두 센서가 SDA/SCL 을 공유하고 LPn 만 따로 받는다. 부팅 때 B 의 LPn 을
//  LOW 로 눌러 둔 채 A 를 기본 주소(0x52)로 올려 0x54 로 바꾸고, 그 뒤 B 를
//  0x52 그대로 올린다(ST AN5831 다중 센서 절차). NCS·SPI_I2C_N 은 GND(I2C 모드).
//  ESP32 가 소프트 리셋되면 A 는 이미 0x54 에 있을 수 있어 두 주소를 다 찔러 본다.
//
//  격자: 각 8×8 에 flip_rows/flip_cols 를 먼저 적용해 장면 방향(row 0 = 위,
//  col 0 = 센서가 보는 왼쪽)으로 맞춘 뒤, stack_rows=false 면 A|B 가로(16×8),
//  true 면 A 위 / B 아래(8×16)로 붙인다. 한쪽만 살아 있으면 8×8 로 돈다
//  (격자 크기가 바뀌면 추정기가 배경·기준선을 다시 쌓는다).
//
//  프레임 방출: 살아 있는 센서 중 첫 번째가 새 프레임을 주면 다른 쪽의 마지막
//  프레임과 함께 붙여 낸다(두 센서는 동기화하지 않는다, 최대 한 프레임 66 ms 차).
//
//  이 클래스는 Arduino 에 묶여 있어 호스트 테스트가 없다. 붙이는 순서·방향은
//  TofLine::remapZones 와 같은 규칙이고, 실기에서 손 들기로 확인한다
//  (dataset/tof_logger/README.md "방향 확인").
// ============================================================================

constexpr uint8_t kTofMaxSensors = 2;
constexpr uint8_t kTofSensorW = 8;
constexpr uint8_t kTofSensorH = 8;
constexpr uint16_t kTofSensorZones = kTofSensorW * kTofSensorH;
constexpr uint16_t kTofArrayZones = kTofSensorZones * kTofMaxSensors;
// LPn 선이 안 먹어 버스에서 뗄 수 없는 센서를 임시로 옮겨 두는 주소(8-bit).
// 실기 10-16: B 의 LPn 이 효과가 없어 전원 재투입 뒤 둘 다 0x52 로 깨면 첫 init 이 충돌했다.

struct TofArrayConfig {
  TwoWire *wire = &Wire1;
  int sda = 23;
  int scl = 18;
  uint32_t i2c_hz = 1000000;
  uint8_t ranging_hz = 15;
  uint8_t count = 2;                           // 1 또는 2
  int lpn[kTofMaxSensors] = {32, 33};
  uint16_t address[kTofMaxSensors] = {0x54, 0x52};  // 8-bit. [1] 은 기본 주소를 유지한다
  bool flip_rows = false;                      // 센서 8×8 → 장면 방향
  bool flip_cols = true;                       // ST 순서는 행 안의 zone 이 거꾸로다
  bool stack_rows = false;                     // false: A|B 가로, true: A 위 B 아래
  bool swap = false;                           // A/B 자리 바꾸기(배선을 안 바꾸고 좌우 교체)
  uint32_t retry_ms = 5000;
  uint8_t max_read_errors = 10;                // 연속 읽기 실패가 이만큼이면 센서를 내리고 다시 올린다
};

class TofArray {
 public:
  explicit TofArray(const TofArrayConfig &cfg);

  // 버스를 열고 센서를 차례로 올린다. 센서당 init 이 수 초 걸린다(펌웨어 업로드).
  // log 를 주면 올리기 전에 LPn 조합별 I2C 주소 스캔 결과를 한 줄 찍는다(배선 진단).
  void begin(uint32_t now_ms, Print *log = nullptr);
  // 죽은 센서 재시도 + 프레임 읽기. 새 격자가 만들어지면 frameReady() 가 true.
  void poll(uint32_t now_ms);
  bool frameReady();  // 읽으면 false 로 돌아간다

  uint8_t width() const { return w_; }
  uint8_t height() const { return h_; }
  const int16_t *distance() const { return dist_; }  // row-major 장면 방향, -1 = 타깃 없음
  const uint8_t *status() const { return st_; }      // 255 = 타깃 없음
  // 센서 i 의 마지막 8×8(장면 방향, flip 적용 뒤). UART 0x05 는 이것을 센서별로 보낸다 — 두 센서 시야가
  // 대부분 겹쳐 16×8 스티칭은 한 장면이 아니기 때문(TofPayload.h). sensorOk(i) 가 거짓이면 낡은 값이다.
  const int16_t *sensorDistance(uint8_t i) const { return scene_d_[i < kTofMaxSensors ? i : 0]; }
  const uint8_t *sensorStatus(uint8_t i) const { return scene_st_[i < kTofMaxSensors ? i : 0]; }
  uint8_t okMask() const { return ok_mask_; }        // b0 = A, b1 = B
  uint8_t okCount() const;
  uint32_t frames() const { return frames_; }

  TofPostureEstimator &estimator() { return estimator_; }
  const TofFeatures &features() const { return estimator_.features(); }

  // 마지막 실패 단계·코드(USB 진단용). 성공하면 where 가 nullptr.
  const char *lastErrorWhere() const { return err_where_; }
  int lastErrorCode() const { return err_code_; }
  uint8_t lastErrorSensor() const { return err_sensor_; }
  // 센서별 마지막 실패(그 센서가 올라오면 지워진다).
  const char *errorWhere(uint8_t i) const { return i < kTofMaxSensors ? sensor_err_where_[i] : nullptr; }
  int errorCode(uint8_t i) const { return i < kTofMaxSensors ? sensor_err_code_[i] : 0; }
  bool sensorOk(uint8_t i) const { return i < kTofMaxSensors && ok_[i]; }

 private:
  bool acks(uint16_t address8) const;
  // 모든 LPn 이 LOW 인데 기본 주소가 응답하면(이전 실행이 init 해 LPn 을 무시하는 센서): 주소가 이미 갈려 있으면
  // 그대로 두고, 0x52 에만 몰려 있으면 SW 리부트로 미초기화 상태로 되돌린다.
  void parkStuckSensor(Print *log);
  bool startSensor(uint8_t i, uint32_t now_ms);
  // init(펌웨어 업로드 ≈1.3 s) + 실패 시 stop_ranging 뒤 한 번 더. log 가 있으면 상태·소요 ms 한 줄.
  uint8_t initSensor(VL53L8CX &s, const char *tag);
  // vl53l8cx_init() 의 SW 리부트 시퀀스만 보낸다(≈100 ms). 리부트된 센서는 미초기화라 LPn LOW 를 다시 따른다.
  static void softReboot(VL53L8CX &s);
  void stopSensor(uint8_t i);
  bool readSensor(uint8_t i);
  void fail(uint8_t i, const char *where, int code);
  void rebuildGrid();
  void stitch(uint32_t now_ms);

  TofArrayConfig cfg_;
  Print *log_ = nullptr;  // begin() 의 log. 부팅·재시도 진단 한 줄씩
  VL53L8CX *sensor_[kTofMaxSensors] = {nullptr, nullptr};
  VL53L8CX_ResultsData results_;  // 약 1.5 KB, 센서가 번갈아 쓴다
  TofPostureEstimator estimator_;

  bool ok_[kTofMaxSensors] = {false, false};
  bool addressed_[kTofMaxSensors] = {false, false};  // 주소 변경까지 끝났는지(A 만 의미 있음)
  bool fresh_[kTofMaxSensors] = {false, false};
  uint8_t read_errors_[kTofMaxSensors] = {0, 0};
  uint32_t last_try_ms_[kTofMaxSensors] = {0, 0};
  int16_t scene_d_[kTofMaxSensors][kTofSensorZones];
  uint8_t scene_st_[kTofMaxSensors][kTofSensorZones];

  int16_t dist_[kTofArrayZones];
  uint8_t st_[kTofArrayZones];
  uint8_t w_ = 0;
  uint8_t h_ = 0;
  uint8_t ok_mask_ = 0;
  bool frame_ready_ = false;
  uint32_t frames_ = 0;

  const char *err_where_ = nullptr;
  int err_code_ = 0;
  uint8_t err_sensor_ = 0;
  const char *sensor_err_where_[kTofMaxSensors] = {nullptr, nullptr};
  int sensor_err_code_[kTofMaxSensors] = {0, 0};
};

}  // namespace deskmate
