#include "TofPosture.h"

#include <math.h>
#include <stdlib.h>

#include <algorithm>

namespace deskmate {

namespace {

// 프레임 간격이 크게 벌어져도(로그 재생·일시정지) EMA 가 한 번에 튀지 않게 묶는다
constexpr float kMaxDtS = 1.0f;

float emaAlpha(float dt_s, uint32_t tau_ms) {
  if (tau_ms == 0) return 1.0f;
  return 1.0f - expf(-dt_s * 1000.0f / (float)tau_ms);
}

int16_t median3(int16_t a, int16_t b, int16_t c) {
  return std::max(std::min(a, b), std::min(std::max(a, b), c));
}

float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

bool isPresentPosture(TofPosture p) {
  return p != TofPosture::Away && p != TofPosture::Unknown;
}

}  // namespace

const char *tofPostureName(TofPosture p) {
  switch (p) {
    case TofPosture::Upright: return "upright";
    case TofPosture::LeanForward: return "lean_forward";
    case TofPosture::LeanBack: return "lean_back";
    case TofPosture::Slouch: return "slouch";
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
  nod_trend_init_ = false;
  nod_armed_ = true;
  nod_tracking_ = false;
  nod_n_ = 0;
  nod_head_ = 0;
}

void TofPostureEstimator::resetBaseline() {
  baseline_ready_ = false;
  calib_active_ = false;
  calib_n_ = 0;
  feat_.baseline_ready = false;
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
  classify(now_ms, dt_s);
  updateNod(now_ms, dt_s);

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
  feat_.baseline_ready = baseline_ready_;
}

void TofPostureEstimator::classify(uint32_t now_ms, float dt_s) {
  feat_.baseline_ready = baseline_ready_;
  feat_.head_delta_valid = false;
  feat_.baseline_deviation = 0.0f;

  TofPosture cand;
  bool immediate = false;
  if (!feat_.present) {
    cand = TofPosture::Away;  // 재실 쪽에서 이미 away_ms 동안 확인했다
    immediate = true;
  } else if (!baseline_ready_) {
    cand = TofPosture::Unknown;
    immediate = feat_.posture == TofPosture::Away;
  } else if (!feat_.geometry_valid) {
    cand = feat_.posture;  // 근거가 잠깐 없으면 현재 판정 유지
  } else {
    const float delta = feat_.head_depth_mm - base_head_depth_;
    const float drop = feat_.head_row_index - base_head_row_;  // 양수 = 머리가 내려감
    feat_.head_delta_valid = true;
    feat_.head_delta_mm = delta;

    float dev = 0.0f;
    if (cfg_.lean_forward_mm > 0.0f) dev = std::max(dev, -delta / cfg_.lean_forward_mm);
    if (cfg_.lean_back_mm > 0.0f) dev = std::max(dev, delta / cfg_.lean_back_mm);
    if (cfg_.slouch_head_drop > 0.0f) dev = std::max(dev, drop / cfg_.slouch_head_drop);
    feat_.baseline_deviation = dev;

    // 엎드림은 머리가 내려가면서 가까워지므로 높이 판정을 먼저 본다
    if (drop >= cfg_.slouch_head_drop) cand = TofPosture::Slouch;
    else if (delta <= -cfg_.lean_forward_mm) cand = TofPosture::LeanForward;
    else if (delta >= cfg_.lean_back_mm) cand = TofPosture::LeanBack;
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
  // 자세 전환 빈도는 사람이 있을 때의 자세끼리만 센다
  if (isPresentPosture(feat_.posture) && isPresentPosture(p)) {
    change_ts_[change_head_] = now_ms;
    change_head_ = (uint8_t)((change_head_ + 1) % kChangeRing);
    if (change_n_ < kChangeRing) change_n_++;
  }
  feat_.posture = p;
}

// 머리 거리가 느린 추세보다 nod_amp_mm 이상 가까워질 때마다 숙임 1회.
// 다시 추세 근처(절반)로 돌아와야 다음 숙임을 센다.
void TofPostureEstimator::updateNod(uint32_t now_ms, float dt_s) {
  if (!feat_.present) {
    nod_tracking_ = false;
    nod_trend_init_ = false;
    nod_armed_ = true;
    nod_n_ = 0;
    feat_.nod_rate_valid = false;
    feat_.nod_rate_hz = 0.0f;
    return;
  }
  if (!nod_tracking_) {
    nod_tracking_ = true;
    nod_track_start_ms_ = now_ms;
  }

  if (feat_.geometry_valid) {
    const float x = feat_.head_depth_mm;
    if (!nod_trend_init_) {
      nod_trend_ = x;
      nod_trend_init_ = true;
    } else if (dt_s > 0.0f) {
      nod_trend_ += emaAlpha(dt_s, cfg_.nod_trend_tau_ms) * (x - nod_trend_);
    }
    const float dev = x - nod_trend_;
    if (nod_armed_ && dev <= -cfg_.nod_amp_mm) {
      nod_ts_[nod_head_] = now_ms;
      nod_head_ = (uint8_t)((nod_head_ + 1) % kNodRing);
      if (nod_n_ < kNodRing) nod_n_++;
      nod_armed_ = false;
    } else if (!nod_armed_ && dev >= -cfg_.nod_amp_mm * 0.5f) {
      nod_armed_ = true;
    }
  }

  const uint32_t tracked = now_ms - nod_track_start_ms_;
  feat_.nod_rate_valid = cfg_.nod_window_ms > 0 && tracked >= cfg_.nod_window_ms / 2;
  if (!feat_.nod_rate_valid) {
    feat_.nod_rate_hz = 0.0f;
    return;
  }
  const uint32_t span = std::min(tracked, cfg_.nod_window_ms);
  uint8_t recent = 0;
  for (uint8_t i = 0; i < nod_n_; i++) {
    const uint32_t ts = nod_ts_[(nod_head_ + kNodRing - 1 - i) % kNodRing];
    if (now_ms - ts > span) break;
    recent++;
  }
  feat_.nod_rate_hz = (float)recent * 1000.0f / (float)span;
}

}  // namespace deskmate
