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
uint32_t last_c1001_retry_ms = 0;
uint8_t mmwave_fail_streak = 0;
uint32_t c1001_retry_interval_ms = kC1001RetryMs;
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
  Serial1.begin(kC1001Baud, SERIAL_8N1, kC1001RxPin, kC1001TxPin);
  pi4_serial.begin(kPi4Baud, SERIAL_8N1, kPi4RxPin, kPi4TxPin);

  Serial.println(F("DESKMATE ESP32 sensor node starting"));
  mmwave_ready = mmwave.begin();
  Serial.println(mmwave_ready ? F("C1001 ready") : F("C1001 initialization failed"));
  environment_sensors.begin(millis(), environment_sample);
}

void loop() {
  const uint32_t now_ms = millis();
  // C1001 boots several seconds after power-up and may be plugged in after the ESP32; keep retrying.
  // 실패가 이어지면 간격을 두 배씩 늘린다. probe 를 통과하고도 begin() 이 실패하는
  // 상태(센서가 응답하다 마는 경우)에서는 시도 한 번이 15 s 를 먹어서, 고정 10 s
  // 간격으로 부르면 루프가 영영 제 주기를 못 돈다 - 환경값까지 같이 늦어진다.
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
  if (now_ms - last_mmwave_ms >= kMmwavePeriodMs) {
    last_mmwave_ms = now_ms;
    const MmwaveSample sample = mmwave_ready ? mmwave.read() : MmwaveSample{};
    // 센서가 답을 멈추면 read() 한 번이 수 초를 먹어 환경·heartbeat 까지 같이 굶는다.
    // 몇 번 연속으로 응답이 없으면 죽은 것으로 보고 재시도 경로(probe 300 ms)로 돌린다.
    // 센서가 돌아오면 그 경로가 알아서 다시 잡는다.
    if (mmwave_ready && mmwave.lastReadTimedOut()) {
      if (++mmwave_fail_streak >= kC1001FailStreakMax) {
        mmwave_ready = false;
        mmwave_fail_streak = 0;
        last_c1001_retry_ms = now_ms;
        c1001_retry_interval_ms = kC1001RetryMs;
        Serial.println(F("C1001 stopped answering - back to retry"));
      }
    } else {
      mmwave_fail_streak = 0;
    }
    const DrowsyState drowsy_state = drowsy_detector.update(sample, now_ms);
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
