#pragma once

#include <Arduino.h>

#ifndef DESKMATE_UART2_TX
#define DESKMATE_UART2_TX 1
#endif

#ifndef DESKMATE_FIRMWARE_VERSION
#define DESKMATE_FIRMWARE_VERSION 0x0110
#endif

namespace deskmate {

constexpr uint32_t kUsbBaud = 115200;
constexpr uint32_t kC1001Baud = 115200;
constexpr uint32_t kPi4Baud = 115200;

constexpr int kC1001RxPin = 16;
constexpr int kC1001TxPin = 17;
constexpr int kPi4TxPin = 25;
constexpr int kPi4RxPin = 26;
constexpr int kEnvironmentSdaPin = 21;
constexpr int kEnvironmentSclPin = 22;
constexpr int kDht22DataPin = 27;

constexpr uint8_t kBh1750Address = 0x23;

constexpr uint32_t kMmwavePeriodMs = 1000;
constexpr uint32_t kEnvironmentPeriodMs = 5000;
constexpr uint32_t kScd41ReadyPollMs = 500;
constexpr uint32_t kBh1750ReadIntervalMs = 1000;
constexpr uint32_t kDht22ReadIntervalMs = 2000;
constexpr uint32_t kEnvironmentSensorRetryMs = 10000;
constexpr uint32_t kEnvironmentStaleAfterMs = 15000;
constexpr uint32_t kC1001RetryMs = 10000;
// 재시도가 계속 실패하면 간격을 두 배씩 늘려 이 값까지 간다. begin() 은 센서가
// 응답하다 마는 상태에서 15 s 를 먹으므로 10 s 마다 부르면 루프가 못 돈다.
constexpr uint32_t kC1001RetryMaxMs = 120000;

// ---- ToF: VL53L8CX ×2, 전용 I2C 버스(Wire1) ----
// GPIO16/17 은 C1001 UART1, GPIO21/22 는 환경 센서 I2C(SCD41 은 100 kHz 까지)라
// ToF 는 1 MHz 전용 버스를 따로 둔다. 76EHwan/ESP32_VL53L8 의 LPn=16 · SPI_I2C_N=17 · NCS=5 는
// C1001(16/17) 과 겹쳐서 바꿨다. NCS·SPI_I2C_N 은 GND 로 묶는다(I2C 모드, 핀 소비 없음).
// 배선·브레드보드 배치: docs/breadboard-esp32-node.md
constexpr int kTofSdaPin = 23;
constexpr int kTofSclPin = 18;
constexpr int kTofLpnPinA = 32;           // 센서 A (장면의 왼쪽 또는 위)
constexpr int kTofLpnPinB = 33;           // 센서 B (장면의 오른쪽 또는 아래)
constexpr uint32_t kTofI2cHz = 1000000;   // 펌웨어 84 KB 업로드·8×8 15 Hz ×2. 선이 길어 실패하면 400000
constexpr uint8_t kTofRangingHz = 15;     // 8×8 최대
constexpr uint16_t kTofAddressA = 0x54;   // 8-bit. A 만 주소를 바꾸고 B 는 기본 0x52 를 쓴다
constexpr uint32_t kTofRetryMs = 5000;
constexpr uint32_t kTofFeaturePeriodMs = 200;   // UART TYPE 0x03 (tof_feature) 5 Hz
constexpr uint32_t kTofPosturePeriodMs = 1000;  // UART TYPE 0x04 + USB JSON 1 Hz
// UART TYPE 0x05 원본 격자 5 Hz. 16×8 은 조각 2개(각 200 B payload, wire ≈ 215 B) → 약 2.2 kB/s,
// 115200 8N1(11.5 kB/s)의 19 %. 센서 주기 15 Hz 를 그대로 보내면 56 % 라 5 Hz 로 솎는다.
constexpr uint32_t kTofGridPeriodMs = 200;

// 졸음 판정 임계값은 실측 근거 주석과 함께 sensors/c1001/DrowsyDetector.h 에 있다.
// 시연용으로 확정 시간만 줄이려면 -DDESKMATE_DROWSY_HOLD_SCALE_PCT=<퍼센트>.

}  // namespace deskmate
