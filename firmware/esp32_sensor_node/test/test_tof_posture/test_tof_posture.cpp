// ToF 자세 추정기의 판정 구조를 합성 장면으로 고정한다.
//
// 장면: 뒤쪽 벽(1600 mm) 앞에 머리(원)와 상체(직사각형)로 된 사람을 그리고,
// zone 중심이 어느 도형에 들어가는지로 거리 격자를 만든다. 15 Hz, ±5 mm 잡음.
// 실측이 아니므로 임계값의 "정답"을 증명하지 않는다. 대신 배경 분리·기준선·
// 분류·디바운스·노딩 집계와 격자 크기 무관성이라는 구조를 붙잡는다.
#include <unity.h>

#include <cstdint>
#include <memory>
#include <vector>

#include "preprocess/tof/TofPosture.h"

using namespace deskmate;

void setUp() {}
void tearDown() {}

namespace {

constexpr uint32_t kFrameMs = 67;  // ≈ 15 Hz

struct Person {
  float head_top = 0.15f;   // 머리 꼭대기, 격자 높이 대비 (0 = 맨 위)
  float head_r = 0.14f;     // 머리 반지름, 정규화 좌표
  float shift = 0.0f;       // 좌우 이동, 격자 너비 대비
  int head_mm = 650;
  int torso_mm = 700;
  int tilt_mm = 0;          // 상체 오른쪽 절반은 +tilt/2, 왼쪽 절반은 -tilt/2
};

struct Scene {
  bool person = false;
  Person p;
  int bg_mm = 1600;
  bool desk_edge = false;   // 맨 아래 행에 60 mm 짜리 책상 모서리
  int chair_mm = 0;         // > 0 이면 아래쪽 가운데에 사람 거리 안의 정지 물체
};

class Sim {
 public:
  Sim(uint8_t w, uint8_t h, const TofPostureConfig &cfg = TofPostureConfig())
      : w_(w), h_(h), dist_(w * h), status_(w * h), est_(new TofPostureEstimator(cfg)) {}

  // ms 동안 장면을 흘린다. mutate 로 프레임마다 결측·튐을 넣을 수 있다.
  template <typename F>
  void run(const Scene &s, uint32_t ms, F mutate) {
    for (uint32_t elapsed = 0; elapsed < ms; elapsed += kFrameMs) {
      render(s);
      mutate(frame_++, dist_.data(), status_.data());
      push();
    }
  }
  void run(const Scene &s, uint32_t ms) {
    run(s, ms, [](uint32_t, int16_t *, uint8_t *) {});
  }

  // 머리 거리를 시간에 따라 바꿔 가며 흘린다(노딩).
  template <typename F>
  void runHead(Scene s, uint32_t ms, F head_offset_mm) {
    const int base = s.p.head_mm;
    for (uint32_t elapsed = 0; elapsed < ms; elapsed += kFrameMs) {
      s.p.head_mm = base + head_offset_mm(elapsed);
      render(s);
      push();
      frame_++;
    }
  }

  const TofFeatures &f() const { return est_->features(); }
  TofPostureEstimator &est() { return *est_; }
  uint8_t width() const { return w_; }
  uint8_t height() const { return h_; }
  std::vector<int16_t> &dist() { return dist_; }
  std::vector<uint8_t> &status() { return status_; }

  void render(const Scene &s) {
    for (uint8_t r = 0; r < h_; r++) {
      for (uint8_t c = 0; c < w_; c++) {
        const float u = (c + 0.5f) / w_;
        const float v = (r + 0.5f) / h_;
        int d = s.bg_mm;
        if (s.chair_mm > 0 && u > 0.3f && u < 0.7f && v > 0.6f) d = s.chair_mm;
        if (s.person) {
          const Person &p = s.p;
          const float cu = 0.5f + p.shift;
          const float cv = p.head_top + p.head_r;
          const float du = u - cu, dv = v - cv;
          const float shoulder = p.head_top + 2.0f * p.head_r + 0.02f;
          if (du * du + dv * dv <= p.head_r * p.head_r) {
            d = p.head_mm;
          } else if (v >= shoulder && u >= cu - 0.3f && u <= cu + 0.3f) {
            d = p.torso_mm + (u >= cu ? p.tilt_mm / 2 : -p.tilt_mm / 2);
          }
        }
        if (s.desk_edge && r == h_ - 1) d = 60;
        const uint16_t z = (uint16_t)r * w_ + c;
        dist_[z] = (int16_t)(d + noise());
        status_[z] = 5;
      }
    }
  }

