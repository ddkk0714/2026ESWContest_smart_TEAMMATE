// 녹화·시뮬레이션 프레임을 펌웨어 규칙 추정기(TofPosture.cpp, 수정 없이 그대로)에 넣어
// 프레임별 판정을 낸다. 보드 없이 규칙을 채점·튜닝하는 용도.
//
// 입력(stdin), 프레임마다 한 줄:  ms w h d0 .. d(n-1) st0 .. st(n-1)
//   `cmd b` / `cmd g` / `cmd r` 줄은 수집 펌웨어의 USB 명령과 같다(기준선·배경·초기화). 출력 없음.
//   d 는 mm(-1 = 타깃 없음), 장면 방향(row 0 = 위, col 은 센서가 볼 때 왼→오른쪽).
// 출력(stdout), 프레임마다 한 줄:
//   posture conf baseline present head_mm torso_mm drop_mm chin_ratio nods motion
//   (값이 무효면 nan)
// 인자: TofPostureConfig 필드=값 (예: lean_forward_mm=60 posture_hold_ms=1000)
//
// 빌드: g++ -O2 -std=gnu++17 -I <firmware>/src tof_replay.cpp <firmware>/src/preprocess/tof/TofPosture.cpp
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "preprocess/tof/TofPosture.h"

using namespace deskmate;

namespace {

bool setField(TofPostureConfig &c, const std::string &key, double v) {
#define F(name)                                   \
  if (key == #name) {                             \
    c.name = static_cast<decltype(c.name)>(v);    \
    return true;                                  \
  }
  F(accept_semi_valid) F(fov_vertical_deg) F(hold_frames) F(min_person_mm) F(max_person_mm)
  F(bg_margin_mm) F(bg_learn_after_ms) F(bg_alpha) F(present_min_coverage) F(present_on_ms)
  F(away_ms) F(head_row_min_fill) F(head_band_ratio) F(motion_deadband_mm) F(motion_tau_ms)
  F(motion_full_mm_s) F(baseline_still_motion) F(baseline_ms) F(baseline_reset_away_ms)
  F(recline_ratio_on) F(recline_ratio_off) F(face_down_drop_on_mm) F(face_down_drop_off_mm)
  F(lean_forward_mm) F(chin_arm_margin_mm) F(chin_arm_rows_on) F(chin_arm_rows_off)
  F(chin_arm_min_rows) F(posture_hold_ms) F(confidence_tau_ms) F(change_window_ms)
  F(nod_amp_mm) F(nod_min_peak_mm) F(nod_lookback_ms) F(nod_fall_max_ms) F(nod_recover_frac)
  F(nod_recover_ms) F(nod_max_ms) F(nod_body_mm) F(nod_window_ms) F(nod_count)
  F(drowsy_still_ms) F(drowsy_hold_ms) F(nod_rate_min_track_ms)
#undef F
  return false;
}

}  // namespace

int main(int argc, char **argv) {
  TofPostureConfig cfg;
  for (int i = 1; i < argc; ++i) {
    const char *eq = std::strchr(argv[i], '=');
    if (!eq || !setField(cfg, std::string(argv[i], eq - argv[i]), std::atof(eq + 1))) {
      std::fprintf(stderr, "unknown config: %s\n", argv[i]);
      return 2;
    }
  }
  static TofPostureEstimator est(cfg);  // 54×42 까지 들어가는 상태 배열이 커서 정적으로 둔다

  std::vector<int16_t> d;
  std::vector<uint8_t> st;
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.empty()) continue;
    if (line.rfind("cmd ", 0) == 0 && line.size() > 4) {
      if (line[4] == 'b') est.resetBaseline();
      if (line[4] == 'g') est.requestBackgroundCapture();
      if (line[4] == 'r') est.reset();
      continue;
    }
    std::istringstream in(line);
    long ms;
    int w, h;
    if (!(in >> ms >> w >> h) || w <= 0 || h <= 0) {
      std::fprintf(stderr, "bad line\n");
      return 3;
    }
    const int n = w * h;
    d.resize(n);
    st.resize(n);
    for (int i = 0; i < n; ++i) {
      int v;
      in >> v;
      d[i] = static_cast<int16_t>(v);
    }
    for (int i = 0; i < n; ++i) {
      int v;
      in >> v;
      st[i] = static_cast<uint8_t>(v);
    }
    TofFrameView view;
    view.width = static_cast<uint8_t>(w);
    view.height = static_cast<uint8_t>(h);
    view.distance_mm = d.data();
    view.status = st.data();
    est.update(view, static_cast<uint32_t>(ms));
    const TofFeatures &f = est.features();
    const double nan = std::nan("");
    std::printf("%s %.3f %d %d %.1f %.1f %.1f %.3f %u %.3f\n", tofPostureName(f.posture),
                f.posture_confidence, f.baseline_ready ? 1 : 0, f.present ? 1 : 0,
                f.geometry_valid ? f.head_depth_mm : nan, f.torso_valid ? f.torso_depth_mm : nan,
                (f.baseline_ready && f.geometry_valid) ? f.head_drop_mm : nan, f.chin_arm_ratio,
                f.nods_in_window, f.motion_score);
  }
  return 0;
}
