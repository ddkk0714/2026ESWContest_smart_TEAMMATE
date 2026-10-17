#include <Arduino.h>

#include "pins.h"
#include "sensors/c1001/C1001Passive.h"
#include "sensors/c1001/DrowsyDetector.h"
#include "sensors/environment/EnvironmentPayload.h"
#include "sensors/environment/EnvironmentSensors.h"
#include "sensors/tof/TofArray.h"
#include "sensors/tof/TofPayload.h"
#include "transport/frame.h"
#include "transport/usb_json.h"

using namespace deskmate;

// VL53L8CX 드라이버의 IO_Read 가 약 4 KB 버퍼를 스택에 두므로 기본 8 KB loop 스택은 모자란다.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

// ToF 배치는 빌드 플래그로 바꾼다(실기 방향 확인 뒤 고정): dataset/tof_logger/README.md "방향 확인".
#ifndef DESKMATE_TOF_COUNT
#define DESKMATE_TOF_COUNT 2
#endif
#ifndef DESKMATE_TOF_FLIP_ROWS
#define DESKMATE_TOF_FLIP_ROWS 0
#endif
#ifndef DESKMATE_TOF_FLIP_COLS
#define DESKMATE_TOF_FLIP_COLS 1
#endif
#ifndef DESKMATE_TOF_STACK_ROWS
#define DESKMATE_TOF_STACK_ROWS 0  // 0: A|B 가로(16×8), 1: A 위 B 아래(8×16)
#endif
#ifndef DESKMATE_TOF_SWAP
#define DESKMATE_TOF_SWAP 0
#endif

