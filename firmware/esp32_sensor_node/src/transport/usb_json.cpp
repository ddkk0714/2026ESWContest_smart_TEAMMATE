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

void writeEnvironmentStubJson(Stream& output, uint32_t now_ms) {
  output.print(F("{\"t\":\"env\",\"ms\":"));
  output.print(now_ms);
  output.println(F(",\"co2_ppm\":null,\"temp_c\":null,\"humidity_pct\":null,\"lux\":null,"
                   "\"co2_valid\":false,\"temp_valid\":false,\"humidity_valid\":false,"
                   "\"lux_valid\":false}"));
}

}  // namespace deskmate
