// UART TYPE 0x03/0x04/0x05 payload 를 고정한다. kGoldenFeatureHex·kGoldenGridPart0/1Hex 는 hub 테스트
// (hub/tests/test_uart_frame.py::test_tof_feature_vector / test_tof_grid_golden_parts)에 같은 hex 로
// 들어 있다 — 바꾸면 둘 다 바꾼다.
#include <unity.h>

#include <cstdio>
#include <cstring>

#include "sensors/tof/TofPayload.h"

using namespace deskmate;

void setUp() {}
void tearDown() {}

namespace {

const char *kGoldenFeatureHex = "01015305f4ff3200780010082a360064021908000f0002251d01";
const char *kGoldenPostureHex = "01015300";

// 0x05 골든 격자 16×8: zone i 는 i%11==0 이면 타깃 없음(-1/255), 아니면 dist 450+3i, status (5,9,6,0)[i%4].
// frame_id 0x2A, sensor_mask 0x03. 조각 2개(행 0~3, 행 4~7), 각 200 B.
const char *kGoldenGridPart0Hex =
    "100800042a030002ffffc501c801cb01ce01d101d401d701da01dd01e001ffffe601e901ec01ef01f201f501f801fb01fe010102"
    "ffff07020a020d0210021302160219021c021f022202ffff28022b022e023102340237023a023d0240024302ffff49024c024f02"
    "5202550258025b025e0261026402ffff6a026d0270027302760279027c027f02ff09060005090600050906ff0509060005090600"
    "0509ff00050906000509060005ff06000509060005090600ff09060005090600050906ff0509060005090600";
const char *kGoldenGridPart1Hex =
    "100804042a03010282028502ffff8b028e029102940297029a029d02a002a302a602ffffac02af02b202b502b802bb02be02c102"
    "c402c702ffffcd02d002d302d602d902dc02df02e202e502e802ffffee02f102f402f702fa02fd020003030306030903ffff0f03"
    "1203150318031b031e032103240327032a03ffff30033303360339033c033f030509ff00050906000509060005ff060005090600"
    "05090600ff09060005090600050906ff05090600050906000509ff00050906000509060005ff060005090600";

void goldenGrid(int16_t *dist, uint8_t *st, uint16_t n) {
  static const uint8_t kStatus[4] = {5, 9, 6, 0};
  for (uint16_t i = 0; i < n; ++i) {
    if (i % 11 == 0) {
      dist[i] = kTofGridNoTargetMm;
      st[i] = kTofGridNoTargetStatus;
    } else {
      dist[i] = static_cast<int16_t>(450 + 3 * i);
      st[i] = kStatus[i % 4];
    }
  }
}

TofFeatures goldenFeatures() {
  TofFeatures f;
  f.present = true;
  f.posture = TofPosture::Upright;
  f.posture_confidence = 0.834f;
  f.motion_score = 0.05f;
  f.head_delta_valid = true;
  f.head_delta_mm = -12.3f;
  f.nod_rate_valid = true;
  f.nod_rate_hz = 0.05f;
  f.valid_zones = 120;
  f.grid_width = 16;
  f.grid_height = 8;
  f.coverage_ratio = 0.42f;
  f.presence_count = 54;
  f.geometry_valid = true;
  f.centroid_depth_mm = 612.4f;
  f.head_row_index = 0.25f;
  f.shoulder_tilt_valid = true;
  f.shoulder_tilt_mm = 8.2f;
  f.motion_indicator_mm_s = 15.4f;
  f.posture_change_rate_per_min = 2.0f;
  f.baseline_deviation = 0.37f;
  f.baseline_ready = true;
  f.drowsy = false;
  f.nods_in_window = 1;
  return f;
}

void toHex(const uint8_t *buf, size_t n, char *out) {
  for (size_t i = 0; i < n; ++i) std::snprintf(out + i * 2, 3, "%02x", buf[i]);
}

}  // namespace

void test_feature_payload_matches_golden() {
  uint8_t buf[kTofFeaturePayloadSize];
  encodeTofFeaturePayload(goldenFeatures(), 0x03, buf);
  char hex[kTofFeaturePayloadSize * 2 + 1];
  toHex(buf, sizeof(buf), hex);
  TEST_ASSERT_EQUAL_STRING(kGoldenFeatureHex, hex);
}

void test_posture_payload_matches_golden() {
  uint8_t buf[kTofPosturePayloadSize];
  encodeTofPosturePayload(goldenFeatures(), buf);
  char hex[kTofPosturePayloadSize * 2 + 1];
  toHex(buf, sizeof(buf), hex);
  TEST_ASSERT_EQUAL_STRING(kGoldenPostureHex, hex);
}

void test_invalid_fields_become_null_sentinels() {
  TofFeatures f;  // 기본값: 아무것도 유효하지 않음, away
  uint8_t buf[kTofFeaturePayloadSize];
  encodeTofFeaturePayload(f, 0x00, buf);
  TEST_ASSERT_EQUAL_UINT8(0, buf[0]);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(TofPosture::Away), buf[1]);
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[4]);
  TEST_ASSERT_EQUAL_UINT8(0x7F, buf[5]);  // head_delta null
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[6]);
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[7]);  // nod null
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[15]);
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[16]);  // centroid null
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[17]);  // head_row null
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[18]);
  TEST_ASSERT_EQUAL_UINT8(0x7F, buf[19]);  // shoulder null
  TEST_ASSERT_EQUAL_UINT8(0, buf[24]);     // 센서 없음, 기준선 없음
}

