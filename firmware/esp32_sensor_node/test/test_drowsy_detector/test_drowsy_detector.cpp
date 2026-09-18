// 졸음 판정기의 계약을 호스트에서 고정한다.
//
// 임계값은 76EHwan/ESP32-mmWave 의 실측 로그로 맞춘 것이라 (DrowsyDetector.h 의
// 주석 참고) 숫자를 무심코 건드리면 현장에서만 티가 난다. 여기서는 판정 구조
// 자체 — 락온 디바운스, 증거 개수에 따른 확정 시간, 각성 복귀 — 를 붙잡는다.
//
// 보드 UART 가 필요 없도록 update(C1001Passive&) 대신 개별 이벤트를 직접 넣는다.
#include <unity.h>

#include <cstdint>

#include "sensors/c1001/DrowsyDetector.h"

using namespace deskmate;

namespace {

/// 사람이 앉아 있고 락온이 선 상태까지 만들어 준다. 거리는 디바운스 때문에
/// 연속 3 샘플이 필요하다.
void seatPerson(DrowsyDetector& d, uint32_t t0 = 0) {
  d.begin(t0);
  d.onPresence(1);
  for (uint8_t i = 0; i < kLockDebounceN; i++) d.onDistance(50);
}

/// [from, to) 를 1초 간격으로 돌면서 조용한 체동만 넣는다.
/// level 은 kMoveQuietSpike 아래여야 "정지"가 유지된다.
void quietSeconds(DrowsyDetector& d, uint32_t from, uint32_t to, uint16_t level = 5) {
  for (uint32_t t = from; t < to; t += 1000) {
    d.onBodyMove(level, t);
    d.tick(t);
  }
}

/// 호흡이 정상으로 잡히는 상태. 이걸 안 넣으면 respRate 0 이 그대로 증거로
/// 잡혀(엎드림) 확정 시간이 3분에서 1분으로 줄어든다.
void breathingNormally(DrowsyDetector& d) { d.onRespiration(16, 1); }

}  // namespace

void test_no_person_until_presence_is_reported() {
  DrowsyDetector d;
  d.begin(0);
  d.tick(1000);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kNoPerson), static_cast<int>(d.state()));
}

// 가려지거나 멀어지면 체동이 1 근처로 죽는데, 그걸 "미동도 없다"로 읽으면
// 가려진 내내 졸음이 뜬다. 락온이 없으면 아예 판정하지 않는다.
void test_presence_without_lock_reports_nolock() {
  DrowsyDetector d;
  d.begin(0);
  d.onPresence(1);
  for (uint8_t i = 0; i < kLockDebounceN; i++) d.onDistance(11);   // 노트북에 가림
  d.tick(1000);
  TEST_ASSERT_FALSE(d.locked());
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kNoLock), static_cast<int>(d.state()));
}

// 거리는 2초마다 갱신되는데 가끔 한 샘플만 튄다. 그 한 번에 락온이 끊기면
// 카운터가 매번 0으로 죽어 확정이 영영 안 선다.
void test_single_distance_outlier_does_not_break_lock() {
  DrowsyDetector d;
  seatPerson(d);
  TEST_ASSERT_TRUE(d.locked());

  d.onDistance(11);           // 한 샘플만 범위 밖
  TEST_ASSERT_TRUE(d.locked());

  d.onDistance(11);
  d.onDistance(11);           // 연속 3회 — 이제는 끊는다
  TEST_ASSERT_FALSE(d.locked());
}

// 호흡·심박이 한 번도 안 잡힌 동안의 0 값을 증거로 쓰면 안 된다.
void test_warmup_until_first_valid_heart_rate() {
  DrowsyDetector d;
  seatPerson(d);
  d.tick(1000);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kWarmup), static_cast<int>(d.state()));

  d.onHeartRate(72, 2000);
  d.tick(2000);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kAwake), static_cast<int>(d.state()));
}

void test_warmup_expires_even_without_heart_rate() {
  DrowsyDetector d;
  seatPerson(d);
  d.tick(kDrowsyWarmupMs + 1);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kAwake), static_cast<int>(d.state()));
}

// 증거가 없으면 체동만으로 3분을 채워야 한다. 집중해서 가만히 화면을 보는 것과
// 조는 것이 체동만으로는 구분되지 않기 때문이다.
void test_stillness_alone_needs_the_long_hold() {
  DrowsyDetector d;
  seatPerson(d);
  breathingNormally(d);
  d.onHeartRate(72, 100);
  TEST_ASSERT_EQUAL_UINT8(0, d.evidence());
  TEST_ASSERT_EQUAL_UINT32(kHoldNoEvidenceMs, d.needMs());

  // 스파이크 유예(5초)가 지나야 "정지"가 서고 거기서부터 카운터가 돈다.
  quietSeconds(d, 0, kStillAfterSpikeMs + kHoldOneMs + 2000);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kAwake), static_cast<int>(d.state()));

  quietSeconds(d, kStillAfterSpikeMs + kHoldOneMs + 2000,
               kStillAfterSpikeMs + kHoldNoEvidenceMs + 2000);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kDrowsy), static_cast<int>(d.state()));
}