  void push() {
    TofFrameView v;
    v.width = w_;
    v.height = h_;
    v.distance_mm = dist_.data();
    v.status = status_.data();
    TEST_ASSERT_TRUE(est_->update(v, t_));
    t_ += kFrameMs;
  }

  uint32_t lcg() {
    seed_ = seed_ * 1664525u + 1013904223u;
    return seed_ >> 8;
  }

 private:
  int noise() { return (int)(lcg() % 11) - 5; }

  uint8_t w_, h_;
  std::vector<int16_t> dist_;
  std::vector<uint8_t> status_;
  std::unique_ptr<TofPostureEstimator> est_;
  uint32_t t_ = 0;
  uint32_t frame_ = 0;
  uint32_t seed_ = 12345;
};

Scene empty() { return Scene(); }

Scene seated(Person p = Person()) {
  Scene s;
  s.person = true;
  s.p = p;
  return s;
}

// 빈 자리로 배경을 배우고, 앉아서 기준선이 잡힐 때까지 흘린다.
void calibrate(Sim &sim, Person p = Person()) {
  sim.run(empty(), 5000);
  sim.run(seated(p), 12000);
}

void assertPosture(TofPosture expected, const TofFeatures &f) {
  TEST_ASSERT_EQUAL_STRING(tofPostureName(expected), tofPostureName(f.posture));
}

}  // namespace

// ---------------------------------------------------------------------------

void test_empty_scene_is_away() {
  Sim sim(8, 8);
  sim.run(empty(), 5000);
  TEST_ASSERT_FALSE(sim.f().present);
  assertPosture(TofPosture::Away, sim.f());
  TEST_ASSERT_EQUAL_UINT16(0, sim.f().presence_count);
  TEST_ASSERT_EQUAL_UINT16(64, sim.f().valid_zones);
}

void test_near_objects_are_not_a_person() {
  // 책상 모서리·케이블처럼 아주 가까운 zone 은 사람 거리 범위 밖이다
  Sim sim(8, 8);
  Scene s = empty();
  s.desk_edge = true;
  sim.run(s, 5000);
  TEST_ASSERT_FALSE(sim.f().present);
}

void test_unknown_until_baseline_then_upright() {
  Sim sim(8, 8);
  sim.run(empty(), 5000);
  sim.run(seated(), 3000);
  TEST_ASSERT_TRUE(sim.f().present);
  TEST_ASSERT_FALSE(sim.f().baseline_ready);
  assertPosture(TofPosture::Unknown, sim.f());

  sim.run(seated(), 9000);
  TEST_ASSERT_TRUE(sim.f().baseline_ready);
  assertPosture(TofPosture::Upright, sim.f());
  TEST_ASSERT_TRUE(sim.f().head_delta_valid);
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 0.0f, sim.f().head_delta_mm);
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 650.0f, sim.f().head_depth_mm);

  // 확신도는 확정 뒤 일치가 쌓이면서 오른다(EMA τ 2 s)
  sim.run(seated(), 5000);
  TEST_ASSERT_TRUE(sim.f().posture_confidence > 0.9f);
}

void test_lean_forward_detected() {
  Sim sim(8, 8);
  calibrate(sim);
  Person p;
  p.head_mm -= 150;
  p.torso_mm -= 100;
  sim.run(seated(p), 2000);
  assertPosture(TofPosture::LeanForward, sim.f());
  TEST_ASSERT_FLOAT_WITHIN(15.0f, -150.0f, sim.f().head_delta_mm);
  TEST_ASSERT_TRUE(sim.f().baseline_deviation >= 1.0f);
}

void test_lean_back_detected() {
  Sim sim(8, 8);
  calibrate(sim);
  Person p;
  p.head_mm += 180;
  p.torso_mm += 120;
  sim.run(seated(p), 2000);
  assertPosture(TofPosture::LeanBack, sim.f());
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 180.0f, sim.f().head_delta_mm);
}

void test_slouch_detected() {
  Sim sim(8, 8);
  calibrate(sim);
  Person p;
  p.head_top += 0.3f;  // 머리가 화면 아래로 내려감
  sim.run(seated(p), 2000);
  assertPosture(TofPosture::Slouch, sim.f());
}

void test_face_down_counts_as_slouch() {
  // 엎드림: 머리가 크게 내려가면서 가까워진다. enum 에 별도 값이 없어 slouch 로 낸다
  Sim sim(8, 8);
  calibrate(sim);
  Person p;
  p.head_top += 0.4f;
  p.head_mm -= 200;
  sim.run(seated(p), 2000);
  assertPosture(TofPosture::Slouch, sim.f());
}

