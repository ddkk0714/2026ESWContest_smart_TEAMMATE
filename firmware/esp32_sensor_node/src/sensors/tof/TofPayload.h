#pragma once

#include <stdint.h>

#include "preprocess/tof/TofPosture.h"

namespace deskmate {

// ============================================================================
//  UART2 TYPE 0x03 / 0x04 / 0x05 payload — ESP32 → Pi 4 (docs/data-spec.md §13.1)
//
//  Arduino 의존이 없어 호스트 테스트(test/test_tof_payload)로 고정한다.
//  Python 디코더는 hub/deskmate_hub/ingest/uart_frame.py 의 _TOF / _TOF_POSTURE / _TOF_GRID_HEADER
//  구조체이고, 둘이 맞아야 하는 golden 벡터는 양쪽 테스트에 같은 hex 로 들어 있다.
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
//
//  TYPE 0x05 tof_grid — 원본 거리·상태 격자 조각, 5 Hz (2026-10-17: 자세 AI 모델은 Pi 4 에서 돈다.
//  ESP32 는 센서 원본을 그대로 넘긴다). 16×8 int16 + u8 = 384 B 는 프레임 LEN 상한 256 B
//  (kFrameMaxPayload · Pi 4 uart_rx kMaxPayloadLength)를 넘으므로 행 단위로 조각 낸다.
//  한 조각은 최대 64 zone(= 센서 한 개 분량): 16 열이면 4 행, 8 열이면 8 행. 거리는 mm 그대로
//  보낸다 — 학습 데이터(dataset/tof_logger frames.jsonl 의 d·st)와 같은 값이어야 Pi 4 특징이 학습과
//  같아진다. 하위 바이트만 보내는 압축은 2.55 m 에서 잘리고 10 mm 로 뭉개져 쓰지 않는다.
//    0  grid_w u8           전체 격자 폭
//    1  grid_h u8           전체 격자 높이
//    2  row0 u8             이 조각의 첫 행
//    3  rows u8             이 조각의 행 수 (n = rows × grid_w ≤ 64)
//    4  frame_id u8         같은 격자의 조각끼리 같은 값. 격자마다 +1 (wrap)
//    5  sensor_mask u8      b0 센서 A 동작 · b1 센서 B 동작
//    6  part u8             조각 번호 0..parts-1
//    7  parts u8            조각 수
//    8  dist i16 × n        row-major 장면 방향(row 0 = 위). -1 = 타깃 없음
//    8+2n status u8 × n     VL53 target status(5·9 유효, 6·10 대체로 유효). 255 = 타깃 없음
//  = 8 + 3n B, 64 zone 이면 200 B.
// ============================================================================

constexpr uint8_t kTofFeaturePayloadSize = 26;
constexpr uint8_t kTofPosturePayloadSize = 4;

constexpr uint8_t kTofGridHeaderSize = 8;
constexpr uint8_t kTofGridMaxZonesPerPart = 64;
constexpr uint16_t kTofGridMaxPayloadSize = kTofGridHeaderSize + kTofGridMaxZonesPerPart * 3;  // 200
constexpr int16_t kTofGridNoTargetMm = -1;
constexpr uint8_t kTofGridNoTargetStatus = 255;

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

// 조각당 행 수 = max(1, 64 / grid_w). 조각 수 = ceil(grid_h / 조각당 행 수). grid_w 가 0 이면 0.
uint8_t tofGridRowsPerPart(uint8_t grid_w);
uint8_t tofGridPartCount(uint8_t grid_w, uint8_t grid_h);

// part 번째 조각을 out 에 쓰고 payload 길이를 돌려준다. 격자가 비었거나 part 가 범위 밖이면 0.
// dist/status 는 TofArray::distance()/status() 와 같은 row-major 장면 방향 배열(grid_w × grid_h).
uint16_t encodeTofGridPart(const int16_t *dist, const uint8_t *status, uint8_t grid_w, uint8_t grid_h,
                           uint8_t part, uint8_t frame_id, uint8_t sensor_mask,
                           uint8_t out[kTofGridMaxPayloadSize]);

}  // namespace deskmate
