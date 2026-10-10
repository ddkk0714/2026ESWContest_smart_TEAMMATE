// ToF 자세·졸음 추정기의 판정 구조를 합성 장면으로 고정한다.
//
// 장면: 뒤쪽 벽(1600 mm) 앞에 머리(원)·상체(직사각형)·팔(직사각형)로 된 사람을
// 그린다. zone 중심에 걸리는 도형 중 가장 가까운 것의 거리를 쓴다(VL53 Closest 모드).
// 15 Hz, ±5 mm 잡음. 실측이 아니므로 임계값의 "정답"을 증명하지 않는다.
// 대신 배경 분리·기준선·자세 판정 순서·히스테리시스·유지 시간·끄덕임/졸음 판정과
// 격자 크기 무관성이라는 구조를 붙잡는다.
#include <unity.h>

#include <cstdint>
#include <functional>
#include <memory>
#include <vector>

#include "preprocess/tof/TofPosture.h"

using namespace deskmate;

void setUp() {}
void tearDown() {}

namespace {

constexpr uint32_t kFrameMs = 67;  // ≈ 15 Hz

enum class Arm { None, ChinRight, ChinLeft, Typing };

struct Person {
  float head_top = 0.15f;   // 머리 꼭대기, 격자 높이 대비 (0 = 맨 위)
  float head_r = 0.14f;     // 머리 반지름, 정규화 좌표
  float shift = 0.0f;       // 좌우 이동, 격자 너비 대비
  int head_mm = 650;
  int torso_mm = 700;
  int tilt_mm = 0;          // 상체 오른쪽 절반은 +tilt/2, 왼쪽 절반은 -tilt/2
  Arm arm = Arm::None;
};

struct Scene {
  bool person = false;
  Person p;
  int bg_mm = 1600;
  bool desk_edge = false;   // 맨 아래 행에 60 mm 짜리 책상 모서리
  int chair_mm = 0;         // > 0 이면 아래쪽 가운데에 사람 거리 안의 정지 물체
};

// 시간에 따라 머리를 움직이는 함수: (경과 ms) → (머리 꼭대기 이동, 머리 거리 이동, 몸통 거리 이동)
struct Motion {
  float head_top = 0.0f;
  int head_mm = 0;
  int torso_mm = 0;
};
using MotionFn = std::function<Motion(uint32_t)>;

// depth_teacher 실측 모양의 끄덕임: 0.8 s 에 떨어지고 0.2 s 머문 뒤 0.5 s 에 돌아온다
Motion nodShape(uint32_t t, float drop = 0.08f, int closer = 50) {
  float k = 0.0f;
  if (t < 800) k = t / 800.0f;
  else if (t < 1000) k = 1.0f;
  else if (t < 1500) k = 1.0f - (t - 1000) / 500.0f;
  Motion m;
  m.head_top = drop * k;
  m.head_mm = (int)(-closer * k);
  return m;
}

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

  // 머리·몸통을 시간에 따라 움직이며 흘린다.
  void runMotion(Scene s, uint32_t ms, const MotionFn &fn) {
    const Person base = s.p;
    for (uint32_t elapsed = 0; elapsed < ms; elapsed += kFrameMs) {
      const Motion m = fn(elapsed);
      s.p.head_top = base.head_top + m.head_top;
      s.p.head_mm = base.head_mm + m.head_mm;
      s.p.torso_mm = base.torso_mm + m.torso_mm;
      render(s);
      push();
      frame_++;
    }
  }

  const TofFeatures &f() const { return est_->features(); }
  TofPostureEstimator &est() { return *est_; }