void test_short_lean_does_not_switch() {
  Sim sim(8, 8);
  calibrate(sim);
  Person p;
  p.head_mm -= 150;
  sim.run(seated(p), 500);  // posture_hold_ms(1 s) 보다 짧다
  sim.run(seated(), 1500);
  assertPosture(TofPosture::Upright, sim.f());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, sim.f().posture_change_rate_per_min);
}

void test_dropouts_and_spikes_do_not_flip() {
  Sim sim(8, 8);
  calibrate(sim);
  // 매 프레임 20% zone 무효(상태 4·타깃 없음), 5% zone 은 한 프레임짜리 큰 튐
  sim.run(seated(), 10000, [&sim](uint32_t, int16_t *d, uint8_t *st) {
    for (uint16_t z = 0; z < 64; z++) {
      const uint32_t r = sim.lcg() % 100;
      if (r < 10) st[z] = 4;
      else if (r < 20) d[z] = -1;
      else if (r < 25) d[z] = (int16_t)(d[z] > 1000 ? 300 : 3700);
    }
  });
  TEST_ASSERT_TRUE(sim.f().present);
  assertPosture(TofPosture::Upright, sim.f());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, sim.f().posture_change_rate_per_min);
}

void test_nod_rate() {
  Sim sim(8, 8);
  calibrate(sim);
  // 2 초마다 0.6 초 동안 머리가 60 mm 가까워진다 = 0.5 Hz
  sim.runHead(seated(), 20000, [](uint32_t t) { return (t % 2000) < 600 ? -60 : 0; });
  TEST_ASSERT_TRUE(sim.f().nod_rate_valid);
  TEST_ASSERT_FLOAT_WITHIN(0.1f, 0.5f, sim.f().nod_rate_hz);
  // 숙임 폭이 lean_forward 임계보다 작고 짧아 자세는 그대로다
  assertPosture(TofPosture::Upright, sim.f());
}

void test_no_nods_when_still() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(seated(), 20000);
  TEST_ASSERT_TRUE(sim.f().nod_rate_valid);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, sim.f().nod_rate_hz);
}

void test_shoulder_tilt_sign() {
  Sim sim(8, 8);
  Person p;
  p.tilt_mm = 120;  // 오른쪽 상체가 더 멀다
  calibrate(sim, p);
  TEST_ASSERT_TRUE(sim.f().shoulder_tilt_valid);
  TEST_ASSERT_FLOAT_WITHIN(20.0f, 120.0f, sim.f().shoulder_tilt_mm);
}

void test_motion_score_still_vs_moving() {
  Sim sim(8, 8);
  calibrate(sim);
  TEST_ASSERT_TRUE(sim.f().motion_score < 0.05f);  // ±5 mm 잡음은 데드밴드 안

  // 상체가 프레임마다 ±40 mm 씩 흔들림
  sim.runHead(seated(), 3000, [](uint32_t t) { return ((t / kFrameMs) % 2) ? 40 : -40; });
  TEST_ASSERT_TRUE(sim.f().motion_score > 0.3f);
}

void test_short_absence_keeps_baseline() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(empty(), 10000);
  TEST_ASSERT_FALSE(sim.f().present);
  assertPosture(TofPosture::Away, sim.f());
  TEST_ASSERT_TRUE(sim.f().baseline_ready);

  sim.run(seated(), 2000);
  assertPosture(TofPosture::Upright, sim.f());
}

void test_long_absence_drops_baseline() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(empty(), sim.est().config().baseline_reset_away_ms + 5000);
  TEST_ASSERT_FALSE(sim.f().baseline_ready);
  sim.run(seated(), 2000);
  assertPosture(TofPosture::Unknown, sim.f());
}

void test_posture_change_rate_counts_transitions() {
  Sim sim(8, 8);
  calibrate(sim);
  Person fwd;
  fwd.head_mm -= 150;
  for (int i = 0; i < 3; i++) {
    sim.run(seated(fwd), 3000);
    sim.run(seated(), 3000);
  }
  // upright→forward→upright 3번 = 전환 6회 (창 60 s 안)
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 6.0f, sim.f().posture_change_rate_per_min);
}

void test_static_object_needs_background_capture() {
  // 켤 때부터 의자가 사람 거리 안에 있으면 배경을 몰라 재실로 잡힌다
  Sim sim(8, 8);
  Scene s = empty();
  s.chair_mm = 1000;
  sim.run(s, 3000);
  TEST_ASSERT_TRUE(sim.f().present);

  sim.est().requestBackgroundCapture();
  sim.run(s, 4000);
  TEST_ASSERT_FALSE(sim.f().present);

  // 의자 앞에 사람이 앉으면 다시 잡힌다
  Scene sit = s;
  sit.person = true;
  sim.run(sit, 1000);
  TEST_ASSERT_TRUE(sim.f().present);
}

