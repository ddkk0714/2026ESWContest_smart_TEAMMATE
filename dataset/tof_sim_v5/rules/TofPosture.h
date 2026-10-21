#pragma once

#include <stdint.h>

namespace deskmate {

// ============================================================================
//  ToF 멀티존 거리 프레임으로 책상 앞 사람의 자세·졸음 추정
//
//  자세: 바른자세 · 상체 앞으로 · 뒤로 젖힘 · 엎드림 · 턱괴기 · 꾸벅 졸음 · 자리비움
//
//  정의는 팀에 이미 검증된 두 판정을 ToF 물리량으로 옮긴 것이다.
//   - display/atlas/camsvc/src/posture_pose (카메라 스켈레톤, 76EHwan/pico_esp32-cam_ftdi
//     정답지로 검증): 바른자세·젖힘·엎드림·턱괴기·자리비움과 판정 순서, 히스테리시스,
//     1.5 s 유지. 카메라는 깊이를 몰라 "어깨 폭이 좁아짐"으로 젖힘을 읽지만 ToF 는
//     거리를 직접 재므로 같은 문턱을 거리 비율로 쓴다(어깨 폭 0.90배 ↔ 거리 1/0.90배).
//     카메라의 머리 낙차는 어깨 폭 단위라 mm 로 바꿨다(어깨 폭 약 380 mm 가정).
//   - dataset/depth_teacher (D435if 깊이 카메라 교사 라벨, 프로토콜 3회 검증):
//     졸음 = 머리 동역학만. 0.5~1.5 s 에 고개가 떨어지고 1.5 s 안에 60% 이상
//     되돌아오는 끄덕임이 60 s 안에 2회, 또는 끄덕이듯 떨군 채 5 s 정지.
//     그동안 몸통이 크게 움직이면 끄덕임이 아니라 상체 숙임이다.
//  공개 데이터셋 중 8×8·54×42 ToF 로 앉은 자세를 다룬 것은 찾지 못했다.
//  깊이 기반 공개 데이터(SitPose — Azure Kinect, 젖힘 포함 6자세)는 자세 범주의
//  근거로만 참고했다. 실측 문턱은 depth_teacher 라벨로 맞춰야 한다.
//
//  격자 크기에 묶이지 않는다. VL53L9CX 54×42 원본, binning 축소 격자(24×20 ·
//  12×10 · 8×6 …), VL53L8CX 8×8 모두 같은 코드로 돈다.
//
//  판정 구조
//   1) zone 별 시간 필터: 상태값으로 유효 판정 → 최근 3프레임 중앙값(튀는 값 제거)
//      → 짧은 결측은 마지막 값 유지
//   2) 전경 분리: 빈 자리일 때 배운 배경보다 충분히 가까운 zone = 사람.
//      배경을 아직 모르면 거리 범위로만 자른다
//   3) 기하 특징 7종 + 머리·몸통 거리, 머리 낙차, 팔 기둥(턱괴기)
//   4) 앉은 뒤 처음 조용한 구간을 "바른 자세" 기준선으로 잡고, 기준선 대비 변화로
//      분류한다(절대 자세가 아닌 상대 변화 — 사람·의자 높이·거리마다 다시 맞추지 않게)
//   5) 후보 자세가 posture_hold_ms 동안 유지돼야 확정한다. 꾸벅임의 바닥은 기하가
//      엎드림과 같고, 둘은 모양이 아니라 시간으로 갈린다(camsvc 와 같은 이유)
//
//  좌표 약속: 입력 프레임은 row-major, row 0 이 장면의 위쪽(천장 쪽),
//  col 은 센서가 바라보는 방향 기준 왼→오른쪽. 센서 거치 방향이 달라
//  위아래가 뒤집히면 TofPostureConfig::row0_is_top 을 false 로 둔다.
//
//  임계값은 전부 TofPostureConfig 에 있다. 출처를 옮긴 값 외에는 합성 시나리오
//  (test/test_tof_posture)로만 맞춘 잠정값이고, 실측 후 교체한다.
// ============================================================================

#ifndef DESKMATE_TOF_MAX_ZONES
#define DESKMATE_TOF_MAX_ZONES (54 * 42)  // VL53L9CX 원본. 8×8 전용 빌드는 64 로 줄여 RAM 절약
#endif

constexpr uint16_t kTofMaxZones = DESKMATE_TOF_MAX_ZONES;

enum class TofPosture : uint8_t {
  Unknown = 0,  // 사람은 있지만 판단 근거 부족(기준선 미확보 등)
  Upright,      // 바른자세 (기준선)
  LeanForward,  // 상체 앞으로: 머리가 기준선보다 가까움, 낙차는 작음
  Recline,      // 뒤로 젖힘·눕기: 머리가 기준선보다 멂
  FaceDown,     // 엎드림: 머리가 기준선보다 크게 내려감
  ChinRest,     // 턱괴기: 몸통 앞으로 세운 팔 기둥이 머리 밑까지 이어짐
  Drowsy,       // 꾸벅 졸음: 끄덕임 반복 또는 끄덕이듯 떨군 채 정지
  Away,         // 자리비움
};

const char *tofPostureName(TofPosture p);  // "upright" "recline" "face_down" …

struct TofPostureConfig {
  // --- 입력 ---
  bool row0_is_top = true;           // false 면 위아래를 뒤집어 해석
  bool accept_semi_valid = true;     // 상태값 6·10(대체로 유효)도 받기. 5·9 는 항상 받음
  float fov_vertical_deg = 45.0f;    // 격자 세로 시야각. VL53L8CX 45°. 머리 낙차를 mm 로 바꿀 때 씀

