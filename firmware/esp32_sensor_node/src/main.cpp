#include <Arduino.h>

#include "pins.h"
#include "sensors/c1001/C1001Passive.h"
#include "sensors/c1001/DrowsyDetector.h"
#include "sensors/environment/EnvironmentPayload.h"
#include "sensors/environment/EnvironmentSensors.h"
#include "transport/frame.h"
#include "transport/usb_json.h"

using namespace deskmate;

HardwareSerial pi4_serial(2);
C1001Passive mmwave(Serial1);
DrowsyDetector drowsy_detector;
EnvironmentSensors environment_sensors(Wire);
EnvironmentSample environment_sample;

uint32_t last_mmwave_ms = 0;
uint32_t last_environment_ms = 0;
uint32_t last_heartbeat_ms = 0;
uint16_t mmwave_sequence = 0;
uint16_t environment_sequence = 0;
uint16_t heartbeat_sequence = 0;
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

void writeHeartbeatUart(uint32_t now_ms) {
#if DESKMATE_UART2_TX
  const uint16_t version = DESKMATE_FIRMWARE_VERSION;
  const uint8_t payload[] = {
      static_cast<uint8_t>(now_ms & 0xFF),
      static_cast<uint8_t>((now_ms >> 8) & 0xFF),
      static_cast<uint8_t>((now_ms >> 16) & 0xFF),
      static_cast<uint8_t>((now_ms >> 24) & 0xFF),
      static_cast<uint8_t>(version & 0xFF),
      static_cast<uint8_t>((version >> 8) & 0xFF),
      static_cast<uint8_t>(mmwave_ready),
  };
  writeUartFrame(pi4_serial, kFrameTypeHeartbeat, heartbeat_sequence++, now_ms, payload,
                 sizeof(payload));
#endif
}

void setup() {
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
}

void loop() {
  // 파서는 논블로킹이다. 주기와 무관하게 매 루프에서 밀린 바이트를 소화하고,
  // 판정기는 값이 실제로 갱신된 것만 소비한다. 보고가 끊기면 질의로 깨운다.
  if (mmwave_ready) {
    mmwave.poll();
    mmwave.nudgeIfSilent(millis());
  }

  const uint32_t now_ms = millis();
  if (mmwave_ready) drowsy_detector.update(mmwave, now_ms);

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
