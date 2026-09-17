#include <Arduino.h>

#include "pins.h"
#include "sensors/c1001/C1001Passive.h"
#include "sensors/c1001/DrowsyDetector.h"
#include "sensors/environment/EnvironmentSensors.h"
#include "transport/frame.h"
#include "transport/usb_json.h"

using namespace deskmate;

HardwareSerial pi4_serial(2);
C1001Passive mmwave(Serial1);
DrowsyDetector drowsy_detector;
EnvironmentSensors environment_sensors;

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

// Wire scaling matches hub/deskmate_hub/ingest/uart_frame.py (_ENV = "<HhHHB"):
// co2 ppm as-is, temp/humidity x10, lux as-is (integer lux, clamped to u16).
uint16_t clampU16(float value) {
  if (value <= 0.0F) return 0;
  if (value >= 65535.0F) return 65535;
  return static_cast<uint16_t>(value + 0.5F);
}

int16_t scaledI16x10(float value) {
  const float scaled = value * 10.0F;
  if (scaled <= -32768.0F) return -32768;
  if (scaled >= 32767.0F) return 32767;
  return static_cast<int16_t>(scaled >= 0.0F ? scaled + 0.5F : scaled - 0.5F);
}

void writeEnvironmentUart(uint32_t now_ms, const EnvironmentSample& sample) {
#if DESKMATE_UART2_TX
  const uint16_t co2_ppm = sample.co2_valid ? clampU16(static_cast<float>(sample.co2_ppm)) : 0;
  const int16_t temp_x10 = sample.temp_valid ? scaledI16x10(sample.temp_c) : 0;
  const uint16_t humidity_x10 = sample.humidity_valid ? clampU16(sample.humidity_pct * 10.0F) : 0;
  const uint16_t lux = sample.lux_valid ? clampU16(sample.lux) : 0;
  const uint8_t valid_bits = (sample.co2_valid ? 0x01 : 0) |
                             (sample.temp_valid ? 0x02 : 0) |
                             (sample.humidity_valid ? 0x04 : 0) |
                             (sample.lux_valid ? 0x08 : 0);
  const uint8_t payload[] = {
      static_cast<uint8_t>(co2_ppm & 0xFF),
      static_cast<uint8_t>(co2_ppm >> 8),
      static_cast<uint8_t>(temp_x10 & 0xFF),
      static_cast<uint8_t>((temp_x10 >> 8) & 0xFF),
      static_cast<uint8_t>(humidity_x10 & 0xFF),
      static_cast<uint8_t>(humidity_x10 >> 8),
      static_cast<uint8_t>(lux & 0xFF),
      static_cast<uint8_t>(lux >> 8),
      valid_bits,
  };
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
  environment_sensors.begin();
  Serial.println(mmwave_ready ? F("C1001 ready") : F("C1001 initialization failed"));
}

void loop() {
  const uint32_t now_ms = millis();
  if (now_ms - last_mmwave_ms >= kMmwavePeriodMs) {
    last_mmwave_ms = now_ms;
    const MmwaveSample sample = mmwave_ready ? mmwave.read() : MmwaveSample{};
    const DrowsyState drowsy_state = drowsy_detector.update(sample, now_ms);
    writeMmwaveJson(Serial, now_ms, sample, drowsy_state);
    writeMmwaveUart(now_ms, sample, drowsy_state);
  }
  if (now_ms - last_environment_ms >= kEnvironmentPeriodMs) {
    last_environment_ms = now_ms;
    const EnvironmentSample sample = environment_sensors.read();
    writeEnvironmentJson(Serial, now_ms, sample);
    writeEnvironmentUart(now_ms, sample);
  }
  if (now_ms - last_heartbeat_ms >= kMmwavePeriodMs) {
    last_heartbeat_ms = now_ms;
    writeHeartbeatUart(now_ms);
  }
}