  // --- zone 시간 필터 ---
  uint8_t hold_frames = 3;           // 결측 시 마지막 값을 유지할 프레임 수

  // --- 전경(사람) 분리 ---
  int16_t min_person_mm = 150;       // 이보다 가까우면 책상 모서리·케이블로 보고 버림
  int16_t max_person_mm = 1300;      // 이보다 멀면 사람으로 보지 않음
  int16_t bg_margin_mm = 150;        // 배경보다 이만큼 가까워야 사람
  uint32_t bg_learn_after_ms = 3000; // 자리 비움이 이만큼 이어진 뒤부터 배경 학습
  float bg_alpha = 0.05f;            // 배경 EMA 계수 (프레임당)

  // --- 재실 ---
  float present_min_coverage = 0.06f;  // 전경 zone 비율 하한
  uint32_t present_on_ms = 500;        // 이만큼 이어져야 재실
  uint32_t away_ms = 3000;             // 이만큼 비어야 자리 비움

  // --- 머리 영역 ---
  float head_row_min_fill = 0.10f;   // 행의 이 비율(최소 1 zone) 이상이 전경이면 "머리 꼭대기 행"
  float head_band_ratio = 0.25f;     // 머리 꼭대기에서 아래로 격자 높이의 이 비율(최소 1행)을 머리 영역으로

  // --- 움직임 ---
  int16_t motion_deadband_mm = 15;   // 프레임 간 변화가 이 이하면 센서 잡음으로 보고 0
  uint32_t motion_tau_ms = 1000;     // motion EMA 시정수
  float motion_full_mm_s = 300.0f;   // motion_score 1.0 에 해당하는 평균 변화 속도

  // --- 기준선(바른 자세) ---
  float baseline_still_motion = 0.25f;     // motion_score 가 이 아래인 구간만 기준선으로
  uint32_t baseline_ms = 10000;            // 조용한 구간이 이만큼 이어지면 기준선 확정
  uint32_t baseline_reset_away_ms = 300000; // 이만큼 자리를 비우면 기준선 폐기(다른 사람·재착석)