  void render(const Scene &s) {
    for (uint8_t r = 0; r < h_; r++) {
      for (uint8_t c = 0; c < w_; c++) {
        const float u = (c + 0.5f) / w_;
        const float v = (r + 0.5f) / h_;
        int d = s.bg_mm;
        if (s.chair_mm > 0 && u > 0.3f && u < 0.7f && v > 0.6f) d = std::min(d, s.chair_mm);
        if (s.person) {
          const Person &p = s.p;
          const float cu = 0.5f + p.shift;
          const float cv = p.head_top + p.head_r;
          const float du = u - cu, dv = v - cv;
          const float shoulder = p.head_top + 2.0f * p.head_r + 0.02f;
          if (du * du + dv * dv <= p.head_r * p.head_r) d = std::min(d, p.head_mm);
          if (v >= shoulder && u >= cu - 0.3f && u <= cu + 0.3f)
            d = std::min(d, p.torso_mm + (u >= cu ? p.tilt_mm / 2 : -p.tilt_mm / 2));
          const int arm_mm = p.torso_mm - 120;  // 팔뚝은 가슴보다 앞에 있다
          const float chin = p.head_top + 2.0f * p.head_r - 0.04f;
          switch (p.arm) {
            case Arm::ChinRight:
              if (v >= chin && u >= cu + 0.06f && u <= cu + 0.18f) d = std::min(d, arm_mm);
              break;
            case Arm::ChinLeft:
              if (v >= chin && u >= cu - 0.18f && u <= cu - 0.06f) d = std::min(d, arm_mm);
              break;
            case Arm::Typing:
              if (v >= 0.86f && u >= cu - 0.35f && u <= cu + 0.35f) d = std::min(d, p.torso_mm - 150);
              break;
            case Arm::None:
              break;
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

// 빈 자리로 배경을 배우고, 앉아서 기준선이 잡히고(재실 0.5 s + 10 s)
// 바른자세가 확정될(1.5 s) 때까지 흘린다.
void calibrate(Sim &sim, Person p = Person()) {
  sim.run(empty(), 5000);
  sim.run(seated(p), 13000);
}

void assertPosture(TofPosture expected, const TofFeatures &f) {
  TEST_ASSERT_EQUAL_STRING(tofPostureName(expected), tofPostureName(f.posture));
}

Person leanForward() {
  Person p;
  p.head_mm -= 150;
  p.torso_mm -= 100;
  return p;
}

Person recline() {
  Person p;
  p.head_mm += 150;
  p.torso_mm += 100;
  p.head_top += 0.05f;
  return p;
}

Person faceDown() {
  Person p;
  p.head_top += 0.45f;
  p.head_mm -= 150;
  p.torso_mm -= 120;
  return p;
}

Person chinRest(Arm side = Arm::ChinRight) {
  Person p;
  p.arm = side;
  return p;
}

}  // namespace

// ---------------------------------------------------------------------------
// 재실·배경

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

  Scene sit = s;
  sit.person = true;
  sim.run(sit, 1000);
  TEST_ASSERT_TRUE(sim.f().present);
}

// ---------------------------------------------------------------------------
// 기준선·자세

void test_unknown_until_baseline_then_upright() {
  Sim sim(8, 8);
  sim.run(empty(), 5000);
  sim.run(seated(), 3000);
  TEST_ASSERT_TRUE(sim.f().present);
  TEST_ASSERT_FALSE(sim.f().baseline_ready);
  assertPosture(TofPosture::Unknown, sim.f());

  sim.run(seated(), 10000);
  TEST_ASSERT_TRUE(sim.f().baseline_ready);
  assertPosture(TofPosture::Upright, sim.f());
  TEST_ASSERT_TRUE(sim.f().head_delta_valid);
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 0.0f, sim.f().head_delta_mm);
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 650.0f, sim.f().head_depth_mm);
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 0.0f, sim.f().head_drop_mm);

  // 확신도는 확정 뒤 일치가 쌓이면서 오른다(EMA τ 2 s)
  sim.run(seated(), 5000);
  TEST_ASSERT_TRUE(sim.f().posture_confidence > 0.9f);
}

void test_lean_forward_detected() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(seated(leanForward()), 2500);
  assertPosture(TofPosture::LeanForward, sim.f());
  TEST_ASSERT_FLOAT_WITHIN(15.0f, -150.0f, sim.f().head_delta_mm);
  TEST_ASSERT_TRUE(sim.f().baseline_deviation >= 1.0f);
}

void test_recline_detected() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(seated(recline()), 2500);
  assertPosture(TofPosture::Recline, sim.f());
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 150.0f, sim.f().head_delta_mm);
}

void test_face_down_detected() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(seated(faceDown()), 2500);
  assertPosture(TofPosture::FaceDown, sim.f());
  TEST_ASSERT_TRUE(sim.f().head_drop_mm > 135.0f);
  // 상체째 접은 것이라 끄덕임·졸음이 아니다
  TEST_ASSERT_FALSE(sim.f().drowsy);
}

void test_chin_rest_detected_on_either_side() {
  Sim right(8, 8);
  calibrate(right);
  right.run(seated(chinRest(Arm::ChinRight)), 2500);
  assertPosture(TofPosture::ChinRest, right.f());
  TEST_ASSERT_EQUAL_INT8(1, right.f().chin_arm_side);
  TEST_ASSERT_TRUE(right.f().chin_arm_ratio >= 0.6f);

  Sim left(8, 8);
  calibrate(left);
  left.run(seated(chinRest(Arm::ChinLeft)), 2500);
  assertPosture(TofPosture::ChinRest, left.f());
  TEST_ASSERT_EQUAL_INT8(-1, left.f().chin_arm_side);
}

