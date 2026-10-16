// UART TYPE 0x03/0x04 payload 를 고정한다. kGoldenFeatureHex 는 hub 테스트
// (hub/tests/test_uart_frame.py::test_tof_feature_vector)에 같은 hex 로 들어 있다 — 바꾸면 둘 다 바꾼다.
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

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_feature_payload_matches_golden);
  RUN_TEST(test_posture_payload_matches_golden);
  RUN_TEST(test_invalid_fields_become_null_sentinels);
  RUN_TEST(test_percentages_saturate);
  return UNITY_END();
}
