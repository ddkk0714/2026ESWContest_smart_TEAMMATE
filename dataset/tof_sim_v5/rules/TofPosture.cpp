#include "TofPosture.h"

#include <math.h>
#include <stdlib.h>

#include <algorithm>

namespace deskmate {

namespace {

// 프레임 간격이 크게 벌어져도(로그 재생·일시정지) EMA 가 한 번에 튀지 않게 묶는다
constexpr float kMaxDtS = 1.0f;
constexpr float kPi = 3.14159265f;

// 끄덕임 신호 평활에 쓰는 이동 중앙값 길이. depth_teacher smoothing.median_frames 와 같다.
// 머리 꼭대기 행이 경계에서 한 프레임씩 깜빡이는 것을 끄덕임으로 세지 않게 한다.
constexpr uint8_t kNodMedianN = 5;
// 낙하가 이보다 빠르면 행 깜빡임으로 본다. 실측 끄덕임은 0.5~1.5 s 에 떨어진다.
constexpr uint32_t kNodFallMinMs = 200;

float emaAlpha(float dt_s, uint32_t tau_ms) {
  if (tau_ms == 0) return 1.0f;
  return 1.0f - expf(-dt_s * 1000.0f / (float)tau_ms);
}

int16_t median3(int16_t a, int16_t b, int16_t c) {
  return std::max(std::min(a, b), std::min(std::max(a, b), c));
}

float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

// 자세 전환 빈도는 사람이 앉아 취하는 자세끼리만 센다(졸음·자리비움 제외)
bool isBodyPosture(TofPosture p) {
  return p != TofPosture::Away && p != TofPosture::Unknown && p != TofPosture::Drowsy;
}

}  // namespace

const char *tofPostureName(TofPosture p) {
  switch (p) {
    case TofPosture::Upright: return "upright";
    case TofPosture::LeanForward: return "lean_forward";
    case TofPosture::Recline: return "recline";
    case TofPosture::FaceDown: return "face_down";
    case TofPosture::ChinRest: return "chin_rest";
    case TofPosture::Drowsy: return "drowsy";
    case TofPosture::Away: return "away";
    case TofPosture::Unknown:
    default: return "unknown";
  }
}

TofPostureEstimator::TofPostureEstimator(const TofPostureConfig &cfg) : cfg_(cfg) { reset(); }

void TofPostureEstimator::reset() {
  for (uint16_t z = 0; z < kTofMaxZones; z++) {
    ZoneState &s = zs_[z];
    s.hist_n = 0;
    s.age = 255;
    s.filt = -1;
    s.prev = -1;
    s.bg = -1;
    fg_[z] = 0;
  }
  bg_capture_pending_ = false;
  feat_ = TofFeatures();
  feat_.grid_width = w_;
  feat_.grid_height = h_;
  have_last_ts_ = false;
  raw_present_ = false;
  raw_since_ms_ = 0;
  away_since_ms_ = 0;
  motion_ema_ = 0.0f;
  resetBaseline();
  candidate_ = TofPosture::Away;
  candidate_since_ms_ = 0;
  change_n_ = 0;
  change_head_ = 0;
  resetNod();
}

void TofPostureEstimator::resetBaseline() {
  baseline_ready_ = false;
  calib_active_ = false;
  calib_n_ = 0;
  was_dropped_ = was_chin_ = was_receded_ = false;
  feat_.baseline_ready = false;
  resetNod();
}

void TofPostureEstimator::resetNod() {
  nod_sample_n_ = 0;
  nod_sample_head_ = 0;
  nod_phase_ = NodPhase::Idle;
  nod_armed_ = true;
  held_still_ = false;
  nod_n_ = 0;
  nod_head_ = 0;
  nod_tracking_ = false;
  drowsy_evidence_seen_ = false;
  feat_.drowsy = false;
  feat_.nods_in_window = 0;
  feat_.nod_rate_valid = false;
  feat_.nod_rate_hz = 0.0f;
}

bool TofPostureEstimator::update(const TofFrameView &frame, uint32_t now_ms) {
  if (frame.distance_mm == nullptr || frame.width == 0 || frame.height == 0) return false;
  const uint32_t n = (uint32_t)frame.width * frame.height;
  if (n > kTofMaxZones) return false;

  if (frame.width != w_ || frame.height != h_) {
    w_ = frame.width;
    h_ = frame.height;
    zones_ = (uint16_t)n;
    reset();
  }

  float dt_s = 0.0f;
  if (have_last_ts_) {
    dt_s = (float)(now_ms - last_ts_) / 1000.0f;
    if (dt_s > kMaxDtS) dt_s = kMaxDtS;
  } else {
    // 첫 프레임: 지금부터 비어 있던 것으로 본다
    raw_since_ms_ = now_ms;
    away_since_ms_ = now_ms;
  }
  have_last_ts_ = true;
  last_ts_ = now_ms;

  filterZones(frame);

  if (bg_capture_pending_) {
    for (uint16_t z = 0; z < zones_; z++) zs_[z].bg = zs_[z].filt;
    bg_capture_pending_ = false;
  }

  const uint16_t fg_count = segment();
  feat_.grid_width = w_;
  feat_.grid_height = h_;
  feat_.presence_count = fg_count;
  feat_.coverage_ratio = (float)fg_count / (float)zones_;

  computeGeometry(fg_count);
  updateMotion(dt_s);
  updatePresence(feat_.coverage_ratio >= cfg_.present_min_coverage, now_ms);
  updateBackground(now_ms);
  updateBaseline(now_ms);

  // 기준선 대비 머리 거리·낙차
  feat_.baseline_ready = baseline_ready_;
  feat_.head_delta_valid = baseline_ready_ && feat_.geometry_valid;
  if (feat_.head_delta_valid) {
    feat_.head_delta_mm = feat_.head_depth_mm - base_head_depth_;
    const float span = h_ > 1 ? (float)(h_ - 1) / (float)h_ : 1.0f;
    const float drop_rad = (feat_.head_row_index - base_head_row_) * span * cfg_.fov_vertical_deg * kPi / 180.0f;
    feat_.head_drop_mm = drop_rad * feat_.head_depth_mm;
  }

  updateNod(now_ms);
  classify(now_ms, dt_s);

  for (uint16_t z = 0; z < zones_; z++) zs_[z].prev = zs_[z].filt;
  return true;
}

bool TofPostureEstimator::accepted(int16_t d, uint8_t st) const {
  if (d < 0) return false;
  if (st == 5 || st == 9) return true;
  return cfg_.accept_semi_valid && (st == 6 || st == 10);
}

// 상태값 판정 → 3프레임 중앙값 → 짧은 결측 유지
void TofPostureEstimator::filterZones(const TofFrameView &frame) {
  uint16_t valid = 0;
  for (uint8_t r = 0; r < h_; r++) {
    const uint8_t src_r = cfg_.row0_is_top ? r : (uint8_t)(h_ - 1 - r);
    for (uint8_t c = 0; c < w_; c++) {
      const uint16_t z = (uint16_t)r * w_ + c;
      const uint16_t src = (uint16_t)src_r * w_ + c;
      const int16_t d = frame.distance_mm[src];
      const uint8_t st = frame.status ? frame.status[src] : 5;
      ZoneState &s = zs_[z];
      if (accepted(d, st)) {
        if (s.hist_n < 3) {
          s.hist[s.hist_n++] = d;
        } else {
          s.hist[0] = s.hist[1];
          s.hist[1] = s.hist[2];
          s.hist[2] = d;
        }
        s.age = 0;
        s.filt = s.hist_n == 3 ? median3(s.hist[0], s.hist[1], s.hist[2]) : d;
      } else {
        if (s.age < 255) s.age++;
        if (s.filt < 0 || s.age > cfg_.hold_frames) {
          s.filt = -1;
          s.hist_n = 0;
        }
      }
      if (s.filt >= 0) valid++;
    }
  }
  feat_.valid_zones = valid;
}

bool TofPostureEstimator::isForeground(uint16_t z) const {
  const ZoneState &s = zs_[z];
  if (s.filt < cfg_.min_person_mm || s.filt > cfg_.max_person_mm) return false;
  return s.bg < 0 || (s.bg - s.filt) >= cfg_.bg_margin_mm;
}

uint16_t TofPostureEstimator::segment() {
  uint16_t count = 0;
  for (uint16_t z = 0; z < zones_; z++) {
    const uint8_t cur = isForeground(z) ? 1 : 0;
    fg_[z] = (uint8_t)(((fg_[z] & 1) << 1) | cur);
    count += cur;
  }
  return count;
}

int16_t TofPostureEstimator::medianOf(uint16_t n) {
  int16_t *mid = scratch_ + n / 2;
  std::nth_element(scratch_, mid, scratch_ + n);
  return *mid;
}

void TofPostureEstimator::computeGeometry(uint16_t fg_count) {
  feat_.geometry_valid = false;
  feat_.shoulder_tilt_valid = false;
  feat_.torso_valid = false;
  feat_.chin_arm_ratio = 0.0f;
  feat_.chin_arm_side = 0;
  if (fg_count == 0 || feat_.coverage_ratio < cfg_.present_min_coverage) return;

  // ② 전경 거리 중앙값
  uint16_t n = 0;
  for (uint16_t z = 0; z < zones_; z++)
    if (fg_[z] & 1) scratch_[n++] = zs_[z].filt;
  feat_.centroid_depth_mm = (float)medianOf(n);

  // ③ 머리 꼭대기 행: 전경이 행 너비의 일정 비율 이상인 첫 행
  const uint16_t min_fill = std::max<uint16_t>(1, (uint16_t)ceilf(cfg_.head_row_min_fill * w_));
  int16_t head_row = -1;
  int16_t first_any = -1;
  for (uint8_t r = 0; r < h_ && head_row < 0; r++) {
    uint16_t cnt = 0;
    for (uint8_t c = 0; c < w_; c++) cnt += fg_[(uint16_t)r * w_ + c] & 1;
    if (cnt > 0 && first_any < 0) first_any = r;
    if (cnt >= min_fill) head_row = r;
  }
  if (head_row < 0) head_row = first_any;
  if (head_row < 0) return;
  feat_.head_row_index = h_ > 1 ? (float)head_row / (float)(h_ - 1) : 0.0f;

  // 머리 영역 거리 중앙값
  const uint8_t band = std::max<uint8_t>(1, (uint8_t)lroundf(cfg_.head_band_ratio * h_));
  const uint8_t band_end = (uint8_t)std::min<int>(h_, head_row + band);
  n = 0;
  for (uint8_t r = (uint8_t)head_row; r < band_end; r++)
    for (uint8_t c = 0; c < w_; c++) {
      const uint16_t z = (uint16_t)r * w_ + c;
      if (fg_[z] & 1) scratch_[n++] = zs_[z].filt;
    }
  if (n == 0) return;
  feat_.head_depth_mm = (float)medianOf(n);
  feat_.geometry_valid = true;
  if (band_end >= h_) return;  // 머리 밑 상체가 화면에 없음

  // 머리 밑 상체 거리. 의자 등받이처럼 머리보다 한참 뒤에 있는 것은 뺀다
  const int16_t torso_far = (int16_t)(feat_.head_depth_mm + 250.0f);
  n = 0;
  for (uint8_t r = band_end; r < h_; r++)
    for (uint8_t c = 0; c < w_; c++) {
      const uint16_t z = (uint16_t)r * w_ + c;
      if ((fg_[z] & 1) && zs_[z].filt <= torso_far) scratch_[n++] = zs_[z].filt;
    }
  if (n > 0) {
    feat_.torso_depth_mm = (float)medianOf(n);
    // 팔 기둥의 기준은 가슴이다. 팔이 상체 zone 을 많이 가려 중앙값이 앞으로
    // 끌려와도 가슴을 잡도록, 뒤쪽 75% 지점을 쓴다
    int16_t *q = scratch_ + (n * 3) / 4;
    std::nth_element(scratch_, q, scratch_ + n);
    const int16_t chest = *q;
    feat_.torso_valid = true;

    // 턱괴기 팔 기둥: 머리 바로 아래 행부터 끊김 없이 가슴보다 가까운 열
    const uint8_t torso_rows = (uint8_t)(h_ - band_end);
    const int16_t arm_far = (int16_t)(chest - cfg_.chin_arm_margin_mm);
    uint8_t best = 0;
    int16_t best_c = -1;
    for (uint8_t c = 0; c < w_; c++) {
      uint8_t run = 0;
      for (uint8_t r = band_end; r < h_; r++) {
        const uint16_t z = (uint16_t)r * w_ + c;
        if ((fg_[z] & 1) && zs_[z].filt <= arm_far) run++;
        else break;
      }
      if (run > best) {
        best = run;
        best_c = c;
      }
    }
    if (best >= cfg_.chin_arm_min_rows && best_c >= 0) {
      feat_.chin_arm_ratio = (float)best / (float)torso_rows;
      // 짝수 너비에는 정가운데 열이 없으므로 가운데에서 조금이라도 비켜나면 그쪽이다
      const float off = ((float)best_c + 0.5f) / (float)w_ - 0.5f;
      feat_.chin_arm_side = off < -1e-3f ? -1 : (off > 1e-3f ? 1 : 0);
    }
  }

  // ④ 머리 아래 상체의 좌우 거리 차 (홀수 너비면 가운데 열은 제외)
  const uint8_t left_end = w_ / 2;
  const uint8_t right_begin = (uint8_t)((w_ + 1) / 2);
  uint16_t nl = 0;
  for (uint8_t r = band_end; r < h_; r++)
    for (uint8_t c = 0; c < left_end; c++) {
      const uint16_t z = (uint16_t)r * w_ + c;
      if (fg_[z] & 1) scratch_[nl++] = zs_[z].filt;
    }
  if (nl == 0) return;
  const int16_t left = medianOf(nl);
  uint16_t nr = 0;
  for (uint8_t r = band_end; r < h_; r++)
    for (uint8_t c = right_begin; c < w_; c++) {
      const uint16_t z = (uint16_t)r * w_ + c;
      if (fg_[z] & 1) scratch_[nr++] = zs_[z].filt;
    }
  if (nr == 0) return;
  feat_.shoulder_tilt_mm = (float)(medianOf(nr) - left);
  feat_.shoulder_tilt_valid = true;
}

// ⑤ 두 프레임 연속 전경인 zone 의 거리 변화만 센다.
//    사람이 막 들어온 zone 은 배경→사람으로 크게 바뀌어 움직임이 부풀기 때문이다.
void TofPostureEstimator::updateMotion(float dt_s) {
  float rate = 0.0f;
  if (dt_s > 0.0f) {
    float sum = 0.0f;
    uint16_t cnt = 0;
    for (uint16_t z = 0; z < zones_; z++) {
      if (fg_[z] != 3) continue;
      const ZoneState &s = zs_[z];
      if (s.prev < 0 || s.filt < 0) continue;
      const int diff = abs(s.filt - s.prev) - cfg_.motion_deadband_mm;
      if (diff > 0) sum += (float)diff;
      cnt++;
    }
    if (cnt > 0) rate = sum / (float)cnt / dt_s;
    motion_ema_ += emaAlpha(dt_s, cfg_.motion_tau_ms) * (rate - motion_ema_);
  }
  feat_.motion_indicator_mm_s = motion_ema_;
  feat_.motion_score = cfg_.motion_full_mm_s > 0.0f ? clamp01(motion_ema_ / cfg_.motion_full_mm_s) : 0.0f;
}

void TofPostureEstimator::updatePresence(bool raw, uint32_t now_ms) {
  if (raw != raw_present_) {
    raw_present_ = raw;
    raw_since_ms_ = now_ms;
  }
  if (raw_present_) {
    if (!feat_.present && now_ms - raw_since_ms_ >= cfg_.present_on_ms) feat_.present = true;
  } else if (feat_.present && now_ms - raw_since_ms_ >= cfg_.away_ms) {
    feat_.present = false;
    away_since_ms_ = raw_since_ms_;  // 실제로 사라진 시각
  }
}

// 자리 비움이 이어지는 동안 보이는 것을 배경으로 천천히 배운다
void TofPostureEstimator::updateBackground(uint32_t now_ms) {
  if (feat_.present || raw_present_) return;
  if (now_ms - away_since_ms_ < cfg_.bg_learn_after_ms) return;
  for (uint16_t z = 0; z < zones_; z++) {
    ZoneState &s = zs_[z];
    if (s.filt < 0) continue;
    if (s.bg < 0) {
      s.bg = s.filt;
      continue;
    }
    const float step = cfg_.bg_alpha * (float)(s.filt - s.bg);
    int16_t inc = (int16_t)lroundf(step);
    if (inc == 0 && s.filt != s.bg) inc = s.filt > s.bg ? 1 : -1;  // 정수 반올림으로 멈추지 않게
    s.bg = (int16_t)(s.bg + inc);
  }
}

// 앉은 뒤 처음 조용한 구간의 머리 거리·높이를 "바른 자세" 로 잡는다
void TofPostureEstimator::updateBaseline(uint32_t now_ms) {
  if (!feat_.present) {
    calib_active_ = false;
    if (baseline_ready_ && now_ms - away_since_ms_ >= cfg_.baseline_reset_away_ms) resetBaseline();
    return;
  }
  if (baseline_ready_) return;

  const bool still = feat_.geometry_valid && feat_.motion_score <= cfg_.baseline_still_motion;
  if (!still) {
    calib_active_ = false;
    return;
  }
  if (!calib_active_) {
    calib_active_ = true;
    calib_start_ms_ = now_ms;
    calib_depth_sum_ = 0.0;
    calib_row_sum_ = 0.0;
    calib_n_ = 0;
  }
  calib_depth_sum_ += feat_.head_depth_mm;
  calib_row_sum_ += feat_.head_row_index;
  calib_n_++;
  if (now_ms - calib_start_ms_ >= cfg_.baseline_ms && calib_n_ > 0) {
    base_head_depth_ = (float)(calib_depth_sum_ / calib_n_);
    base_head_row_ = (float)(calib_row_sum_ / calib_n_);
    baseline_ready_ = true;
    calib_active_ = false;
  }
}

// depth_teacher 의 끄덕임 판정을 ToF 로 옮긴 것.
//
//   끄덕임 신호 v = (기준선 머리 거리 − 현재 머리 거리) + 머리 낙차 mm  (5프레임 중앙값)
//   Idle    : 직전 nod_lookback_ms 최저점보다 nod_amp_mm 이상 올라가면(0.2~1.5 s 안에) Falling
//   Falling : 정점을 따라가다가, 떨어진 폭의 nod_recover_frac 만큼 nod_recover_ms 안에
//             되돌아오면 끄덕임 1회. 그사이 몸통이 nod_body_mm 넘게 움직이면 상체 숙임이라 취소.
//             정점 뒤 nod_recover_ms 안에 안 돌아오면 Held(떨군 채)
//   Held    : 떨군 채 몸이 조용히 drowsy_still_ms 를 넘기면 졸음 근거
//   졸음    : 창 안 끄덕임 ≥ nod_count 또는 Held 정지. 근거가 끊겨도 drowsy_hold_ms 유지
void TofPostureEstimator::updateNod(uint32_t now_ms) {
  if (!feat_.present) {
    resetNod();
    return;
  }
  if (!nod_tracking_) {
    nod_tracking_ = true;
    nod_track_start_ms_ = now_ms;
  }

  bool evidence = false;
  if (baseline_ready_ && feat_.geometry_valid) {
    const float raw = (base_head_depth_ - feat_.head_depth_mm) + feat_.head_drop_mm;

    // 5프레임 이동 중앙값: 원신호를 따로 모아 둔다
    static_assert(kNodMedianN <= kNodSampleRing, "ring too small");
    nod_sample_ts_[nod_sample_head_] = now_ms;
    nod_sample_v_[nod_sample_head_] = raw;
    nod_sample_torso_[nod_sample_head_] = feat_.torso_valid ? feat_.torso_depth_mm : -1.0f;
    nod_sample_head_ = (uint8_t)((nod_sample_head_ + 1) % kNodSampleRing);
    if (nod_sample_n_ < kNodSampleRing) nod_sample_n_++;

    float recent[kNodMedianN];
    const uint8_t m = std::min<uint8_t>(kNodMedianN, nod_sample_n_);
    for (uint8_t i = 0; i < m; i++)
      recent[i] = nod_sample_v_[(nod_sample_head_ + kNodSampleRing - 1 - i) % kNodSampleRing];
    std::nth_element(recent, recent + m / 2, recent + m);
    const float v = recent[m / 2];

    // 직전 lookback 의 최저점(가장 든 자세). 원신호 기준이라 약간 보수적이다.
    // 그 시점의 몸통 거리를 함께 잡아 둔다 - 문턱을 넘을 때는 이미 몸이 반쯤
    // 움직인 뒤라, 그때 값을 기준으로 하면 상체 숙임을 끄덕임으로 놓친다
    float min_v = v;
    uint32_t min_t = now_ms;
    float min_torso = feat_.torso_valid ? feat_.torso_depth_mm : -1.0f;
    for (uint8_t i = 0; i < nod_sample_n_; i++) {
      const uint8_t idx = (uint8_t)((nod_sample_head_ + kNodSampleRing - 1 - i) % kNodSampleRing);
      if (now_ms - nod_sample_ts_[idx] > cfg_.nod_lookback_ms) break;
      if (nod_sample_v_[idx] < min_v) {
        min_v = nod_sample_v_[idx];
        min_t = nod_sample_ts_[idx];
        min_torso = nod_sample_torso_[idx];
      }
    }

    const bool torso_moved = nod_torso_ref_valid_ && feat_.torso_valid &&
                             fabsf(feat_.torso_depth_mm - nod_torso_ref_) > cfg_.nod_body_mm;

    switch (nod_phase_) {
      case NodPhase::Idle: {
        // 직전 낙하(끄덕임·취소)가 끝나 신호가 lookback 최저점 근처로 내려와야 다시 받는다.
        // 안 그러면 같은 낙하를 다음 프레임에 또 후보로 잡는다
        if (!nod_armed_) {
          if (v - min_v < 0.5f * cfg_.nod_amp_mm) nod_armed_ = true;
          break;
        }
        const uint32_t fall = now_ms - min_t;
        if (v - min_v >= cfg_.nod_amp_mm && v >= cfg_.nod_min_peak_mm && fall >= kNodFallMinMs &&
            fall <= cfg_.nod_fall_max_ms) {
          nod_phase_ = NodPhase::Falling;
          nod_start_v_ = min_v;
          nod_start_ms_ = min_t;
          nod_peak_v_ = v;
          nod_peak_ms_ = now_ms;
          nod_torso_ref_valid_ = min_torso >= 0.0f;
          nod_torso_ref_ = min_torso;
        }
        break;
      }
      case NodPhase::Falling: {
        if (v > nod_peak_v_) {
          nod_peak_v_ = v;
          nod_peak_ms_ = now_ms;
        }
        const float fell = nod_peak_v_ - nod_start_v_;
        if (torso_moved || now_ms - nod_start_ms_ > cfg_.nod_max_ms) {
          nod_phase_ = NodPhase::Idle;
          nod_armed_ = false;
        } else if (nod_peak_v_ - v >= cfg_.nod_recover_frac * fell &&
                   now_ms - nod_peak_ms_ <= cfg_.nod_recover_ms) {
          nod_ts_[nod_head_] = now_ms;
          nod_head_ = (uint8_t)((nod_head_ + 1) % kNodRing);
          if (nod_n_ < kNodRing) nod_n_++;
          nod_phase_ = NodPhase::Idle;
          nod_armed_ = false;
        } else if (now_ms - nod_peak_ms_ > cfg_.nod_recover_ms) {
          nod_phase_ = NodPhase::Held;
          held_still_ = false;
        }
        break;
      }
      case NodPhase::Held: {
        if (torso_moved || v < nod_start_v_ + 0.5f * cfg_.nod_amp_mm) {
          nod_phase_ = NodPhase::Idle;  // 천천히 들었거나 상체째 움직임
          nod_armed_ = false;
          break;
        }
        // 고개만 앞으로 뺀 채(거북목) 멈춘 것은 떨군 게 아니다. 머리가 실제로
        // 내려가 있어야 졸음 근거로 친다 (depth_teacher 도 '고개 떨군 채 정지')
        const bool head_down = feat_.head_drop_mm >= 0.5f * cfg_.nod_amp_mm;
        if (head_down && feat_.motion_score <= cfg_.baseline_still_motion) {
          if (!held_still_) {
            held_still_ = true;
            held_still_since_ms_ = now_ms;
          } else if (now_ms - held_still_since_ms_ >= cfg_.drowsy_still_ms) {
            evidence = true;
          }
        } else {
          held_still_ = false;
        }
        break;
      }
    }
  }

  uint8_t recent_nods = 0;
  for (uint8_t i = 0; i < nod_n_; i++) {
    const uint32_t ts = nod_ts_[(nod_head_ + kNodRing - 1 - i) % kNodRing];
    if (now_ms - ts > cfg_.nod_window_ms) break;
    recent_nods++;
  }
  feat_.nods_in_window = recent_nods;
  if (cfg_.nod_count > 0 && recent_nods >= cfg_.nod_count) evidence = true;

  if (evidence) {
    drowsy_evidence_seen_ = true;
    drowsy_evidence_ms_ = now_ms;
  }
  feat_.drowsy = drowsy_evidence_seen_ && now_ms - drowsy_evidence_ms_ <= cfg_.drowsy_hold_ms;

  const uint32_t tracked = now_ms - nod_track_start_ms_;
  feat_.nod_rate_valid = baseline_ready_ && cfg_.nod_window_ms > 0 && tracked >= cfg_.nod_rate_min_track_ms;
  if (feat_.nod_rate_valid) {
    const uint32_t span = std::min(tracked, cfg_.nod_window_ms);
    feat_.nod_rate_hz = (float)recent_nods * 1000.0f / (float)span;
  } else {
    feat_.nod_rate_hz = 0.0f;
  }
}

void TofPostureEstimator::classify(uint32_t now_ms, float dt_s) {
  feat_.baseline_deviation = 0.0f;

  TofPosture cand;
  bool immediate = false;
  if (!feat_.present) {
    cand = TofPosture::Away;  // 재실 쪽에서 이미 away_ms 동안 확인했다
    immediate = true;
  } else if (!baseline_ready_) {
    cand = TofPosture::Unknown;
    immediate = feat_.posture == TofPosture::Away;
  } else if (feat_.drowsy) {
    cand = TofPosture::Drowsy;  // 끄덕임 판정이 이미 시간 조건을 거쳤다
    immediate = true;
  } else if (!feat_.geometry_valid) {
    // 근거가 잠깐 없으면 현재 판정 유지. 졸음은 근거가 끊겼으니 놓는다
    cand = feat_.posture == TofPosture::Drowsy ? TofPosture::Unknown : feat_.posture;
  } else {
    const float delta = feat_.head_delta_mm;
    const float ratio = base_head_depth_ > 0.0f ? feat_.head_depth_mm / base_head_depth_ : 1.0f;
    const float drop = feat_.head_drop_mm;

    // camsvc 처럼 세 스위치 모두 히스테리시스를 둔다. 문턱에 걸친 머리나 팔은
    // 매 프레임 라벨을 뒤집어 유지 타이머가 영영 안 찬다.
    was_dropped_ = drop > (was_dropped_ ? cfg_.face_down_drop_off_mm : cfg_.face_down_drop_on_mm);
    was_chin_ = feat_.chin_arm_ratio >= (was_chin_ ? cfg_.chin_arm_rows_off : cfg_.chin_arm_rows_on);
    was_receded_ = ratio > (was_receded_ ? cfg_.recline_ratio_off : cfg_.recline_ratio_on);

    float dev = 0.0f;
    if (cfg_.face_down_drop_on_mm > 0.0f) dev = std::max(dev, drop / cfg_.face_down_drop_on_mm);
    if (cfg_.recline_ratio_on > 1.0f) dev = std::max(dev, (ratio - 1.0f) / (cfg_.recline_ratio_on - 1.0f));
    if (cfg_.lean_forward_mm > 0.0f) dev = std::max(dev, -delta / cfg_.lean_forward_mm);
    if (cfg_.chin_arm_rows_on > 0.0f) dev = std::max(dev, feat_.chin_arm_ratio / cfg_.chin_arm_rows_on);
    feat_.baseline_deviation = dev;

    // 판정 순서는 camsvc 와 같다. 턱괴기는 머리가 아니라 팔의 사실이라 먼저 보되,
    // 머리가 엎드림만큼 내려갔으면 엎드림이다. 젖힘은 멀어진 것만으로 읽는다.
    if (was_chin_ && !was_dropped_) cand = TofPosture::ChinRest;
    else if (was_receded_) cand = TofPosture::Recline;
    else if (was_dropped_) cand = TofPosture::FaceDown;
    else if (delta <= -cfg_.lean_forward_mm) cand = TofPosture::LeanForward;
    else cand = TofPosture::Upright;
  }

  if (cand != candidate_) {
    candidate_ = cand;
    candidate_since_ms_ = now_ms;
  }
  if (cand != feat_.posture && (immediate || now_ms - candidate_since_ms_ >= cfg_.posture_hold_ms)) {
    commitPosture(cand, now_ms);
  }

  const float agree = cand == feat_.posture ? 1.0f : 0.0f;
  if (dt_s > 0.0f) {
    feat_.posture_confidence += emaAlpha(dt_s, cfg_.confidence_tau_ms) * (agree - feat_.posture_confidence);
  }

  // ⑥ 최근 창 안의 자세 전환 횟수
  uint8_t recent = 0;
  for (uint8_t i = 0; i < change_n_; i++) {
    const uint32_t ts = change_ts_[(change_head_ + kChangeRing - 1 - i) % kChangeRing];
    if (now_ms - ts > cfg_.change_window_ms) break;
    recent++;
  }
  feat_.posture_change_rate_per_min =
      cfg_.change_window_ms > 0 ? (float)recent * 60000.0f / (float)cfg_.change_window_ms : 0.0f;
}

void TofPostureEstimator::commitPosture(TofPosture p, uint32_t now_ms) {
  if (isBodyPosture(feat_.posture) && isBodyPosture(p)) {
    change_ts_[change_head_] = now_ms;
    change_head_ = (uint8_t)((change_head_ + 1) % kChangeRing);
    if (change_n_ < kChangeRing) change_n_++;
  }
  feat_.posture = p;
}

}  // namespace deskmate
