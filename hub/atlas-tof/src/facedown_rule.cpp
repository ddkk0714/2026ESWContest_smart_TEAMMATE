#include "facedown_rule.h"

#include <algorithm>
#include <vector>

namespace tof {
namespace {

inline bool nearCell(int16_t d, uint8_t s, const FaceDownParams& p) {
    return (s == 5 || s == 9) && d > p.near_min_mm && d < p.near_max_mm;
}

// numpy.median: 정렬 뒤 홀수면 가운데, 짝수면 가운데 둘의 평균
double medianMm(std::vector<int>& v) {
    std::sort(v.begin(), v.end());
    const size_t n = v.size();
    if (n % 2) return v[n / 2];
    return (static_cast<double>(v[n / 2 - 1]) + static_cast<double>(v[n / 2])) / 2.0;
}

}  // namespace

FaceDownFeatures faceDownFeatures(const int16_t* dA, const uint8_t* sA, const int16_t* dB, const uint8_t* sB, const FaceDownParams& p) {
    FaceDownFeatures f;
    std::vector<int> lo_d, up_d;
    const int16_t* ds[2] = {dA, dB};
    const uint8_t* ss[2] = {sA, sB};
    int top[2] = {8, 8};
    for (int k = 0; k < 2; ++k) {
        for (int r = 0; r < 8; ++r) {
            int row_near = 0;
            for (int c = 0; c < 8; ++c) {
                const int i = r * 8 + c;
                if (!nearCell(ds[k][i], ss[k][i], p)) continue;
                ++row_near;
                if (r < 4) { ++f.up; up_d.push_back(ds[k][i]); }
                else { ++f.lo; lo_d.push_back(ds[k][i]); }
            }
            if (row_near >= 2 && top[k] == 8) top[k] = r;
        }
    }
    f.topA = top[0]; f.topB = top[1];
    // A 의 아래 near 거리 뒤에 B 의 것이 이어진다(np.r_) — 중앙값은 순서와 무관
    if (!lo_d.empty()) { f.has_dlo = true; f.dlo = medianMm(lo_d) / 1000.0; }
    if (!up_d.empty()) { f.has_dup = true; f.dup = medianMm(up_d) / 1000.0; }
    bool flag = false;
    if (f.has_dlo) {
        const bool low = f.up <= p.up_max && f.lo >= p.lo_min && f.dlo <= p.dlo_max_m && f.topA >= p.top_min && f.topB >= p.top_min;
        const bool near = f.lo >= p.near_lo_min && f.dlo <= p.near_dlo_max_m && (!f.has_dup || f.dup <= p.near_dup_max_m);
        flag = low || near;
    }
    f.flag = flag;
    f.seated = f.up >= p.seated_up_min && f.topA <= p.seated_top_max && f.topB <= p.seated_top_max;
    return f;
}

void FaceDownRule::reset() {
    win_.clear();
    on_ = false;
    share_ = 0.0;
    last_t_ = -1.0;
    last_ = FaceDownFeatures{};
}

FaceDownFeatures FaceDownRule::update(double t_s, const int16_t* dA, const uint8_t* sA, const int16_t* dB, const uint8_t* sB) {
    const FaceDownFeatures f = faceDownFeatures(dA, sA, dB, sB, p_);
    win_.emplace_back(t_s, f.flag);
    while (win_.front().first < t_s - p_.window_s) win_.pop_front();
    int flagged = 0;
    for (const auto& w : win_) flagged += w.second ? 1 : 0;
    share_ = static_cast<double>(flagged) / static_cast<double>(win_.size());
    on_ = share_ >= (on_ ? p_.off_share : p_.on_share);
    ++pairs_;
    last_t_ = t_s;
    last_ = f;
    return f;
}

}  // namespace tof
