#include "usb_json.h"

namespace deskmate {
namespace {

void writeBool(Stream& output, bool value) { output.print(value ? F("true") : F("false")); }

void writeNullable(Stream& output, bool valid, uint16_t value) {
  if (valid) {
    output.print(value);
  } else {
    output.print(F("null"));
  }
}

// uint8_t fields (resp/heart bpm) would otherwise be ambiguous between the uint16_t and float overloads.
void writeNullable(Stream& output, bool valid, uint8_t value) {
  writeNullable(output, valid, static_cast<uint16_t>(value));
}

void writeNullable(Stream& output, bool valid, float value) {
  if (valid) {
    output.print(value, 1);
  } else {
    output.print(F("null"));
  }
}

}  // namespace

void writeMmwaveJson(Stream& output, uint32_t now_ms, const MmwaveSample& sample,
                     DrowsyState drowsy_state) {
  // Keep `{"t":` at byte zero: the PC bridge intentionally ignores every
  // other line, allowing human-readable Serial diagnostics to coexist.
  output.print(F("{\"t\":\"mmwave\",\"ms\":"));
  output.print(now_ms);
  output.print(F(",\"present\":"));
  writeBool(output, sample.present);
  output.print(F(",\"motion_state\":\""));
  output.print(motionStateName(sample.motion_state));
  output.print(F("\",\"motion_level\":"));
  output.print(sample.motion_level);
  output.print(F(",\"distance_cm\":"));
  writeNullable(output, sample.distance_valid, sample.distance_cm);
  output.print(F(",\"resp_bpm\":"));
  writeNullable(output, sample.resp_valid, sample.resp_bpm);
  output.print(F(",\"resp_valid\":"));
  writeBool(output, sample.resp_valid);
  output.print(F(",\"heart_bpm\":"));
  writeNullable(output, sample.heart_valid, sample.heart_bpm);
  output.print(F(",\"heart_valid\":"));
  writeBool(output, sample.heart_valid);
  output.print(F(",\"drowsy_state\":\""));
  output.print(drowsyStateName(drowsy_state));
  output.print(F("\",\"valid\":"));
  writeBool(output, sample.valid);
  output.println(F("}"));
}

void writeEnvironmentJson(Stream& output, uint32_t now_ms, const EnvironmentSample& sample) {
  output.print(F("{\"t\":\"env\",\"ms\":"));
  output.print(now_ms);
  output.print(F(",\"co2_ppm\":"));
  writeNullable(output, sample.co2_valid, static_cast<uint16_t>(sample.co2_ppm));
  output.print(F(",\"temp_c\":"));
  writeNullable(output, sample.temp_valid, sample.temp_c);
  output.print(F(",\"humidity_pct\":"));
  writeNullable(output, sample.humidity_valid, sample.humidity_pct);
  output.print(F(",\"lux\":"));
  writeNullable(output, sample.lux_valid, sample.lux);
  output.print(F(",\"co2_valid\":"));
  writeBool(output, sample.co2_valid);
  output.print(F(",\"temp_valid\":"));
  writeBool(output, sample.temp_valid);
  output.print(F(",\"humidity_valid\":"));
  writeBool(output, sample.humidity_valid);
  output.print(F(",\"lux_valid\":"));
  writeBool(output, sample.lux_valid);
  output.println(F("}"));
}

void writeEnvironmentStubJson(Stream& output, uint32_t now_ms) {
  writeEnvironmentJson(output, now_ms, EnvironmentSample{});
}

}  // namespace deskmate