void test_typing_forearms_are_not_chin_rest() {
  // 키보드 위 팔뚝은 맨 아래 행에만 걸린다
  Sim sim(8, 8);
  calibrate(sim);
  Person p;
  p.arm = Arm::Typing;
  sim.run(seated(p), 3000);
  assertPosture(TofPosture::Upright, sim.f());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, sim.f().chin_arm_ratio);
}

void test_face_down_wins_over_chin_rest() {
  // 팔이 보여도 머리가 엎드림만큼 내려갔으면 엎드림 (camsvc: chin && !dropped)
  Sim sim(8, 8);
  calibrate(sim);
  Person p = faceDown();
  p.arm = Arm::ChinRight;
  sim.run(seated(p), 2500);
  assertPosture(TofPosture::FaceDown, sim.f());
}

void test_short_lean_does_not_switch() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(seated(leanForward()), 1000);  // posture_hold_ms(1.5 s) 보다 짧다
  sim.run(seated(), 2000);
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
  TEST_ASSERT_FALSE(sim.f().drowsy);
}

// ---------------------------------------------------------------------------
// 끄덕임·졸음

void test_single_nod_is_not_drowsy() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.runMotion(seated(), 4000, [](uint32_t t) { return nodShape(t); });
  TEST_ASSERT_EQUAL_UINT8(1, sim.f().nods_in_window);
  TEST_ASSERT_FALSE(sim.f().drowsy);
  assertPosture(TofPosture::Upright, sim.f());
}

void test_two_nods_in_a_minute_are_drowsy_then_clear() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.runMotion(seated(), 4000, [](uint32_t t) { return nodShape(t); });
  sim.run(seated(), 10000);
  sim.runMotion(seated(), 4000, [](uint32_t t) { return nodShape(t); });
  TEST_ASSERT_EQUAL_UINT8(2, sim.f().nods_in_window);
  TEST_ASSERT_TRUE(sim.f().drowsy);
  assertPosture(TofPosture::Drowsy, sim.f());

  // 끄덕임이 창(60 s)을 벗어나고 유지 시간(5 s)이 지나면 풀린다
  sim.run(seated(), 70000);
  TEST_ASSERT_FALSE(sim.f().drowsy);
  assertPosture(TofPosture::Upright, sim.f());
}

void test_nod_then_head_held_down_is_drowsy() {
  // 끄덕이듯 떨군 뒤 그대로 5 s 넘게 정지
  Sim sim(8, 8);
  calibrate(sim);
  sim.runMotion(seated(), 9000, [](uint32_t t) {
    Motion m = nodShape(std::min<uint32_t>(t, 900));  // 정점에서 멈춘다
    return m;
  });
  TEST_ASSERT_EQUAL_UINT8(0, sim.f().nods_in_window);
  TEST_ASSERT_TRUE(sim.f().drowsy);
  assertPosture(TofPosture::Drowsy, sim.f());
}

void test_quick_lean_with_torso_is_not_a_nod() {
  // 상체째 숙였다 일어나는 것은 머리 신호가 커도 끄덕임이 아니다 (몸통 거리 변화)
  Sim sim(8, 8);
  calibrate(sim);
  for (int i = 0; i < 3; i++) {
    sim.runMotion(seated(), 3000, [](uint32_t t) {
      Motion m = nodShape(t, 0.0f, 120);
      m.torso_mm = m.head_mm;
      return m;
    });
  }
  TEST_ASSERT_EQUAL_UINT8(0, sim.f().nods_in_window);
  TEST_ASSERT_FALSE(sim.f().drowsy);
}

void test_head_forward_held_is_not_drowsy() {
  // 고개만 앞으로 빼고 가만히 있는 것(거북목)은 떨굼이 아니다
  Sim sim(8, 8);
  calibrate(sim);
  sim.runMotion(seated(), 9000, [](uint32_t t) {
    Motion m;
    m.head_mm = t < 800 ? -(int)(t * 60 / 800) : -60;
    return m;
  });
  TEST_ASSERT_FALSE(sim.f().drowsy);
}

void test_nod_rate() {
  Sim sim(8, 8);
  calibrate(sim);
  // 6 초마다 한 번씩 60 초 = 창(60 s) 안에 10회
  sim.runMotion(seated(), 60000, [](uint32_t t) { return nodShape(t % 6000); });
  TEST_ASSERT_TRUE(sim.f().nod_rate_valid);
  TEST_ASSERT_EQUAL_UINT8(10, sim.f().nods_in_window);
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 10.0f / 60.0f, sim.f().nod_rate_hz);
}