void test_percentages_saturate() {
  TofFeatures f;
  f.posture_confidence = 7.0f;
  f.motion_score = -1.0f;
  f.baseline_deviation = 9.9f;
  f.posture_change_rate_per_min = 999.0f;
  f.motion_indicator_mm_s = 1e9f;
  uint8_t buf[kTofFeaturePayloadSize];
  encodeTofFeaturePayload(f, 0x02, buf);
  TEST_ASSERT_EQUAL_UINT8(100, buf[2]);
  TEST_ASSERT_EQUAL_UINT8(0, buf[3]);
  TEST_ASSERT_EQUAL_UINT8(255, buf[23]);
  TEST_ASSERT_EQUAL_UINT8(255, buf[22]);
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[20]);
  TEST_ASSERT_EQUAL_UINT8(0xFF, buf[21]);
  TEST_ASSERT_EQUAL_UINT8(kTofFlagSensorB, buf[24]);
}

void test_grid_parts_match_golden() {
  int16_t dist[128];
  uint8_t st[128];
  goldenGrid(dist, st, 128);
  TEST_ASSERT_EQUAL_UINT8(4, tofGridRowsPerPart(16));
  TEST_ASSERT_EQUAL_UINT8(2, tofGridPartCount(16, 8));
  uint8_t buf[kTofGridMaxPayloadSize];
  char hex[kTofGridMaxPayloadSize * 2 + 1];
  uint16_t len = encodeTofGridPart(dist, st, 16, 8, 0, 0x2A, 0x03, buf);
  TEST_ASSERT_EQUAL_UINT16(200, len);
  toHex(buf, len, hex);
  TEST_ASSERT_EQUAL_STRING(kGoldenGridPart0Hex, hex);
  len = encodeTofGridPart(dist, st, 16, 8, 1, 0x2A, 0x03, buf);
  TEST_ASSERT_EQUAL_UINT16(200, len);
  toHex(buf, len, hex);
  TEST_ASSERT_EQUAL_STRING(kGoldenGridPart1Hex, hex);
  TEST_ASSERT_EQUAL_UINT16(0, encodeTofGridPart(dist, st, 16, 8, 2, 0x2A, 0x03, buf));  // 범위 밖
}

void test_grid_part_layout_for_other_grids() {
  int16_t dist[128];
  uint8_t st[128];
  goldenGrid(dist, st, 128);
  uint8_t buf[kTofGridMaxPayloadSize];
  // 한쪽 센서만: 8×8 → 조각 1개, 64 zone
  TEST_ASSERT_EQUAL_UINT8(1, tofGridPartCount(8, 8));
  uint16_t len = encodeTofGridPart(dist, st, 8, 8, 0, 7, 0x02, buf);
  TEST_ASSERT_EQUAL_UINT16(200, len);
  TEST_ASSERT_EQUAL_UINT8(8, buf[0]);
  TEST_ASSERT_EQUAL_UINT8(8, buf[1]);
  TEST_ASSERT_EQUAL_UINT8(0, buf[2]);
  TEST_ASSERT_EQUAL_UINT8(8, buf[3]);
  TEST_ASSERT_EQUAL_UINT8(7, buf[4]);
  TEST_ASSERT_EQUAL_UINT8(0x02, buf[5]);
  TEST_ASSERT_EQUAL_UINT8(0, buf[6]);
  TEST_ASSERT_EQUAL_UINT8(1, buf[7]);
  // 세로 배치 8×16 → 8 행씩 조각 2개. 두 번째 조각은 row0 8, zone 64 부터
  TEST_ASSERT_EQUAL_UINT8(2, tofGridPartCount(8, 16));
  len = encodeTofGridPart(dist, st, 8, 16, 1, 7, 0x03, buf);
  TEST_ASSERT_EQUAL_UINT16(200, len);
  TEST_ASSERT_EQUAL_UINT8(8, buf[2]);
  TEST_ASSERT_EQUAL_UINT8(8, buf[3]);
  TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(dist[64] & 0xFF), buf[8]);
  TEST_ASSERT_EQUAL_UINT8(st[64], buf[8 + 2 * 64]);
  // 빈 격자·너무 넓은 격자는 0
  TEST_ASSERT_EQUAL_UINT8(0, tofGridPartCount(0, 8));
  TEST_ASSERT_EQUAL_UINT8(0, tofGridPartCount(65, 1));
  TEST_ASSERT_EQUAL_UINT16(0, encodeTofGridPart(dist, st, 0, 0, 0, 0, 0, buf));
  // 모든 조각이 LEN 상한(256 B) 안에 든다
  TEST_ASSERT_TRUE(kTofGridMaxPayloadSize <= 256);
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_feature_payload_matches_golden);
  RUN_TEST(test_posture_payload_matches_golden);
  RUN_TEST(test_invalid_fields_become_null_sentinels);
  RUN_TEST(test_percentages_saturate);
  RUN_TEST(test_grid_parts_match_golden);
  RUN_TEST(test_grid_part_layout_for_other_grids);
  return UNITY_END();
}
