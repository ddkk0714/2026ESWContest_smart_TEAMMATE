#pragma once

#include <stdint.h>

namespace deskmate {

// ============================================================================
//  ToF 멀티존 거리 프레임으로 책상 앞 사람의 자세 추정
//
//  roadmap §4-4 의 기하 특징 7종을 만들고, 그것으로 data-spec §6.1
//  `tof_feature` 의 posture enum · motion_score · head_delta_mm · nod_rate_hz 를
//  낸다. 원본 zone 배열은 여기서 소비하고 밖으로 내보내지 않는다
//  (운영 ToF raw 미전송).
//
//  격자 크기에 묶이지 않는다. VL53L9CX 54×42 원본, binning 축소 격자(24×20 ·
//  12×10 · 8×6 …), VL53L8CX 8×8 모두 같은 코드로 돈다. 비율로 정의한 값
//  (coverage, head_row_index 등)은 격자가 바뀌어도 의미가 같다.
//
//  판정 구조
//   1) zone 별 시간 필터: 상태값으로 유효 판정 → 최근 3프레임 중앙값(튀는 값 제거)
//      → 짧은 결측은 마지막 값 유지
//   2) 전경 분리: 빈 자리일 때 배운 배경보다 충분히 가까운 zone = 사람.
//      배경을 아직 모르면 거리 범위로만 자른다
//   3) 기하 특징 7종 계산
//   4) 앉은 뒤 처음 조용한 구간을 "바른 자세" 기준선으로 잡고,
//      기준선 대비 머리 거리·머리 높이 변화로 자세를 분류한다
//      (절대 자세가 아닌 상대 변화 — data-spec §8 baseline 원칙과 같다)
//   5) 후보 자세가 posture_hold_ms 동안 유지돼야 확정한다(채터링 방지)
//
//  좌표 약속: 입력 프레임은 row-major, row 0 이 장면의 위쪽(천장 쪽),
//  col 은 센서가 바라보는 방향 기준 왼→오른쪽. 센서 거치 방향이 달라
//  위아래가 뒤집히면 TofPostureConfig::row0_is_top 을 false 로 둔다.
//
//  임계값은 전부 TofPostureConfig 에 있다. 기본값은 합성 시나리오
//  (test/test_tof_posture)로만 맞춘 잠정값이고, 실측 후 교체한다.
// ============================================================================

#ifndef DESKMATE_TOF_MAX_ZONES
#define DESKMATE_TOF_MAX_ZONES (54 * 42)  // VL53L9CX 원본. 8×8 전용 빌드는 64 로 줄여 RAM 절약
#endif

constexpr uint16_t kTofMaxZones = DESKMATE_TOF_MAX_ZONES;

// data-spec §6.1 posture enum 과 같은 순서·이름
enum class TofPosture : uint8_t {
  Unknown = 0,  // 사람은 있지만 판단 근거 부족(기준선 미확보 등)
  Upright,      // 기준선 자세
  LeanForward,  // 머리가 기준선보다 가까움 (숙임·거북목·앞으로 기울임)
  LeanBack,     // 머리가 기준선보다 멂 (깊게 기대기)
  Slouch,       // 머리 높이가 기준선보다 낮음 (구부정함 · 극단은 엎드림)
  Away,         // 자리 비움
};

const char *tofPostureName(TofPosture p);  // "upright" 등 data-spec 문자열

struct TofPostureConfig {
  // --- 입력 ---
  bool row0_is_top = true;           // false 면 위아래를 뒤집어 해석
  bool accept_semi_valid = true;     // 상태값 6·10(대체로 유효)도 받기. 5·9 는 항상 받음

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

  // --- 자세 분류 (기준선 대비) ---
  float lean_forward_mm = 80.0f;     // 머리 거리 감소 ≥ 이 값 → lean_forward
  float lean_back_mm = 100.0f;       // 머리 거리 증가 ≥ 이 값 → lean_back
  float slouch_head_drop = 0.20f;    // 머리 꼭대기 행이 격자 높이의 이 비율 이상 내려가면 slouch.
                                     // 8×8 에서는 2행(0.29), 54×42 에서는 9행
  uint32_t posture_hold_ms = 1000;   // 후보 자세가 이만큼 유지돼야 확정
  uint32_t confidence_tau_ms = 2000; // posture_confidence EMA 시정수
  uint32_t change_window_ms = 60000; // posture_change_rate 집계 창

  // --- 노딩 ---
  uint32_t nod_trend_tau_ms = 3000;  // 머리 거리의 느린 추세 EMA 시정수
  float nod_amp_mm = 30.0f;          // 추세보다 이만큼 가까워지면 숙임 1회
  uint32_t nod_window_ms = 20000;    // nod_rate_hz 집계 창
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

  // ---- 진단 ----
  bool baseline_ready = false;
  float head_depth_mm = 0.0f;       // 머리 영역 거리 중앙값
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

  bool accepted(int16_t d, uint8_t st) const;
  void filterZones(const TofFrameView &frame);
  uint16_t segment();
  void computeGeometry(uint16_t fg_count);
  void updateMotion(float dt_s);
  void updatePresence(bool raw_present, uint32_t now_ms);
  void updateBackground(uint32_t now_ms);
  void updateBaseline(uint32_t now_ms);
  void classify(uint32_t now_ms, float dt_s);
  void updateNod(uint32_t now_ms, float dt_s);
  void commitPosture(TofPosture p, uint32_t now_ms);
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

  // 자세 확정
  TofPosture candidate_ = TofPosture::Away;
  uint32_t candidate_since_ms_ = 0;
  static constexpr uint8_t kChangeRing = 32;
  uint32_t change_ts_[kChangeRing];
  uint8_t change_n_ = 0;
  uint8_t change_head_ = 0;

  // 노딩
  bool nod_trend_init_ = false;
  float nod_trend_ = 0.0f;
  bool nod_armed_ = true;
  uint32_t nod_track_start_ms_ = 0;
  bool nod_tracking_ = false;
  static constexpr uint8_t kNodRing = 32;
  uint32_t nod_ts_[kNodRing];
  uint8_t nod_n_ = 0;
  uint8_t nod_head_ = 0;
};

}  // namespace deskmate
