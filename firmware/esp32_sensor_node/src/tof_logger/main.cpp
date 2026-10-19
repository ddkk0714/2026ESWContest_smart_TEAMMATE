// ToF 학습 데이터 수집 펌웨어 (env:tof_logger). 운영 펌웨어가 아니다.
//
// VL53L8CX 8×8 ×2 → 한 격자(16×8 또는 8×16) → 규칙 추정기(TofPosture) → USB 한 줄 JSON
// (장면 방향 zone + 규칙 판정). PC 수집기 dataset/tof_logger/tof_log.py 가 받아서 지시 자세와
// 함께 세션으로 저장한다. 센서 읽기·주소 배정·이어 붙이기는 운영 펌웨어와 같은
// sensors/tof/TofArray 를 쓴다 — 배선·핀은 include/pins.h, docs/breadboard-esp32-node.md.
//
// 10-16 부터 d·st 는 **장면 방향으로 이미 돌린 격자**다(두 센서를 붙이려면 방향을 먼저 맞춰야
// 해서). 그래서 hello 의 flip_rows/flip_cols 는 항상 false 로 보내고, 방향을 바꾸려면 아래
// 매크로로 다시 굽는다. 한쪽 센서만 살아 있으면 8×8 로 떨어진다(w·h 가 줄마다 들어 있다).
//
// USB 로 받는 한 글자 명령:
//   b  기준 자세 다시 잡기 (TofPostureEstimator::resetBaseline)
//   g  다음 프레임을 배경으로 (빈 자리에서)
//   r  추정기 전체 초기화
//   h  hello 다시 보내기
#include <Arduino.h>
#include <Wire.h>

#include "pins.h"
#include "preprocess/tof/TofPosture.h"
#include "sensors/tof/TofArray.h"
#include "tof_logger/TofLine.h"

// VL53L8CX 드라이버의 IO_Read 가 약 4 KB 버퍼를 스택에 두므로 기본 8 KB loop 스택은 모자란다.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

#ifndef DESKMATE_TOF_COUNT
#define DESKMATE_TOF_COUNT 2
#endif
#ifndef DESKMATE_TOF_I2C_HZ
#define DESKMATE_TOF_I2C_HZ kTofI2cHz  // 배선이 길어 init 이 실패하면 400000
#endif
#ifndef DESKMATE_TOF_HZ
#define DESKMATE_TOF_HZ kTofRangingHz
#endif
#ifndef DESKMATE_TOF_LOG_BAUD
#define DESKMATE_TOF_LOG_BAUD 921600  // 한 줄 약 1.4 KB × 15 Hz ≈ 210 kbps
#endif
// 센서 zone 순서 → 장면 방향. 손 들기로 확인하고 맞춘다(dataset/tof_logger/README).
#ifndef DESKMATE_TOF_FLIP_ROWS
#define DESKMATE_TOF_FLIP_ROWS 0
#endif
#ifndef DESKMATE_TOF_FLIP_COLS
#define DESKMATE_TOF_FLIP_COLS 1  // ST 예제가 행 안의 zone 을 거꾸로 찍는다
#endif
#ifndef DESKMATE_TOF_STACK_ROWS
#define DESKMATE_TOF_STACK_ROWS 0  // 0: A|B 가로(16×8), 1: A 위 B 아래(8×16)
#endif
#ifndef DESKMATE_TOF_SWAP
#define DESKMATE_TOF_SWAP 0
#endif

using namespace deskmate;

namespace {

TofArrayConfig makeConfig() {
  TofArrayConfig c;
  c.wire = &Wire1;
  c.sda = kTofSdaPin;
  c.scl = kTofSclPin;
  c.i2c_hz = DESKMATE_TOF_I2C_HZ;
  c.ranging_hz = DESKMATE_TOF_HZ;
  c.count = DESKMATE_TOF_COUNT;
  c.lpn[0] = kTofLpnPinA;
  c.lpn[1] = kTofLpnPinB;
  c.address[0] = kTofAddressA;
  c.address[1] = VL53L8CX_DEFAULT_I2C_ADDRESS;
  c.flip_rows = DESKMATE_TOF_FLIP_ROWS != 0;
  c.flip_cols = DESKMATE_TOF_FLIP_COLS != 0;
  c.stack_rows = DESKMATE_TOF_STACK_ROWS != 0;
  c.swap = DESKMATE_TOF_SWAP != 0;
  c.retry_ms = kTofRetryMs;
  return c;
}

TofArray tof(makeConfig());  // 전역: zone 버퍼·추정기가 커서 스택에 두지 않는다
char line[kTofLineMax];
uint32_t seq = 0;
uint8_t last_ok_mask = 0xFF;
uint32_t last_err_report_ms = 0;
const char *last_err_where = nullptr;

void writeLine(size_t n) {
  if (n > 0) Serial.write(reinterpret_cast<const uint8_t *>(line), n);
}

void sendHello() {
  const uint8_t w = tof.width() > 0 ? tof.width() : kTofSensorW;
  const uint8_t h = tof.height() > 0 ? tof.height() : kTofSensorH;
  const TofLoggerInfo info{tof.okCount() >= 2 ? "VL53L8CX x2" : "VL53L8CX",
                           w,
                           h,
                           DESKMATE_TOF_HZ,
                           false,  // 격자는 이미 장면 방향이다
                           false,
                           "closest",
                           DESKMATE_FIRMWARE_VERSION};
  writeLine(formatHelloLine(line, sizeof(line), millis(), info));
}

void sendError() {
  if (tof.lastErrorWhere() == nullptr) return;
  writeLine(formatErrorLine(line, sizeof(line), millis(), tof.lastErrorWhere(),
                            tof.lastErrorCode()));
}

void handleCommands() {
  while (Serial.available() > 0) {
    switch (Serial.read()) {
      case 'b': tof.estimator().resetBaseline(); break;
      case 'g': tof.estimator().requestBackgroundCapture(); break;
      case 'r': tof.estimator().reset(); break;
      case 'h': sendHello(); break;
      default: break;
    }
  }
}

}  // namespace

void setup() {
  Serial.setTxBufferSize(4096);
  Serial.begin(DESKMATE_TOF_LOG_BAUD);
  sendHello();
  tof.begin(millis());
  sendHello();
  sendError();
  last_ok_mask = tof.okMask();
}

void loop() {
  handleCommands();
  const uint32_t now = millis();
  tof.poll(now);
  if (tof.okMask() != last_ok_mask) {  // 센서가 붙거나 떨어지면 격자가 바뀐다 → hello 갱신
    last_ok_mask = tof.okMask();
    sendHello();
  }
  if (tof.okCount() == 0 || tof.lastErrorWhere() != last_err_where) {
    if (now - last_err_report_ms >= 3000) {
      last_err_report_ms = now;
      last_err_where = tof.lastErrorWhere();
      sendError();
    }
  }
  if (!tof.frameReady()) return;
  writeLine(formatTofLine(line, sizeof(line), seq++, now, tof.width(), tof.height(),
                          tof.distance(), tof.status(), tof.features()));
}