// 사람은 있고 거리도 정상인데 호흡이 안 잡히면 가슴이 가려진 자세다.
// 증거 하나가 서면 확정 시간이 1분으로 줄어든다.
void test_lost_respiration_is_evidence_and_shortens_the_hold() {
  DrowsyDetector d;
  seatPerson(d);
  d.onHeartRate(72, 100);
  d.onRespiration(0, kRespStateNoBreath);

  quietSeconds(d, 0, kStillAfterSpikeMs + kHoldOneMs + 2000);
  TEST_ASSERT_TRUE(d.respLost());
  TEST_ASSERT_EQUAL_UINT8(1, d.evidence());
  TEST_ASSERT_EQUAL_UINT32(kHoldOneMs, d.needMs());
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kDrowsy), static_cast<int>(d.state()));
}

// 락온이 없으면 호흡 소실도 증거가 아니다 — 센서가 가려져도 똑같이 끊긴다.
void test_lost_respiration_is_not_evidence_without_lock() {
  DrowsyDetector d;
  d.begin(0);
  d.onPresence(1);
  for (uint8_t i = 0; i < kLockDebounceN; i++) d.onDistance(11);
  d.onRespiration(0, kRespStateNoBreath);
  d.tick(1000);
  TEST_ASSERT_FALSE(d.respLost());
  TEST_ASSERT_EQUAL_UINT8(0, d.evidence());
}

// 자리에서 일어나는 수준의 체동이면 각성 복귀(10초)를 기다리지 않는다.
void test_large_body_move_clears_drowsy_immediately() {
  DrowsyDetector d;
  seatPerson(d);
  d.onHeartRate(72, 100);
  d.onRespiration(0, kRespStateNoBreath);
  quietSeconds(d, 0, kStillAfterSpikeMs + kHoldOneMs + 2000);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kDrowsy), static_cast<int>(d.state()));

  const uint32_t t = kStillAfterSpikeMs + kHoldOneMs + 2000;
  d.onBodyMove(kWakeSpike + 10, t);
  d.tick(t);
  TEST_ASSERT_EQUAL(static_cast<int>(DrowsyState::kAwake), static_cast<int>(d.state()));
}

// 심박 중앙값은 표본이 모이기 전에는 내지 않는다. 순간값은 3bpm씩 계단식으로
// 40bpm 을 왕복하므로 쓸 수 없다.
void test_heart_median_needs_a_minimum_sample_count() {
  DrowsyDetector d;
  seatPerson(d);
  uint32_t t = 0;
  for (uint8_t i = 0; i < kHrWinMinN - 1; i++) {
    t += 3000;
    d.onHeartRate(72, t);
  }
  TEST_ASSERT_EQUAL_UINT8(0, d.hrMedian(t));

  t += 3000;
  d.onHeartRate(72, t);
  TEST_ASSERT_EQUAL_UINT8(72, d.hrMedian(t));
}

// 기준선은 표본 20개가 찰 때까지 세우지 않는다. 한 번 잡히면 τ=600초로 굳어
// 이후의 drop 이 전부 그 값 기준으로 계산되기 때문이다.
void test_heart_baseline_waits_for_enough_clean_samples() {
  DrowsyDetector d;
  seatPerson(d);
  uint32_t t = 0;
  for (uint8_t i = 0; i < kHrBaseMinN - 1; i++) {
    t += 3000;
    d.onHeartRate(72, t);
  }
  TEST_ASSERT_EQUAL_FLOAT(0.0F, d.hrBase());

  t += 3000;
  d.onHeartRate(72, t);
  TEST_ASSERT_EQUAL_FLOAT(72.0F, d.hrBase());
  TEST_ASSERT_EQUAL_FLOAT(0.0F, d.hrDrop(t));
}

// 체동 스파이크 뒤 60초는 심박을 통째로 버린다. 추정기가 흔들린 채로 남기 때문.
void test_body_move_spike_blanks_heart_rate_samples() {
  DrowsyDetector d;
  seatPerson(d);
  d.onBodyMove(kMoveSpike + 10, 1000);
  TEST_ASSERT_TRUE(d.hrBlanking(2000));

  d.onHeartRate(72, 2000);
  TEST_ASSERT_EQUAL_UINT8(0, d.hrSamples(2000));      // 버려졌다

  TEST_ASSERT_FALSE(d.hrBlanking(1000 + kHrBlankMs + 1));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_no_person_until_presence_is_reported);
  RUN_TEST(test_presence_without_lock_reports_nolock);
  RUN_TEST(test_single_distance_outlier_does_not_break_lock);
  RUN_TEST(test_warmup_until_first_valid_heart_rate);
  RUN_TEST(test_warmup_expires_even_without_heart_rate);
  RUN_TEST(test_stillness_alone_needs_the_long_hold);
  RUN_TEST(test_lost_respiration_is_evidence_and_shortens_the_hold);
  RUN_TEST(test_lost_respiration_is_not_evidence_without_lock);
  RUN_TEST(test_large_body_move_clears_drowsy_immediately);
  RUN_TEST(test_heart_median_needs_a_minimum_sample_count);
  RUN_TEST(test_heart_baseline_waits_for_enough_clean_samples);
  RUN_TEST(test_body_move_spike_blanks_heart_rate_samples);
  return UNITY_END();
}