void test_strict_validity_ignores_semi_valid_status() {
  TofPostureConfig cfg;
  cfg.accept_semi_valid = false;
  Sim sim(8, 8, cfg);
  sim.run(seated(), 3000, [](uint32_t, int16_t *, uint8_t *st) {
    for (uint16_t z = 0; z < 64; z++) st[z] = 6;
  });
  TEST_ASSERT_EQUAL_UINT16(0, sim.f().valid_zones);
  TEST_ASSERT_FALSE(sim.f().present);
}

void test_row_flip_gives_same_posture() {
  TofPostureConfig cfg;
  cfg.row0_is_top = false;
  Sim sim(8, 8, cfg);
  // 센서가 뒤집혀 달린 것처럼 행 순서를 뒤집어 넣는다
  auto flip = [&sim](uint32_t, int16_t *d, uint8_t *) {
    for (uint8_t r = 0; r < 4; r++)
      for (uint8_t c = 0; c < 8; c++) {
        int16_t tmp = d[r * 8 + c];
        d[r * 8 + c] = d[(7 - r) * 8 + c];
        d[(7 - r) * 8 + c] = tmp;
      }
  };
  sim.run(empty(), 5000, flip);
  sim.run(seated(), 12000, flip);
  Person p;
  p.head_top += 0.3f;
  sim.run(seated(p), 2000, flip);
  assertPosture(TofPosture::Slouch, sim.f());
}

void test_same_postures_on_other_grids() {
  // VL53L8CX 8×8, VL53L9CX binning 8×6 · 24×20, 원본 54×42
  const uint8_t sizes[][2] = {{8, 8}, {8, 6}, {24, 20}, {54, 42}};
  for (const auto &sz : sizes) {
    Sim sim(sz[0], sz[1]);
    calibrate(sim);
    assertPosture(TofPosture::Upright, sim.f());
    TEST_ASSERT_EQUAL_UINT8(sz[0], sim.f().grid_width);
    TEST_ASSERT_EQUAL_UINT8(sz[1], sim.f().grid_height);

    Person fwd;
    fwd.head_mm -= 150;
    sim.run(seated(fwd), 2000);
    assertPosture(TofPosture::LeanForward, sim.f());

    Person low;
    low.head_top += 0.3f;
    sim.run(seated(low), 2000);
    assertPosture(TofPosture::Slouch, sim.f());

    sim.run(empty(), 4000);
    assertPosture(TofPosture::Away, sim.f());
  }
}

void test_oversized_or_empty_grid_is_rejected() {
  std::unique_ptr<TofPostureEstimator> est(new TofPostureEstimator());
  std::vector<int16_t> d(64, 500);
  TofFrameView v;
  v.width = 0;
  v.height = 8;
  v.distance_mm = d.data();
  TEST_ASSERT_FALSE(est->update(v, 0));
  v.width = 255;
  v.height = 255;
  TEST_ASSERT_FALSE(est->update(v, 0));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_empty_scene_is_away);
  RUN_TEST(test_near_objects_are_not_a_person);
  RUN_TEST(test_unknown_until_baseline_then_upright);
  RUN_TEST(test_lean_forward_detected);
  RUN_TEST(test_lean_back_detected);
  RUN_TEST(test_slouch_detected);
  RUN_TEST(test_face_down_counts_as_slouch);
  RUN_TEST(test_short_lean_does_not_switch);
  RUN_TEST(test_dropouts_and_spikes_do_not_flip);
  RUN_TEST(test_nod_rate);
  RUN_TEST(test_no_nods_when_still);
  RUN_TEST(test_shoulder_tilt_sign);
  RUN_TEST(test_motion_score_still_vs_moving);
  RUN_TEST(test_short_absence_keeps_baseline);
  RUN_TEST(test_long_absence_drops_baseline);
  RUN_TEST(test_posture_change_rate_counts_transitions);
  RUN_TEST(test_static_object_needs_background_capture);
  RUN_TEST(test_strict_validity_ignores_semi_valid_status);
  RUN_TEST(test_row_flip_gives_same_posture);
  RUN_TEST(test_same_postures_on_other_grids);
  RUN_TEST(test_oversized_or_empty_grid_is_rejected);
  return UNITY_END();
}