  // --- 자세 분류 (기준선 대비, on/off 히스테리시스) ---
  // 젖힘: camsvc kShrinkOn/Off(어깨 폭 0.90/0.95배) ↔ 머리 거리 1/0.90, 1/0.95 배
  float recline_ratio_on = 1.11f;
  float recline_ratio_off = 1.05f;
  // 엎드림: camsvc kSlumpDropOn/Off(어깨 폭 0.35/0.20) × 어깨 폭 약 380 mm
  float face_down_drop_on_mm = 135.0f;
  float face_down_drop_off_mm = 75.0f;
  // 상체 앞으로: depth_teacher lean_forward 5 cm(몸통). 머리 기준이라 조금 넉넉히
  float lean_forward_mm = 80.0f;
  // 턱괴기: 머리 밑 상체 행 중 이 비율 이상이, 머리 바로 아래부터 끊김 없이
  // 몸통 중앙값보다 chin_arm_margin_mm 이상 가까운 열이 있으면 팔 기둥.
  // 타이핑하는 팔뚝은 맨 아래 행에만 걸려 여기에 안 걸린다.
  int16_t chin_arm_margin_mm = 60;
  float chin_arm_rows_on = 0.6f;
  float chin_arm_rows_off = 0.4f;
  uint8_t chin_arm_min_rows = 2;     // 비율과 별개로 최소 이만큼의 행
  uint32_t posture_hold_ms = 1500;   // camsvc kHoldSeconds
  uint32_t confidence_tau_ms = 2000; // posture_confidence EMA 시정수
  uint32_t change_window_ms = 60000; // posture_change_rate 집계 창

  // --- 끄덕임·졸음 (depth_teacher drowsiness 를 mm 로) ---
  // 끄덕임 신호 = (기준선 머리 거리 − 현재) + 머리 낙차 mm. 고개를 떨구면 이마가
  // 앞·아래로 나온다. 15° 낙하 × 회전 반지름 약 15 cm ≈ 40 mm
  float nod_amp_mm = 40.0f;
  // 정점이 기준선보다 이만큼은 숙여져 있어야 한다. 위를 봤다(또는 머리 꼭대기 행이
  // 잡음으로 한 칸 올라갔다) 돌아오는 것을 끄덕임으로 세지 않는다.
  // depth_teacher nod_min_peak_deg 10° 를 nod_amp 15° 와 같은 비율로 옮김
  float nod_min_peak_mm = 27.0f;
  uint32_t nod_lookback_ms = 2000;   // 낙하 시작점 = 직전 이 시간의 최저점
  uint32_t nod_fall_max_ms = 1500;   // 시작점에서 문턱 도달까지 허용 시간
  float nod_recover_frac = 0.6f;     // 떨어진 폭의 이만큼 되돌아오면 복귀
  uint32_t nod_recover_ms = 1500;    // 정점 → 복귀 허용 시간('번쩍')
  uint32_t nod_max_ms = 6000;        // 떨굼~복귀 전체 허용 시간
  float nod_body_mm = 80.0f;         // 그동안 몸통 거리가 이보다 변하면 상체 숙임
  uint32_t nod_window_ms = 60000;
  uint8_t nod_count = 2;             // 창 안에 이만큼 끄덕이면 졸음
  uint32_t drowsy_still_ms = 5000;   // 끄덕이듯 떨군 채 이만큼 정지해도 졸음
  uint32_t drowsy_hold_ms = 5000;    // 졸음 근거가 사라진 뒤 유지 시간
  uint32_t nod_rate_min_track_ms = 10000;  // nod_rate_hz 를 내기 전 최소 관측 시간
};

struct TofFeatures {
  // ---- data-spec §6.1 tof_feature ----
  bool present = false;
  TofPosture posture = TofPosture::Away;
  float posture_confidence = 0.0f;  // 최근 판정이 현재 자세와 일치한 비율(EMA), 0..1
  float motion_score = 0.0f;        // 0..1
  bool head_delta_valid = false;
  float head_delta_mm = 0.0f;       // 기준선 대비 머리 거리 변화. 음수 = 가까워짐
  bool nod_rate_valid = false;
  float nod_rate_hz = 0.0f;
  uint16_t valid_zones = 0;
  uint8_t grid_width = 0;
  uint8_t grid_height = 0;
  float coverage_ratio = 0.0f;      // 전경 zone 비율