TofArrayConfig tofConfig() {
  TofArrayConfig c;
  c.wire = &Wire1;
  c.sda = kTofSdaPin;
  c.scl = kTofSclPin;
  c.i2c_hz = kTofI2cHz;
  c.ranging_hz = kTofRangingHz;
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

HardwareSerial pi4_serial(2);
C1001Passive mmwave(Serial1);
DrowsyDetector drowsy_detector;
EnvironmentSensors environment_sensors(Wire);
EnvironmentSample environment_sample;
TofArray tof(tofConfig());  // 전역: zone 버퍼·추정기가 커서 스택에 두지 않는다

uint32_t last_mmwave_ms = 0;
uint32_t last_environment_ms = 0;
uint32_t last_heartbeat_ms = 0;
// ToF 0x03/0x05 는 "다음 기한" 타이머다(periodDue). 기한을 now 로 덮는 방식은 루프·표본 위상만큼 매번 늦어져
// 10-17 실측에서 0x05 가 센서당 ≈4 Hz(264 ms), 0x03 이 4.2 Hz 로 떨어졌다.
uint32_t next_tof_feature_ms = 0;
uint32_t last_tof_posture_ms = 0;
uint32_t next_tof_grid_ms = 0;
uint32_t last_c1001_retry_ms = 0;
uint32_t c1001_retry_interval_ms = kC1001RetryMs;
uint16_t mmwave_sequence = 0;
uint16_t environment_sequence = 0;
uint16_t heartbeat_sequence = 0;
uint16_t tof_sequence = 0;  // 0x03·0x04 가 같은 흐름을 쓴다
uint16_t tof_grid_sequence = 0;  // 0x05 조각마다 +1. 같은 격자의 조각은 frame_id 로 묶인다
uint32_t tof_grid_tx = 0;        // 보낸 0x05 프레임 수(USB tof_feature 의 tx_grid — 송신 주기 실측용)
uint32_t tof_feature_tx = 0;     // 보낸 0x03 프레임 수(tx_feature)
bool usb_tof_grid = false;       // USB 명령 'z' 토글: 원본 8×8 을 {"t":"tof_grid"} 로도 낸다(PC 진단, tools/sensor_monitor.py)
uint8_t tof_grid_frame_id = 0;
bool mmwave_ready = false;

WireDrowsyState wireDrowsyState(DrowsyState state) {
  switch (state) {
    case DrowsyState::kNoLock:
      return WireDrowsyState::kNoLock;
    case DrowsyState::kWarmup:
      return WireDrowsyState::kWarmup;
    case DrowsyState::kAwake:
      return WireDrowsyState::kAwake;
    case DrowsyState::kDrowsy:
      return WireDrowsyState::kDrowsy;
    default:
      return WireDrowsyState::kNoPerson;
  }
}

// 주기 타이머: 기한이 지났으면 true 를 돌려주고 기한을 period 만큼 민다(평균 주기가 정확히 period).
// 한 주기 넘게 뒤처졌으면(센서가 한동안 죽어 있었거나 루프가 막혔으면) now 기준으로 재동기한다.
bool periodDue(uint32_t now_ms, uint32_t& next_ms, uint32_t period_ms) {
  if (static_cast<int32_t>(now_ms - next_ms) < 0) return false;
  next_ms += period_ms;
  if (static_cast<int32_t>(now_ms - next_ms) >= 0) next_ms = now_ms + period_ms;
  return true;
}

void writeMmwaveUart(uint32_t now_ms, const MmwaveSample& sample, DrowsyState drowsy_state) {
#if DESKMATE_UART2_TX
  const uint16_t distance = sample.distance_valid ? sample.distance_cm : kFrameNullU16;
  const uint8_t payload[] = {
      static_cast<uint8_t>(sample.present),
      static_cast<uint8_t>(sample.motion_state),
      sample.motion_level,
      static_cast<uint8_t>(distance & 0xFF),
      static_cast<uint8_t>((distance >> 8) & 0xFF),
      sample.resp_valid ? sample.resp_bpm : kFrameNullU8,
      static_cast<uint8_t>(sample.resp_valid),
      sample.heart_valid ? sample.heart_bpm : kFrameNullU8,
      static_cast<uint8_t>(sample.heart_valid),
      static_cast<uint8_t>(wireDrowsyState(drowsy_state)),
      static_cast<uint8_t>(sample.valid),
  };
  writeUartFrame(pi4_serial, kFrameTypeMmwave, mmwave_sequence++, now_ms, payload,
                 sizeof(payload));
#endif
}

void writeEnvironmentUart(uint32_t now_ms, const EnvironmentSample& sample) {
#if DESKMATE_UART2_TX
  uint8_t payload[kEnvironmentPayloadSize];
  encodeEnvironmentPayload(sample, payload);
  writeUartFrame(pi4_serial, kFrameTypeEnvironment, environment_sequence++, now_ms, payload,
                 sizeof(payload));
#endif
}

void writeTofFeatureUart(uint32_t now_ms) {
#if DESKMATE_UART2_TX
  uint8_t payload[kTofFeaturePayloadSize];
  encodeTofFeaturePayload(tof.features(), tof.okMask(), payload);
  if (writeUartFrame(pi4_serial, kFrameTypeTofFeatures, tof_sequence++, now_ms, payload,
                     sizeof(payload))) {
    ++tof_feature_tx;
  }
#endif
}

void writeTofPostureUart(uint32_t now_ms) {
#if DESKMATE_UART2_TX
  uint8_t payload[kTofPosturePayloadSize];
  encodeTofPosturePayload(tof.features(), payload);
  writeUartFrame(pi4_serial, kFrameTypeTofPosture, tof_sequence++, now_ms, payload,
                 sizeof(payload));
#endif
}

// 센서별 원본 8×8 → TYPE 0x05 프레임(살아 있는 센서마다 하나, 같은 now_ms·frame_id, SEQ 만 올라감).
// 자세 AI 모델은 Pi 4 에서 센서별로 추론해 융합한다(10-17 표준 — TofPayload.h). 16×8 스티칭은 보내지 않는다.
// 한 프레임이라도 못 쓰면 나머지는 건너뛴다(hub 는 frame_id 가 바뀌면 미완성 표본을 폐기한다).
void writeTofGridUart(uint32_t now_ms) {
#if DESKMATE_UART2_TX
  static uint8_t payload[kTofGridMaxPayloadSize];  // 200 B — loop 스택 대신 정적
  const uint8_t sensors = tof.okCount();
  if (sensors == 0) return;
  const uint8_t frame_id = tof_grid_frame_id++;
  const uint8_t flags = (DESKMATE_TOF_FLIP_ROWS ? kTofGridFlagFlipRows : 0) |
                        (DESKMATE_TOF_FLIP_COLS ? kTofGridFlagFlipCols : 0) |
                        (DESKMATE_TOF_STACK_ROWS ? kTofGridFlagStackRows : 0) |
                        (DESKMATE_TOF_SWAP ? kTofGridFlagSwap : 0);
  for (uint8_t i = 0; i < DESKMATE_TOF_COUNT; ++i) {
    if (!tof.sensorOk(i)) continue;
    const uint16_t len = encodeTofSensorFrame(tof.sensorDistance(i), tof.sensorStatus(i), kTofSensorW, kTofSensorH,
                                              i, sensors, frame_id, tof.okMask(), flags, payload);
    if (len == 0 ||
        !writeUartFrame(pi4_serial, kFrameTypeTofGrid, tof_grid_sequence++, now_ms, payload, len)) {
      return;
    }
    ++tof_grid_tx;
  }
#endif
}

// 같은 표본·같은 frame_id 를 USB JSON 으로(PC 진단, 'z' 로 켬). writeTofGridUart 바로 뒤에서만 부른다.
void writeTofGridJson(uint32_t now_ms) {
  const int16_t* dist[DESKMATE_TOF_COUNT];
  const uint8_t* status[DESKMATE_TOF_COUNT];
  for (uint8_t i = 0; i < DESKMATE_TOF_COUNT; ++i) {
    dist[i] = tof.sensorDistance(i);
    status[i] = tof.sensorStatus(i);
  }
  const uint8_t flags = (DESKMATE_TOF_FLIP_ROWS ? kTofGridFlagFlipRows : 0) |
                        (DESKMATE_TOF_FLIP_COLS ? kTofGridFlagFlipCols : 0) |
                        (DESKMATE_TOF_STACK_ROWS ? kTofGridFlagStackRows : 0) |
                        (DESKMATE_TOF_SWAP ? kTofGridFlagSwap : 0);
  writeTofGridJson(Serial, now_ms, static_cast<uint8_t>(tof_grid_frame_id - 1), tof.okMask(), flags, kTofSensorW,
                   kTofSensorH, DESKMATE_TOF_COUNT, dist, status);
}

void writeHeartbeatUart(uint32_t now_ms) {
#if DESKMATE_UART2_TX
  const uint16_t version = DESKMATE_FIRMWARE_VERSION;
  // 8번째 바이트 tof_ok(b0 = A, b1 = B)는 10-16 추가. 이전 디코더는 7 B 까지만 읽는다.
  const uint8_t payload[] = {
      static_cast<uint8_t>(now_ms & 0xFF),
      static_cast<uint8_t>((now_ms >> 8) & 0xFF),
      static_cast<uint8_t>((now_ms >> 16) & 0xFF),
      static_cast<uint8_t>((now_ms >> 24) & 0xFF),
      static_cast<uint8_t>(version & 0xFF),
      static_cast<uint8_t>((version >> 8) & 0xFF),
      static_cast<uint8_t>(mmwave_ready),
      tof.okMask(),
  };
  writeUartFrame(pi4_serial, kFrameTypeHeartbeat, heartbeat_sequence++, now_ms, payload,
                 sizeof(payload));
#endif
}

// USB 로 받는 한 글자 명령(수집 모드와 같은 글자):
//   b  바른 자세 기준선 다시 잡기   g  다음 프레임을 배경으로(빈 자리에서)   r  추정기 초기화
//   z  원본 8×8 USB 출력(tof_grid) 켜기/끄기 — 기본 꺼짐
void handleUsbCommands() {
  while (Serial.available() > 0) {
    switch (Serial.read()) {
      case 'b': tof.estimator().resetBaseline(); Serial.println(F("# tof baseline reset")); break;
      case 'g': tof.estimator().requestBackgroundCapture(); Serial.println(F("# tof background capture")); break;
      case 'r': tof.estimator().reset(); Serial.println(F("# tof estimator reset")); break;
      case 'z':
        usb_tof_grid = !usb_tof_grid;
        Serial.println(usb_tof_grid ? F("# tof_grid usb on") : F("# tof_grid usb off"));
        break;
      default: break;
    }
  }
}

void printTofStatus() {
  Serial.print(F("VL53L8CX x"));
  Serial.print(DESKMATE_TOF_COUNT);
  Serial.print(F(": ok_mask="));
  Serial.print(tof.okMask());
  Serial.print(F(" grid="));
  Serial.print(tof.width());
  Serial.print('x');
  Serial.print(tof.height());
  for (uint8_t i = 0; i < DESKMATE_TOF_COUNT; ++i) {
    if (tof.sensorOk(i) || tof.errorWhere(i) == nullptr) continue;
    Serial.print(i == 0 ? F(" A=") : F(" B="));
    Serial.print(tof.errorWhere(i));
    Serial.print('/');
    Serial.print(tof.errorCode(i));
  }
  Serial.println();
}

void setup() {
  // USB TX 링 버퍼를 키운다(begin() 앞에서). tof_grid 한 줄 ≈1.1 kB 를 print 할 때 루프가 막히지 않게 —
  // 115200 에서 1.1 kB 는 95 ms 라 기본 버퍼면 ToF 폴링·Pi 4 송신 주기가 밀린다.
  Serial.setTxBufferSize(4096);
  Serial.begin(kUsbBaud);
  // 능동 보고를 놓치지 않도록 수신 버퍼를 키운다. begin() 앞에서 해야 적용된다
  Serial1.setRxBufferSize(1024);
  Serial1.begin(kC1001Baud, SERIAL_8N1, kC1001RxPin, kC1001TxPin);
  pi4_serial.begin(kPi4Baud, SERIAL_8N1, kPi4RxPin, kPi4TxPin);

  Serial.println(F("DESKMATE ESP32 sensor node starting"));
  mmwave_ready = mmwave.begin();
  Serial.println(mmwave_ready ? F("C1001 ready") : F("C1001 initialization failed"));
  drowsy_detector.begin(millis());
  environment_sensors.begin(millis(), environment_sample);
  // ToF 는 센서당 init(펌웨어 업로드)에 수 초가 걸린다. 실패한 센서는 loop 에서 5 s 마다 다시 올린다.
  tof.begin(millis(), &Serial);
  printTofStatus();
}

void loop() {
  handleUsbCommands();
  // 파서는 논블로킹이다. 주기와 무관하게 매 루프에서 밀린 바이트를 소화하고,
  // 판정기는 값이 실제로 갱신된 것만 소비한다. 보고가 끊기면 질의로 깨운다.
  if (mmwave_ready) {
    mmwave.poll();
    mmwave.nudgeIfSilent(millis());
  }

  const uint32_t now_ms = millis();
  // C1001 boots several seconds after power-up and may be plugged in after the ESP32; keep retrying.
  // 실패가 이어지면 간격을 두 배씩 늘린다. probe 를 통과하고도 begin() 이 실패하는
  // 상태(센서가 응답하다 마는 경우)에서는 시도 한 번이 수십 초를 먹어서, 고정 10 s
  // 간격으로 부르면 루프가 영영 제 주기를 못 돈다 - 환경값까지 같이 늦어진다.
  // 한 번 붙은 뒤 보고가 끊기는 건 여기가 아니라 nudgeIfSilent() 가 질의로 깨운다.
  if (!mmwave_ready && now_ms - last_c1001_retry_ms >= c1001_retry_interval_ms) {
    last_c1001_retry_ms = now_ms;
    mmwave_ready = mmwave.begin();
    if (mmwave_ready) {
      c1001_retry_interval_ms = kC1001RetryMs;
      Serial.println(F("C1001 ready (late init)"));
    } else if (c1001_retry_interval_ms < kC1001RetryMaxMs) {
      c1001_retry_interval_ms *= 2;
      Serial.print(F("C1001 retry backoff -> "));
      Serial.println(c1001_retry_interval_ms);
    }
  }
  if (mmwave_ready) drowsy_detector.update(mmwave, now_ms);

  // ToF: 매 루프 폴링(15 Hz ×2). 원본 8×8(0x05, 센서별)은 5 Hz 기한마다 그 뒤 첫 표본을 보내고,
  // 규칙 특징은 5 Hz 로, 자세 요약·USB 진단은 1 Hz 로 낸다.
  // 살아 있는 센서가 없으면 아무것도 보내지 않는다 — hub 는 freshness 로 미가용 처리한다.
  tof.poll(now_ms);
  const bool tof_new_grid = tof.frameReady();
  if (tof.okCount() > 0) {
    // 0x05 는 기한이 지난 뒤 처음 생긴 표본을 보낸다(기한은 표본 시각이 아니라 period 격자에 맞춰 민다).
    if (tof_new_grid && periodDue(now_ms, next_tof_grid_ms, kTofGridPeriodMs)) {
      writeTofGridUart(now_ms);
      if (usb_tof_grid) writeTofGridJson(now_ms);
    }
    if (periodDue(now_ms, next_tof_feature_ms, kTofFeaturePeriodMs)) {
      writeTofFeatureUart(now_ms);
    }
    if (now_ms - last_tof_posture_ms >= kTofPosturePeriodMs) {
      last_tof_posture_ms = now_ms;
      writeTofPostureUart(now_ms);
      writeTofJson(Serial, now_ms, tof.features(), tof.okMask(), tof_grid_tx, tof_feature_tx);
    }
  } else if (now_ms - last_tof_posture_ms >= kTofRetryMs) {
    last_tof_posture_ms = now_ms;
    printTofStatus();
  }

  if (now_ms - last_mmwave_ms >= kMmwavePeriodMs) {
    last_mmwave_ms = now_ms;
    const MmwaveSample sample = mmwave_ready ? mmwave.sample(now_ms) : MmwaveSample{};
    const DrowsyState drowsy_state = drowsy_detector.state();
    writeMmwaveJson(Serial, now_ms, sample, drowsy_state);
    writeMmwaveUart(now_ms, sample, drowsy_state);
  }

  environment_sensors.poll(now_ms, environment_sample);
  if (now_ms - last_environment_ms >= kEnvironmentPeriodMs) {
    last_environment_ms = now_ms;
    writeEnvironmentJson(Serial, now_ms, environment_sample);
    writeEnvironmentUart(now_ms, environment_sample);
  }
  if (now_ms - last_heartbeat_ms >= kMmwavePeriodMs) {
    last_heartbeat_ms = now_ms;
    writeHeartbeatUart(now_ms);
  }
}
