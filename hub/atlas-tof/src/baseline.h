// 설치 기준(베이스라인)과 "장치 이동" 규칙 — misplaced 의 10-17 정의 (rpi4_tof_deploy_20261017/PLACEMENT_BASELINE_NOISE_PROCESS.md §1).
//
// 모델은 시뮬에서 "장치가 이상하게 놓인 장면"을 misplaced 로 배웠는데, 실기(10-26 왼쪽 자리)에서는 엎드림·졸음·턱괴기, 엎드림 뒤
// 바르게까지 대부분 misplaced 로 나왔다. 제품에서 장치 위치는 설치 뒤 거의 안 바뀌므로, 설치 때 센서별 8×8 베이스라인(칸별 중앙값·
// 표준편차·유효 여부)을 한 번 잡고 그 뒤로는 **베이스라인 대비 변화**로만 장치 이동을 본다. 표시되는 misplaced 는
// "모델 misplaced AND 이 규칙이 hold_s 동안 참" 일 때만이고, 규칙이 거짓이면 misplaced 를 뺀 나머지에서 가장 큰 라벨을 낸다.
//
// 규칙(센서별, 프레임마다): 배경 칸 = 베이스라인이 유효하고 거리 ≥ bg_min_mm(1200) 인 칸. 그중 **지금도 유효한** 칸만 평가하고
//   |d − B| > max(moved_min_mm 80, moved_sigma 4 × σ_zone) 이면서 "사람이 가린 것"(d < B 이고 d ≤ person_max_mm)이 아닌 칸의 비율 = moved_frac.
// 두 센서 중 최대가 moved_frac_min(0.5) 이상이 hold_s(10) 동안 이어지면 device_moved. 평가 칸이 min_eval_zones(4) 미만이면 판단 보류(-1).
// 사람은 배경 칸을 가려도 "더 가까워질" 뿐이라 안 센다. 장치가 움직이면 배경 칸이 다른 거리로 바뀐다(다른 벽·가구가 보임).
// 무효가 된 칸(lost)은 세지 않는다 — 10-26 실측(왼쪽 자리, 벽 3~4 m)에서 사람이 앉으면 먼 벽 칸의 70~90 % 가 무효로 떨어져(신호 약함)
// lost 를 세면 장치가 가만히 있어도 규칙이 20 % 구간에서 발화했다. 멀어짐·사람 범위 밖으로 가까워짐은 그 녹화 전체에서 0 이었다.
// 한계: 기울어져 배경 칸이 책상·바닥(사람 범위 안)만 보게 되는 이동은 이 규칙이 못 잡는다(그때 모델 misplaced 는 억제된 채로 남는다).
#ifndef DESKMATE_TOF_BASELINE_H
#define DESKMATE_TOF_BASELINE_H

#include <cstdint>
#include <string>
#include <vector>

#include "tof_line.h"

namespace tof {

struct MovedRuleParams {
    int bg_min_mm = 1200;        // 배경 칸 = 베이스라인 거리가 이 이상
    int moved_min_mm = 80;       // 변화 문턱 하한
    float moved_sigma = 4.f;     // 변화 문턱 = max(moved_min_mm, moved_sigma × σ_zone)
    int person_max_mm = 1300;    // 이보다 가까워진 변화는 사람이 가린 것으로 보고 안 센다
    float moved_frac_min = 0.5f; // 평가한 배경 칸 중 변한 비율이 이 이상이면 후보
    int hold_s = 10;             // 후보가 이 시간 동안 이어지면 device_moved
    int min_eval_zones = 4;      // 지금 유효한 배경 칸이 이보다 적으면 판단 보류
};

struct SensorBaseline {
    bool has = false;
    int id = -1;                 // 물리 센서 번호(없으면 자리 번호)
    int w = 0, h = 0;
    int16_t median_mm[kMaxZones] = {};
    float std_mm[kMaxZones] = {};
    uint8_t valid[kMaxZones] = {};   // 캡처 프레임의 절반 이상에서 유효했던 칸
    int frames = 0;
    int bg_zones = 0;            // 배경 칸 수 (valid ∧ median ≥ bg_min_mm) — 저장할 때 계산해 둔다
    float sigma_mm = 0.f;        // 유효 칸 std 의 중앙값 (문서 §2-4 의 σ_noise)
};

struct Baseline {
    std::vector<SensorBaseline> sensors;
    int64_t captured_at_ms = -1; // 캡처가 끝난 단조 시각(서비스 안에서만 의미), 파일에는 epoch 초도 같이
    double captured_epoch_s = 0.0;
    std::string source;          // "post" | "auto" | "file"
    bool any() const { for (const auto& s : sensors) if (s.has) return true; return false; }
    const SensorBaseline* forId(int id) const { for (const auto& s : sensors) if (s.has && s.id == id) return &s; return nullptr; }
};

// 캡처 누적기: 프레임을 모아 칸별 중앙값·표준편차·유효 비율
struct BaselineAccumulator {
    int w = 0, h = 0;
    std::vector<std::vector<int16_t>> samples;   // [zone][frame] 유효값만
    int frames = 0;
    void reset(int w_, int h_);
    void add(const TofFrame& f);                 // 모델 방향(뒤집기 뒤)의 프레임
    bool finish(SensorBaseline& out, int bg_min_mm) const;   // frames == 0 이면 false
};

// 프레임 하나의 이동 비율(0~1) = changed / (bg − lost). 평가할 배경 칸이 min_eval_zones 미만이면 -1.
// changed = 변한 칸, lost = 지금 무효인 배경 칸(세지 않음), bg = 배경 칸 수.
float movedFraction(const SensorBaseline& b, const TofFrame& f, const MovedRuleParams& p, int* changed = nullptr, int* lost = nullptr,
                    int* bg = nullptr);

// 파일: {"schema":"deskmate/tof_baseline/1","captured_epoch_s":…,"source":…,"sensors":[{"id","w","h","frames","sigma_mm","bg_zones",
//         "median_mm":[…],"std_mm":[…],"valid":[…]}]}  (tmp+rename 으로 쓴다)
bool saveBaseline(const std::string& path, const Baseline& b, std::string* err = nullptr);
bool loadBaseline(const std::string& path, Baseline& out, std::string* err = nullptr);
std::string baselineJson(const Baseline& b, bool with_grids);

}  // namespace tof

#endif  // DESKMATE_TOF_BASELINE_H