void test_no_nods_when_still() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(seated(), 20000);
  TEST_ASSERT_TRUE(sim.f().nod_rate_valid);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, sim.f().nod_rate_hz);
  TEST_ASSERT_FALSE(sim.f().drowsy);
}

// ---------------------------------------------------------------------------
// 특징

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
  sim.runMotion(seated(), 3000, [](uint32_t t) {
    Motion m;
    m.head_mm = m.torso_mm = ((t / kFrameMs) % 2) ? 40 : -40;
    return m;
  });
  TEST_ASSERT_TRUE(sim.f().motion_score > 0.3f);
}

void test_short_absence_keeps_baseline() {
  Sim sim(8, 8);
  calibrate(sim);
  sim.run(empty(), 10000);
  TEST_ASSERT_FALSE(sim.f().present);
  assertPosture(TofPosture::Away, sim.f());
  TEST_ASSERT_TRUE(sim.f().baseline_ready);

  sim.run(seated(), 2500);
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
  for (int i = 0; i < 3; i++) {
    sim.run(seated(recline()), 3000);
    sim.run(seated(), 3000);
  }
  // upright→recline→upright 3번 = 전환 6회 (창 60 s 안)
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 6.0f, sim.f().posture_change_rate_per_min);
}

// ---------------------------------------------------------------------------
// 입력 형식

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
  auto flip = [](uint32_t, int16_t *d, uint8_t *) {
    for (uint8_t r = 0; r < 4; r++)
      for (uint8_t c = 0; c < 8; c++) {
        int16_t tmp = d[r * 8 + c];
        d[r * 8 + c] = d[(7 - r) * 8 + c];
        d[(7 - r) * 8 + c] = tmp;
      }
  };
  sim.run(empty(), 5000, flip);
  sim.run(seated(), 12000, flip);
  sim.run(seated(faceDown()), 2500, flip);
  assertPosture(TofPosture::FaceDown, sim.f());
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

    sim.run(seated(leanForward()), 2500);
    assertPosture(TofPosture::LeanForward, sim.f());
    sim.run(seated(recline()), 2500);
    assertPosture(TofPosture::Recline, sim.f());
    sim.run(seated(faceDown()), 2500);
    assertPosture(TofPosture::FaceDown, sim.f());
    sim.run(seated(chinRest()), 2500);
    assertPosture(TofPosture::ChinRest, sim.f());
    sim.run(seated(), 2500);
    assertPosture(TofPosture::Upright, sim.f());

    sim.runMotion(seated(), 4000, [](uint32_t t) { return nodShape(t); });
    sim.run(seated(), 5000);
    sim.runMotion(seated(), 4000, [](uint32_t t) { return nodShape(t); });
    assertPosture(TofPosture::Drowsy, sim.f());

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
  RUN_TEST(test_static_object_needs_background_capture);
  RUN_TEST(test_unknown_until_baseline_then_upright);
  RUN_TEST(test_lean_forward_detected);
  RUN_TEST(test_recline_detected);
  RUN_TEST(test_face_down_detected);
  RUN_TEST(test_chin_rest_detected_on_either_side);
  RUN_TEST(test_typing_forearms_are_not_chin_rest);
  RUN_TEST(test_face_down_wins_over_chin_rest);
  RUN_TEST(test_short_lean_does_not_switch);
  RUN_TEST(test_dropouts_and_spikes_do_not_flip);
  RUN_TEST(test_single_nod_is_not_drowsy);
  RUN_TEST(test_two_nods_in_a_minute_are_drowsy_then_clear);
  RUN_TEST(test_nod_then_head_held_down_is_drowsy);
  RUN_TEST(test_quick_lean_with_torso_is_not_a_nod);
  RUN_TEST(test_head_forward_held_is_not_drowsy);
  RUN_TEST(test_nod_rate);
  RUN_TEST(test_no_nods_when_still);
  RUN_TEST(test_shoulder_tilt_sign);
  RUN_TEST(test_motion_score_still_vs_moving);
  RUN_TEST(test_short_absence_keeps_baseline);
  RUN_TEST(test_long_absence_drops_baseline);
  RUN_TEST(test_posture_change_rate_counts_transitions);
  RUN_TEST(test_strict_validity_ignores_semi_valid_status);
  RUN_TEST(test_row_flip_gives_same_posture);
  RUN_TEST(test_same_postures_on_other_grids);
  RUN_TEST(test_oversized_or_empty_grid_is_rejected);
  return UNITY_END();
}
