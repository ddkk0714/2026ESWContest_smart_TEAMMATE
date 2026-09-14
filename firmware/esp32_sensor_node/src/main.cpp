#include <Arduino.h>

#include "pins.h"
#include "sensors/c1001/C1001Passive.h"
#include "sensors/c1001/DrowsyDetector.h"
#include "transport/usb_json.h"

using namespace deskmate;

HardwareSerial pi4_serial(2);
C1001Passive mmwave(Serial1);
DrowsyDetector drowsy_detector;

uint32_t last_mmwave_ms = 0;
uint32_t last_environment_ms = 0;
bool mmwave_ready = false;

void setup() {
  Serial.begin(kUsbBaud);
  Serial1.begin(kC1001Baud, SERIAL_8N1, kC1001RxPin, kC1001TxPin);
  pi4_serial.begin(kPi4Baud, SERIAL_8N1, kPi4RxPin, kPi4TxPin);

  Serial.println(F("DESKMATE ESP32 sensor node starting"));
  mmwave_ready = mmwave.begin();
  Serial.println(mmwave_ready ? F("C1001 ready") : F("C1001 initialization failed"));
}

void loop() {
  const uint32_t now_ms = millis();
  if (now_ms - last_mmwave_ms >= kMmwavePeriodMs) {
    last_mmwave_ms = now_ms;
    const MmwaveSample sample = mmwave_ready ? mmwave.read() : MmwaveSample{};
    writeMmwaveJson(Serial, now_ms, sample, drowsy_detector.update(sample, now_ms));
  }
  if (now_ms - last_environment_ms >= kEnvironmentPeriodMs) {
    last_environment_ms = now_ms;
    writeEnvironmentStubJson(Serial, now_ms);
  }
}
