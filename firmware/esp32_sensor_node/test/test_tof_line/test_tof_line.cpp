// 수집 모드 USB 줄 형식을 고정한다. 아래 kGoldenLine 은 PC 수집기 테스트
// (dataset/tof_logger/tests/test_protocol_line.py)에 같은 문자열로 들어 있다 — 바꾸면 둘 다 바꾼다.
#include <unity.h>

#include <cstring>

#include "tof_logger/TofLine.h"

using namespace deskmate;

void setUp() {}
void tearDown() {}

namespace {

const char *kGoldenLine =
    "{\"t\":\"tof\",\"seq\":7,\"ms\":1234,\"w\":2,\"h\":2,\"d\":[612,-1,1500,700],"
    "\"st\":[5,255,9,6],\"rule\":{\"present\":true,\"posture\":\"upright\",\"conf\":0.83,"
    "\"baseline\":true,\"head_mm\":612,\"torso_mm\":null,\"drop_mm\":4,\"chin\":0.00,"
    "\"motion\":0.05,\"nods\":1}}\n";

const int16_t kD[4] = {612, -1, 1500, 700};
const uint8_t kSt[4] = {5, 255, 9, 6};

TofFeatures goldenFeatures() {
  TofFeatures f;
  f.present = true;
  f.posture = TofPosture::Upright;
  f.posture_confidence = 0.834f;
  f.baseline_ready = true;
  f.geometry_valid = true;
  f.head_depth_mm = 612.4f;
  f.torso_valid = false;
  f.torso_depth_mm = 999.0f;  // torso_valid 가 false 라 null 로 나가야 한다
  f.head_drop_mm = 3.6f;
  f.chin_arm_ratio = 0.0f;
  f.motion_score = 0.05f;
  f.nods_in_window = 1;
  return f;
}

}  // namespace

void test_tof_line_matches_golden() {
  char buf[kTofLineMax];
  const size_t n = formatTofLine(buf, sizeof(buf), 7, 1234, 2, 2, kD, kSt, goldenFeatures());
  TEST_ASSERT_EQUAL_UINT32(strlen(kGoldenLine), n);
  TEST_ASSERT_EQUAL_STRING(kGoldenLine, buf);
}

void test_tof_line_starts_with_t_key() {
  char buf[kTofLineMax];
  formatTofLine(buf, sizeof(buf), 0, 0, 2, 2, kD, kSt, TofFeatures());
  TEST_ASSERT_EQUAL_INT(0, strncmp(buf, "{\"t\":", 5));
}

void test_tof_line_8x8_fits_buffer() {
  int16_t d[64];
  uint8_t st[64];
  for (int i = 0; i < 64; ++i) {
    d[i] = -1;  // 길이를 늘리려고 최악값 섞기
    st[i] = 255;
    if (i % 2) d[i] = 3999;
  }
  TofFeatures f = goldenFeatures();
  f.posture = TofPosture::LeanForward;
  f.torso_valid = true;
  f.torso_depth_mm = 12345.0f;
  char buf[kTofLineMax];
  const size_t n = formatTofLine(buf, sizeof(buf), 4294967295u, 4294967295u, 8, 8, d, st, f);
  TEST_ASSERT_GREATER_THAN_UINT32(0, n);
  TEST_ASSERT_EQUAL_CHAR('\n', buf[n - 1]);
}

void test_tof_line_reports_overflow_as_zero() {
  char buf[40];
  TEST_ASSERT_EQUAL_UINT32(0, formatTofLine(buf, sizeof(buf), 7, 1234, 2, 2, kD, kSt,
                                            goldenFeatures()));
}

void test_remap_flips_rows_and_cols() {
  // 2×3 (w=3, h=2)
  const int16_t d[6] = {1, 2, 3, 4, 5, 6};
  const uint8_t st[6] = {11, 12, 13, 14, 15, 16};
  int16_t od[6];
  uint8_t ost[6];

  remapZones(d, st, od, ost, 3, 2, false, false);
  for (int i = 0; i < 6; ++i) TEST_ASSERT_EQUAL_INT16(d[i], od[i]);

  remapZones(d, st, od, ost, 3, 2, false, true);
  const int16_t cols[6] = {3, 2, 1, 6, 5, 4};
  for (int i = 0; i < 6; ++i) TEST_ASSERT_EQUAL_INT16(cols[i], od[i]);
  TEST_ASSERT_EQUAL_UINT8(13, ost[0]);

  remapZones(d, st, od, ost, 3, 2, true, false);
  const int16_t rows[6] = {4, 5, 6, 1, 2, 3};
  for (int i = 0; i < 6; ++i) TEST_ASSERT_EQUAL_INT16(rows[i], od[i]);

  remapZones(d, st, od, ost, 3, 2, true, true);
  const int16_t both[6] = {6, 5, 4, 3, 2, 1};
  for (int i = 0; i < 6; ++i) TEST_ASSERT_EQUAL_INT16(both[i], od[i]);
}

void test_hello_and_error_lines() {
  char buf[kTofLineMax];
  const TofLoggerInfo info{"VL53L8CX", 8, 8, 15, false, true, "closest", 0x0110};
  formatHelloLine(buf, sizeof(buf), 5, info);
  TEST_ASSERT_EQUAL_STRING(
      "{\"t\":\"tof_hello\",\"ms\":5,\"sensor\":\"VL53L8CX\",\"w\":8,\"h\":8,\"hz\":15,"
      "\"flip_rows\":false,\"flip_cols\":true,\"target_order\":\"closest\",\"fw\":272}\n",
      buf);
  formatErrorLine(buf, sizeof(buf), 9, "init", 66);
  TEST_ASSERT_EQUAL_STRING("{\"t\":\"tof_err\",\"ms\":9,\"where\":\"init\",\"code\":66}\n", buf);
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_tof_line_matches_golden);
  RUN_TEST(test_tof_line_starts_with_t_key);
  RUN_TEST(test_tof_line_8x8_fits_buffer);
  RUN_TEST(test_tof_line_reports_overflow_as_zero);
  RUN_TEST(test_remap_flips_rows_and_cols);
  RUN_TEST(test_hello_and_error_lines);
  return UNITY_END();
}
