// ToF 학습 데이터 수집 펌웨어 (env:tof_logger). 운영 펌웨어가 아니다.
//
// VL53L8CX 8×8 → 규칙 추정기(TofPosture) → USB 한 줄 JSON(원본 zone + 규칙 판정).
// PC 수집기 dataset/tof_logger/tof_log.py 가 받아서 지시 자세와 함께 세션으로 저장한다.
//
// USB 로 받는 한 글자 명령:
//   b  기준 자세 다시 잡기 (TofPostureEstimator::resetBaseline)
//   g  다음 프레임을 배경으로 (빈 자리에서)
//   r  추정기 전체 초기화
//   h  hello 다시 보내기
#include <Arduino.h>
#include <Wire.h>
#include <vl53l8cx.h>

#include "pins.h"
#include "preprocess/tof/TofPosture.h"
#include "tof_logger/TofLine.h"

// 배선. SATEL-VL53L8 기준: SPI_I2C_N 은 GND(I2C), LPn·PWREN 은 핀을 주거나 3.3 V 로 묶는다.
#ifndef DESKMATE_TOF_SDA
#define DESKMATE_TOF_SDA 21
#endif
#ifndef DESKMATE_TOF_SCL
#define DESKMATE_TOF_SCL 22
#endif
#ifndef DESKMATE_TOF_LPN_PIN
#define DESKMATE_TOF_LPN_PIN -1  // -1 = 3.3 V 로 묶음
#endif
#ifndef DESKMATE_TOF_PWREN_PIN
#define DESKMATE_TOF_PWREN_PIN -1
#endif
#ifndef DESKMATE_TOF_I2C_HZ
#define DESKMATE_TOF_I2C_HZ 1000000  // 펌웨어 84 KB 업로드·8×8 읽기. 배선이 길어 실패하면 400000
#endif
#ifndef DESKMATE_TOF_HZ
#define DESKMATE_TOF_HZ 15  // 8×8 최대
#endif
#ifndef DESKMATE_TOF_LOG_BAUD
#define DESKMATE_TOF_LOG_BAUD 921600  // 한 줄 약 700 B × 15 Hz
#endif
// 센서 zone 순서 → 장면 방향. 손 들기로 확인하고 맞춘다(dataset/tof_logger/README).
// 로그의 원본 배열에는 영향이 없고 온보드 규칙 판정과 hello 에만 쓰인다.
#ifndef DESKMATE_TOF_FLIP_ROWS
#define DESKMATE_TOF_FLIP_ROWS 0
#endif
#ifndef DESKMATE_TOF_FLIP_COLS
#define DESKMATE_TOF_FLIP_COLS 1  // ST 예제가 행 안의 zone 을 거꾸로 찍는다
#endif

using namespace deskmate;