  // ---- roadmap §4-4 기하 특징 7종 ----
  uint16_t presence_count = 0;      // ① 전경 zone 수
  bool geometry_valid = false;      // 아래 ②~④ 가 의미 있는지(전경이 충분할 때만)
  float centroid_depth_mm = 0.0f;   // ② 전경 거리 중앙값
  float head_row_index = 0.0f;      // ③ 머리 꼭대기 행, 0 = 맨 위 .. 1 = 맨 아래
  bool shoulder_tilt_valid = false;
  float shoulder_tilt_mm = 0.0f;    // ④ 머리 아래 상체의 (오른쪽 − 왼쪽) 거리 중앙값 차
  float motion_indicator_mm_s = 0.0f;   // ⑤ 전경 zone 평균 거리 변화 속도(EMA)
  float posture_change_rate_per_min = 0.0f; // ⑥ 최근 change_window_ms 동안 자세 전환 횟수/분
  float baseline_deviation = 0.0f;  // ⑦ 기준선 대비 편차. 1.0 = 가장 가까운 분류 임계에 도달

  // ---- 자세 판정 근거 ----
  bool baseline_ready = false;
  float head_depth_mm = 0.0f;       // 머리 영역 거리 중앙값
  bool torso_valid = false;
  float torso_depth_mm = 0.0f;      // 머리 아래 상체 거리 중앙값
  float head_drop_mm = 0.0f;        // 기준선 대비 머리 꼭대기 하강(mm 환산). 양수 = 내려감
  float chin_arm_ratio = 0.0f;      // 팔 기둥이 차지한 상체 행 비율(가장 긴 열)
  int8_t chin_arm_side = 0;         // 팔 기둥 위치: -1 왼쪽, 0 가운데/없음, +1 오른쪽
  bool drowsy = false;              // 졸음 판정 (posture 가 Drowsy 일 때 true)
  uint8_t nods_in_window = 0;       // 최근 nod_window_ms 끄덕임 수
};

// 한 프레임. 배열은 update() 호출 동안만 살아 있으면 된다.
struct TofFrameView {
  uint8_t width = 0;
  uint8_t height = 0;
  const int16_t *distance_mm = nullptr;  // row-major, 음수 = 타깃 없음
  const uint8_t *status = nullptr;       // VL53 target status. nullptr 이면 거리 ≥ 0 을 전부 유효로
};

class TofPostureEstimator {
 public:
  explicit TofPostureEstimator(const TofPostureConfig &cfg = TofPostureConfig());

  // 프레임 하나를 처리한다. 격자가 kTofMaxZones 를 넘거나 비어 있으면 false.
  // 격자 크기가 바뀌면 내부 상태(배경·기준선 포함)를 처음부터 다시 쌓는다.
  bool update(const TofFrameView &frame, uint32_t now_ms);

  const TofFeatures &features() const { return feat_; }
  const TofPostureConfig &config() const { return cfg_; }

  // 바른 자세로 다시 앉았을 때 기준선을 새로 잡게 한다(UI "자세 기준 다시 잡기").
  void resetBaseline();
  // 다음 프레임을 그대로 배경으로 삼는다. 자리 비움 자동 학습을 기다릴 수 없을 때
  // (켤 때부터 의자 등 정지 물체가 사람 거리 안에 있어 계속 재실로 잡히는 경우) 쓴다.
  void requestBackgroundCapture() { bg_capture_pending_ = true; }
  // 배경·기준선·필터를 전부 비운다.
  void reset();

 private:
  struct ZoneState {
    int16_t hist[3];
    uint8_t hist_n;
    uint8_t age;   // 마지막 유효 이후 프레임 수
    int16_t filt;  // 필터 출력, -1 = 무효
    int16_t prev;  // 직전 프레임 filt
    int16_t bg;    // 배경 거리, -1 = 모름
  };

