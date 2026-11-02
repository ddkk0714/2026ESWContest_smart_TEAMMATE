// 시연용 엎드림(face_down) 규칙 — 두 센서 원본 8×8 격자의 기하로 직접 읽는다 (2026-10-26, 왼쪽 자리).
// PC 기준 구현 dataset/tof_sim_v5/real_facedown_rule.py (fork tools/real-scoring-1026 @ 04527fb) 과 프레임 단위로 같아야 한다.
//
// 왜: 고정 기울기(CAD 20° 위, 센서 책상 위 0.13 m)와 실제 엎드림 자세에서 모델(opt_l8_c256)은 face_down 을 거의 못 낸다
// (자연 거리에서 P ≤ 0.005). 규칙은 몸 위치를 격자에서 바로 읽는다.
//
// 입력: 보드 /raw 가 주는 그대로의 두 격자(펌웨어 0x05 flags 가 이미 적용됨: row 0 = 위), A = 센서 0, B = 센서 1,
// 같은 ts_ms 를 가진 프레임 쌍마다 한 번 평가(5 Hz). 모델 입력용 변환(뒤집기·radial·fill) 전의 원본을 쓴다.
//
// 프레임마다 ("near" 칸 = status 5 또는 9 이고 150 < d < 1200 mm):
//   up    0~3행 near 칸 수 (A+B)         lo   4~7행 near 칸 수 (A+B)
//   topA  A 에서 near 칸이 2개 이상인 첫 행(없으면 8)   topB 같은 것
//   dlo   4~7행 near 칸 거리의 중앙값(m, A·B 합침; numpy 중앙값 = 짝수면 가운데 둘의 평균). 없으면 미정
//   dup   0~3행 같은 것
//   flag  (몸이 아래) up ≤ 4 ∧ lo ≥ 26 ∧ dlo ≤ 0.60 ∧ topA ≥ 4 ∧ topB ≥ 4
//         (몸이 가까움) lo ≥ 30 ∧ dlo ≤ 0.40 ∧ (위쪽 near 없음 ∨ dup ≤ 0.45)
// 판정: 최근 4.0 s(t ≥ now − 4.0, 현재 포함) 프레임 중 flag 비율 share. share ≥ 0.7 에서 켜지고 ≥ 0.4 동안 유지.
// 켜진 동안 서비스는 posture = face_down, conf = TOFPOSTURE_FD_CONF(0.995), present = true 를 낸다(모델 출력은 instant·model_output 에).
//
// 선택(별도 스위치) upright 치환: seated = up ≥ 8 ∧ topA ≤ 3 ∧ topB ≤ 3. 규칙이 꺼져 있고 모델이 misplaced 인데 seated 면 upright.
//
// 10-26 왼�Ͻ 녹화에서 face_down 단계 80/82/82 %, 다른 라벨 단계 오탐 0 %. 사람 1명·배치 1곳(약 0.65 m)·하루치라 dlo ≤ 0.60 등은
// 이 설치의 거리값이다(엎드림 0.45~0.56 m, 뒤로 젖힘 0.71~0.74 m). 책상·의자가 바뀌면 다시 확인.
#ifndef DESKMATE_TOF_FACEDOWN_RULE_H
#define DESKMATE_TOF_FACEDOWN_RULE_H

#include <cstdint>
#include <deque>
#include <utility>

namespace tof {

struct FaceDownParams {
    int near_min_mm = 150;          // near: near_min_mm < d < near_max_mm (경계 제외)
    int near_max_mm = 1200;
    int up_max = 4;                 // 몸이 아래: 0~3행 near 가 이 이하
    int lo_min = 26;                // 몸이 아래: 4~7행 near 가 이 이상
    double dlo_max_m = 0.60;        // 몸이 아래: 아래쪽 거리 중앙값 (문턱은 double — 기준 구현과 경계값이 같게)
    int top_min = 4;                // 몸이 아래: A·B 모두 이 행 위로는 near 2개 이상인 행이 없다
    int near_lo_min = 30;           // 몸이 가까움
    double near_dlo_max_m = 0.40;
    double near_dup_max_m = 0.45;
    double window_s = 4.0;
    double on_share = 0.7;
    double off_share = 0.4;
    int seated_up_min = 8;          // upright 치환
    int seated_top_max = 3;
};

struct FaceDownFeatures {
    int up = 0, lo = 0, topA = 8, topB = 8;
    bool has_dlo = false, has_dup = false;
    double dlo = 0.0, dup = 0.0;    // m
    bool flag = false;
    bool seated = false;
};

// d/st 는 원본 격자 64칸(row-major, row 0 = 위). -1 = 없음, status 255 = 없음.
FaceDownFeatures faceDownFeatures(const int16_t* dA, const uint8_t* sA, const int16_t* dB, const uint8_t* sB, const FaceDownParams& p);

class FaceDownRule {
public:
    explicit FaceDownRule(FaceDownParams p = {}) : p_(p) {}
    const FaceDownParams& params() const { return p_; }
    void reset();
    // 프레임 쌍 하나. t_s = ts_ms / 1000 (보드 시계). 돌려주는 값은 이번 프레임의 특징 + 판정
    FaceDownFeatures update(double t_s, const int16_t* dA, const uint8_t* sA, const int16_t* dB, const uint8_t* sB);
    bool on() const { return on_; }
    double share() const { return share_; }
    uint64_t pairs() const { return pairs_; }
    double lastT() const { return last_t_; }
    const FaceDownFeatures& last() const { return last_; }

private:
    FaceDownParams p_;
    std::deque<std::pair<double, bool>> win_;
    bool on_ = false;
    double share_ = 0.0;
    uint64_t pairs_ = 0;
    double last_t_ = -1.0;
    FaceDownFeatures last_;
};

}  // namespace tof

#endif  // DESKMATE_TOF_FACEDOWN_RULE_H