namespace {

constexpr uint8_t kW = 8;
constexpr uint8_t kH = 8;
constexpr uint16_t kZones = kW * kH;
constexpr uint32_t kInitRetryMs = 3000;

VL53L8CX sensor(&Wire, DESKMATE_TOF_LPN_PIN);
TofPostureEstimator estimator;  // 전역: zone 상태 배열이 커서 스택에 두지 않는다

VL53L8CX_ResultsData results;
int16_t raw_d[kZones];
uint8_t raw_st[kZones];
int16_t scene_d[kZones];
uint8_t scene_st[kZones];
char line[kTofLineMax];

bool ranging = false;
uint32_t seq = 0;
uint32_t last_init_ms = 0;

void writeLine(size_t n) {
  if (n > 0) Serial.write(reinterpret_cast<const uint8_t *>(line), n);
}

void sendHello() {
  const TofLoggerInfo info{"VL53L8CX",
                           kW,
                           kH,
                           DESKMATE_TOF_HZ,
                           DESKMATE_TOF_FLIP_ROWS != 0,
                           DESKMATE_TOF_FLIP_COLS != 0,
                           "closest",
                           DESKMATE_FIRMWARE_VERSION};
  writeLine(formatHelloLine(line, sizeof(line), millis(), info));
}

void sendError(const char *where, int code) {
  writeLine(formatErrorLine(line, sizeof(line), millis(), where, code));
}

// 0 이면 성공. 실패하면 어느 단계인지 tof_err 로 알린다.
bool startSensor() {
  sensor.begin();
  uint8_t st = sensor.init();  // 펌웨어 업로드, 수 초 걸린다
  if (st != VL53L8CX_STATUS_OK) {
    sendError("init", st);
    return false;
  }
  if ((st = sensor.set_resolution(VL53L8CX_RESOLUTION_8X8)) != VL53L8CX_STATUS_OK) {
    sendError("set_resolution", st);
    return false;
  }
  if ((st = sensor.set_ranging_frequency_hz(DESKMATE_TOF_HZ)) != VL53L8CX_STATUS_OK) {
    sendError("set_frequency", st);
    return false;
  }
  // 사람이 배경보다 앞에 있으니 가장 가까운 대상. TofPosture 합성 시험도 Closest 가정.
  if ((st = sensor.set_target_order(VL53L8CX_TARGET_ORDER_CLOSEST)) != VL53L8CX_STATUS_OK) {
    sendError("set_target_order", st);
    return false;
  }
  if ((st = sensor.set_ranging_mode(VL53L8CX_RANGING_MODE_CONTINUOUS)) != VL53L8CX_STATUS_OK) {
    sendError("set_ranging_mode", st);
    return false;
  }
  if ((st = sensor.start_ranging()) != VL53L8CX_STATUS_OK) {
    sendError("start_ranging", st);
    return false;
  }
  return true;
}

void handleCommands() {
  while (Serial.available() > 0) {
    switch (Serial.read()) {
      case 'b': estimator.resetBaseline(); break;
      case 'g': estimator.requestBackgroundCapture(); break;
      case 'r': estimator.reset(); break;
      case 'h': sendHello(); break;
      default: break;
    }
  }
}

void readFrame() {
  uint8_t ready = 0;
  uint8_t st = sensor.check_data_ready(&ready);
  if (st != VL53L8CX_STATUS_OK) {
    sendError("check_data_ready", st);
    return;
  }
  if (!ready) return;
  if ((st = sensor.get_ranging_data(&results)) != VL53L8CX_STATUS_OK) {
    sendError("get_ranging_data", st);
    return;
  }
  const uint32_t now = millis();
  for (uint16_t i = 0; i < kZones; ++i) {
    const uint16_t k = static_cast<uint16_t>(VL53L8CX_NB_TARGET_PER_ZONE * i);
    if (results.nb_target_detected[i] > 0) {
      raw_d[i] = results.distance_mm[k];
      raw_st[i] = results.target_status[k];
    } else {
      raw_d[i] = -1;
      raw_st[i] = 255;
    }
  }
  remapZones(raw_d, raw_st, scene_d, scene_st, kW, kH, DESKMATE_TOF_FLIP_ROWS != 0,
             DESKMATE_TOF_FLIP_COLS != 0);
  TofFrameView view;
  view.width = kW;
  view.height = kH;
  view.distance_mm = scene_d;
  view.status = scene_st;
  estimator.update(view, now);
  writeLine(formatTofLine(line, sizeof(line), seq++, now, kW, kH, raw_d, raw_st,
                          estimator.features()));
}

}  // namespace

void setup() {
  Serial.setTxBufferSize(2048);
  Serial.begin(DESKMATE_TOF_LOG_BAUD);
  if (DESKMATE_TOF_PWREN_PIN >= 0) {
    pinMode(DESKMATE_TOF_PWREN_PIN, OUTPUT);
    digitalWrite(DESKMATE_TOF_PWREN_PIN, HIGH);
    delay(10);
  }
  Wire.begin(DESKMATE_TOF_SDA, DESKMATE_TOF_SCL, DESKMATE_TOF_I2C_HZ);
  sendHello();
  last_init_ms = millis();
  ranging = startSensor();
  if (ranging) sendHello();
}

void loop() {
  handleCommands();
  if (!ranging) {
    if (millis() - last_init_ms >= kInitRetryMs) {
      last_init_ms = millis();
      ranging = startSensor();
      if (ranging) sendHello();
    }
    return;
  }
  readFrame();
}