  enum class NodPhase : uint8_t { Idle, Falling, Held };

  bool accepted(int16_t d, uint8_t st) const;
  void filterZones(const TofFrameView &frame);
  uint16_t segment();
  void computeGeometry(uint16_t fg_count);
  void updateMotion(float dt_s);
  void updatePresence(bool raw_present, uint32_t now_ms);
  void updateBackground(uint32_t now_ms);
  void updateBaseline(uint32_t now_ms);
  void updateNod(uint32_t now_ms);
  void classify(uint32_t now_ms, float dt_s);
  void commitPosture(TofPosture p, uint32_t now_ms);
  void resetNod();
  int16_t medianOf(uint16_t n);
  bool isForeground(uint16_t z) const;

  TofPostureConfig cfg_;
  TofFeatures feat_;

  uint8_t w_ = 0, h_ = 0;
  uint16_t zones_ = 0;
  // 54×42 기준 약 35 KB. 스택이 아니라 전역·정적 객체로 둔다.
  ZoneState zs_[kTofMaxZones];
  uint8_t fg_[kTofMaxZones];  // bit0 = 이번 프레임 전경, bit1 = 직전 프레임 전경
  int16_t scratch_[kTofMaxZones];
  bool bg_capture_pending_ = false;

  bool have_last_ts_ = false;
  uint32_t last_ts_ = 0;

  // 재실 히스테리시스
  bool raw_present_ = false;    // 이번 프레임 전경 비율이 재실 하한 이상
  uint32_t raw_since_ms_ = 0;
  uint32_t away_since_ms_ = 0;

  // 움직임
  float motion_ema_ = 0.0f;

  // 기준선
  bool baseline_ready_ = false;
  float base_head_depth_ = 0.0f;
  float base_head_row_ = 0.0f;
  bool calib_active_ = false;
  uint32_t calib_start_ms_ = 0;
  double calib_depth_sum_ = 0.0;
  double calib_row_sum_ = 0.0;
  uint32_t calib_n_ = 0;

  // 자세 스위치 히스테리시스 (camsvc was_*)
  bool was_dropped_ = false;
  bool was_chin_ = false;
  bool was_receded_ = false;

  // 자세 확정
  TofPosture candidate_ = TofPosture::Away;
  uint32_t candidate_since_ms_ = 0;
  static constexpr uint8_t kChangeRing = 32;
  uint32_t change_ts_[kChangeRing];
  uint8_t change_n_ = 0;
  uint8_t change_head_ = 0;

  // 끄덕임
  static constexpr uint8_t kNodSampleRing = 96;  // 30 Hz × 3 s 까지
  uint32_t nod_sample_ts_[kNodSampleRing];
  float nod_sample_v_[kNodSampleRing];
  float nod_sample_torso_[kNodSampleRing];  // 그 시점 몸통 거리, -1 = 모름
  uint8_t nod_sample_n_ = 0;
  uint8_t nod_sample_head_ = 0;
  NodPhase nod_phase_ = NodPhase::Idle;
  bool nod_armed_ = true;  // 취소된 낙하가 끝나 머리가 돌아올 때까지 새 낙하를 받지 않음
  float nod_start_v_ = 0.0f;
  uint32_t nod_start_ms_ = 0;
  float nod_peak_v_ = 0.0f;
  uint32_t nod_peak_ms_ = 0;
  float nod_torso_ref_ = 0.0f;
  bool nod_torso_ref_valid_ = false;
  uint32_t held_still_since_ms_ = 0;
  bool held_still_ = false;
  static constexpr uint8_t kNodRing = 32;
  uint32_t nod_ts_[kNodRing];
  uint8_t nod_n_ = 0;
  uint8_t nod_head_ = 0;
  bool nod_tracking_ = false;
  uint32_t nod_track_start_ms_ = 0;
  bool drowsy_evidence_seen_ = false;
  uint32_t drowsy_evidence_ms_ = 0;
};

}  // namespace deskmate
