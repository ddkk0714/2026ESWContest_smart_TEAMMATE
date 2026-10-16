#pragma once

#include <stdint.h>

#include "preprocess/tof/TofPosture.h"

namespace deskmate {

// ============================================================================
//  UART2 TYPE 0x03 / 0x04 payload — ESP32 → Pi 4 (docs/data-spec.md §13.1)
//
//  Arduino 의존이 없어 호스트 테스트(test/test_tof_payload)로 고정한다.
//  Python 디코더는 hub/deskmate_hub/ingest/uart_frame.py 의 _TOF / _TOF_POSTURE 구조체이고,
//  둘이 맞아야 하는 golden 벡터는 양쪽 테스트에 같은 hex 로 들어 있다.
//
//  TYPE 0x03 tof_feature (26 B, little-endian) — 5 Hz
//    0  present u8
//    1  posture u8          0 unknown · 1 upright · 2 lean_forward · 3 recline · 4 face_down
//                           · 5 chin_rest · 6 drowsy · 7 away  (TofPosture enum 값 그대로)
//    2  confidence_pct u8   posture_confidence × 100
//    3  motion_pct u8       motion_score × 100
//    4  head_delta_mm i16   기준선 대비 머리 거리 변화. 0x7FFF = null
//    6  nod_rate_mhz u16    nod_rate_hz × 1000. 0xFFFF = null
//    8  valid_zones u16
//   10  grid_w u8
//   11  grid_h u8
//   12  coverage_pct u8
//   13  presence_count u16
//   15  centroid_mm u16     전경 거리 중앙값. 0xFFFF = null
//   17  head_row_pct u8     head_row_index × 100. 0xFF = null
//   18  shoulder_tilt_mm i16  0x7FFF = null
//   20  motion_mm_s u16
//   22  change_per_min u8
//   23  baseline_dev_pct u8 baseline_deviation × 100, 255 에서 포화
//   24  flags u8            b0 baseline_ready · b1 drowsy · b2 geometry_valid · b3 센서 A 동작 · b4 센서 B 동작
//   25  nods u8             최근 60 s 끄덕임 수
//
//  TYPE 0x04 tof_posture (4 B) — 1 Hz. 화면·네이티브 요약용 축약본.
//    0 present u8 · 1 posture u8 · 2 confidence_pct u8 · 3 drowsy u8
// ============================================================================

constexpr uint8_t kTofFeaturePayloadSize = 26;
constexpr uint8_t kTofPosturePayloadSize = 4;

constexpr uint8_t kTofFlagBaselineReady = 0x01;
constexpr uint8_t kTofFlagDrowsy = 0x02;
constexpr uint8_t kTofFlagGeometryValid = 0x04;
constexpr uint8_t kTofFlagSensorA = 0x08;
constexpr uint8_t kTofFlagSensorB = 0x10;

constexpr int16_t kTofNullI16 = 0x7FFF;
constexpr uint16_t kTofNullU16 = 0xFFFF;
constexpr uint8_t kTofNullU8 = 0xFF;

// sensor_mask: b0 = 센서 A 동작, b1 = 센서 B 동작 (TofArray::okMask()).
void encodeTofFeaturePayload(const TofFeatures &f, uint8_t sensor_mask,
                             uint8_t out[kTofFeaturePayloadSize]);
void encodeTofPosturePayload(const TofFeatures &f, uint8_t out[kTofPosturePayloadSize]);

}  // namespace deskmate
