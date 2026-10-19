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
//  TYPE 0x05 tof_grid — 센서 한 개의 원본 8×8 거리·상태 프레임, 5 Hz (2026-10-17: 자세 AI 모델은 Pi 4 에서
//  돈다. ESP32 는 센서 원본을 그대로 넘긴다).
//
//  표준(10-17): ToF 데이터의 단위는 "센서 하나의 8×8" 이다. 장치의 두 VL53L8CX 는 바깥으로 8° 씩만 벌어져
//  있어(팀 CAD) 45° 시야의 대부분이 겹친다 — 둘을 16×8 로 이어 붙인 격자는 한 장면이 아니라 거의 같은 장면을
//  두 번 본 것이다. 그래서 0x05 는 센서마다 한 프레임씩(A, B) 같은 frame_id·TS 로 보내고, Pi 4 모델은 센서별로
//  추론한 뒤 융합한다(log-softmax 합). TofArray 가 만드는 16×8 스티칭은 규칙 추정기(0x03)·USB 표시용 내부 근사다.
//  크기로도 센서당 8×8 int16+u8 = 192 B 가 LEN 상한 256 B(kFrameMaxPayload · Pi 4 uart_rx kMaxPayloadLength)
//  안에 딱 들어간다(16×8 한 장 384 B 는 넘는다). 거리는 mm 그대로 — 학습 데이터(dataset/tof_logger 의 d·st)와
//  같은 값이어야 Pi 4 특징이 학습과 같아진다. 하위 바이트 압축은 2.55 m 절단·10 mm 양자화라 쓰지 않는다.
//    0  grid_w u8           이 센서 격자 폭 (8)
//    1  grid_h u8           이 센서 격자 높이 (8)
//    2  sensor u8           물리 번호: 0 = A(장면의 왼쪽/위), 1 = B. sensor_mask 의 해당 비트가 서 있어야 한다
//    3  sensors u8          이 frame_id 로 보내는 센서 수(살아 있는 센서 수). B 만 살면 sensor 1 · sensors 1
//    4  frame_id u8         같은 표본의 센서 프레임끼리 같은 값. 표본마다 +1 (wrap)
//    5  sensor_mask u8      b0 센서 A 동작 · b1 센서 B 동작
//    6  flags u8            적용한 방향 변환: b0 flip_rows · b1 flip_cols · b2 stack_rows · b3 swap (DESKMATE_TOF_*)
//    7  reserved u8         0
//    8  dist i16 × n        센서 자체 row-major 장면 방향(row 0 = 위, col 0 = 센서가 보는 왼쪽). -1 = 타깃 없음
//    8+2n status u8 × n     VL53 target status(5·9 유효, 6·10 대체로 유효). 255 = 타깃 없음
//  n = grid_w × grid_h ≤ 64 → 8 + 3n B, 8×8 이면 200 B.
// ============================================================================

constexpr uint8_t kTofFeaturePayloadSize = 26;
constexpr uint8_t kTofPosturePayloadSize = 4;

constexpr uint8_t kTofGridHeaderSize = 8;
constexpr uint8_t kTofGridMaxZones = 64;  // 센서 한 개 8×8
constexpr uint16_t kTofGridMaxPayloadSize = kTofGridHeaderSize + kTofGridMaxZones * 3;  // 200
constexpr int16_t kTofGridNoTargetMm = -1;
constexpr uint8_t kTofGridNoTargetStatus = 255;
constexpr uint8_t kTofGridFlagFlipRows = 0x01;
constexpr uint8_t kTofGridFlagFlipCols = 0x02;
constexpr uint8_t kTofGridFlagStackRows = 0x04;
constexpr uint8_t kTofGridFlagSwap = 0x08;

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

// 센서 한 개의 프레임을 out 에 쓰고 payload 길이를 돌려준다. 격자가 비었거나 64 zone 을 넘거나 sensor_mask 에 그 센서가 없으면 0.
// dist/status 는 TofArray::sensorDistance(i)/sensorStatus(i) 와 같은 row-major 장면 방향 배열(grid_w × grid_h).
uint16_t encodeTofSensorFrame(const int16_t *dist, const uint8_t *status, uint8_t grid_w, uint8_t grid_h,
                              uint8_t sensor, uint8_t sensors, uint8_t frame_id, uint8_t sensor_mask,
                              uint8_t flags, uint8_t out[kTofGridMaxPayloadSize]);

}  // namespace deskmate
