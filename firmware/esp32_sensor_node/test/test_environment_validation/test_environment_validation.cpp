#include <unity.h>

#include <cmath>
#include <cstdint>

#include "sensors/environment/EnvironmentValidation.h"
#include "sensors/environment/EnvironmentPayload.h"

using namespace deskmate;

void test_scd41_co2_validation_ignores_temperature_and_humidity() {
  TEST_ASSERT_TRUE(environment_validation::validateScd41Co2(0.0F));
  TEST_ASSERT_TRUE(environment_validation::validateScd41Co2(40000.0F));
  TEST_ASSERT_FALSE(environment_validation::validateScd41Co2(-1.0F));
  TEST_ASSERT_FALSE(environment_validation::validateScd41Co2(NAN));
}

void test_dht22_accepts_sensor_range_boundaries() {
  TEST_ASSERT_TRUE(
      environment_validation::validateDht22(-40.0F, 0.0F).allValid());
  TEST_ASSERT_TRUE(
      environment_validation::validateDht22(80.0F, 100.0F).allValid());
}

void test_dht22_validity_is_independent_per_channel() {
  const auto invalid_temp =
      environment_validation::validateDht22(NAN, 50.0F);
  TEST_ASSERT_FALSE(invalid_temp.temp_valid);
  TEST_ASSERT_TRUE(invalid_temp.humidity_valid);

  const auto invalid_humidity =
      environment_validation::validateDht22(25.0F, 100.1F);
  TEST_ASSERT_TRUE(invalid_humidity.temp_valid);
  TEST_ASSERT_FALSE(invalid_humidity.humidity_valid);
}

void test_bh1750_rejects_library_errors_and_non_finite_values() {
  TEST_ASSERT_TRUE(environment_validation::validateBh1750(0.0F));
  TEST_ASSERT_TRUE(environment_validation::validateBh1750(54612.5F));
  TEST_ASSERT_FALSE(environment_validation::validateBh1750(-1.0F));
  TEST_ASSERT_FALSE(environment_validation::validateBh1750(INFINITY));
}

void test_freshness_distinguishes_missing_fresh_and_stale() {
  TEST_ASSERT_EQUAL_UINT32(
      UINT32_MAX, environment_validation::sampleAgeMs(100, 0, false));
  TEST_ASSERT_TRUE(environment_validation::isStale(100, 0, false, 15000));
  TEST_ASSERT_FALSE(
      environment_validation::isStale(16000, 1000, true, 15000));
  TEST_ASSERT_TRUE(
      environment_validation::isStale(16001, 1000, true, 15000));
}

void test_age_math_handles_millis_wraparound() {
  TEST_ASSERT_EQUAL_UINT32(
      32, environment_validation::sampleAgeMs(16, UINT32_MAX - 15, true));
}

void test_environment_payload_matches_pi4_decoder_contract() {
  EnvironmentSample sample;
  sample.co2_ppm = 812.0F;
  sample.temp_c = 26.4F;
  sample.humidity_pct = 48.2F;
  sample.lux = 310.0F;
  sample.co2_valid = true;
  sample.temp_valid = true;
  sample.humidity_valid = true;
  sample.lux_valid = true;

  uint8_t payload[kEnvironmentPayloadSize];
  encodeEnvironmentPayload(sample, payload);
  const uint8_t expected[] = {0x2C, 0x03, 0x08, 0x01, 0xE2,
                              0x01, 0x36, 0x01, 0x0F};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, payload, kEnvironmentPayloadSize);
}

void test_environment_payload_keeps_validity_independent() {
  EnvironmentSample sample;
  sample.co2_ppm = 625.0F;
  sample.temp_c = 24.5F;
  sample.humidity_pct = 52.5F;
  sample.lux = 178.0F;
  sample.co2_valid = true;
  sample.temp_valid = false;
  sample.humidity_valid = false;
  sample.lux_valid = true;

  uint8_t payload[kEnvironmentPayloadSize];
  encodeEnvironmentPayload(sample, payload);
  const uint8_t expected[] = {0x71, 0x02, 0x00, 0x00, 0x00,
                              0x00, 0xB2, 0x00, 0x09};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, payload, kEnvironmentPayloadSize);
}

void test_environment_payload_encodes_negative_temperature_as_i16_le() {
  EnvironmentSample sample;
  sample.temp_c = -5.5F;
  sample.temp_valid = true;

  uint8_t payload[kEnvironmentPayloadSize];
  encodeEnvironmentPayload(sample, payload);
  TEST_ASSERT_EQUAL_HEX8(0xC9, payload[2]);
  TEST_ASSERT_EQUAL_HEX8(0xFF, payload[3]);
  TEST_ASSERT_EQUAL_HEX8(kEnvironmentValidTemp, payload[8]);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_scd41_co2_validation_ignores_temperature_and_humidity);
  RUN_TEST(test_dht22_accepts_sensor_range_boundaries);
  RUN_TEST(test_dht22_validity_is_independent_per_channel);
  RUN_TEST(test_bh1750_rejects_library_errors_and_non_finite_values);
  RUN_TEST(test_freshness_distinguishes_missing_fresh_and_stale);
  RUN_TEST(test_age_math_handles_millis_wraparound);
  RUN_TEST(test_environment_payload_matches_pi4_decoder_contract);
  RUN_TEST(test_environment_payload_keeps_validity_independent);
  RUN_TEST(test_environment_payload_encodes_negative_temperature_as_i16_le);
  return UNITY_END();
}
