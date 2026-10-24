// 장치 하나 = 센서 N개(지금은 2개). 센서마다 PostureNet 을 하나씩 두고(각자 GRU 상태),
// 10 Hz 마다 센서별 최신 프레임으로 한 스텝 돌린 뒤 센서별 확률을 합쳐 자세 하나로 만든다.
//
// 합치는 규칙(FusionRule)은 dataset/tof_sim_v5/eval_pair.py·fit_fusion.py 로 같은 세션을 두 센서 뷰로 재어 골랐다
// (2026-10-14, random 배치, ubai_random 4h 체크포인트, 정확도):
//   24세션: 왼쪽만 0.665 · 오른쪽만 0.615 · softmax 평균 0.705 · log-softmax 합 0.682 · 확신 큰 쪽 0.697 ·
//           "한쪽이 misplaced 면 다른 쪽" 0.617 · 둘 중 하나라도 맞으면(상한) 0.854
//   48세션 4-겹: 왼쪽 0.633 · 오른쪽 0.659 · softmax 평균 0.668 · log-softmax 합 0.718 · 확신 큰 쪽 0.659 ·
//           선형 융합(학습) 0.622
// 72세션 합산으로는 log-softmax 합(확률의 기하평균)이 조금 앞서 기본값이고, softmax 평균은 설정으로 남긴다.
// 둘 다 단일 센서보다 낫고 차이는 세션 편차 안쪽이다. 상한과의 간격(≈0.15)은 규칙으로는 못 줄인다 —
// 두 센서를 한 모델이 같이 보는 구조(학습 변경)의 몫이다.
//
// 스레드: feed() 는 읽기 스레드에서, tick()/stateJson() 은 메인 루프·HTTP 스레드에서 부른다. 내부 mutex.
// 시간은 밖에서 넣는다(now_ms, 단조 시계) — 호스트 테스트가 가상 시계로 돌릴 수 있게.
#ifndef DESKMATE_TOF_DEVICE_POSTURE_H
#define DESKMATE_TOF_DEVICE_POSTURE_H

#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

#include "posture_net.h"
#include "tof_line.h"

namespace tof {

enum class FusionRule { LogProb, MeanProb };   // log-softmax 합(기하평균) / softmax 산술평균

struct DeviceConfig {
    int rate_hz = kModelRateHz;     // 모델 스텝 주기. 학습 프레임률과 같아야 한다
    int stale_ms = 1000;            // 이보다 오래 프레임이 없으면 그 센서는 offline (융합에서 제외, 상태 리셋)
    float smooth_seconds = 1.0f;    // 화면용 완만한 확률(EMA). 0 이면 끔
    FusionRule fusion = FusionRule::LogProb;
    int flip_rows = -1;             // 받은 격자를 위아래로 뒤집기: 1 = 뒤집음, 0 = 안 뒤집음, -1 = 자동(UART 0x05 flags b2, 없으면 안 뒤집음)
    int flip_cols = -1;             // 좌우: 같은 규칙(b3)
    // GRU 은닉 상태 리셋 (PC 시뮬 2026-10-24 정의와 같다. 아래 "리셋" 주석)
    int reset_period_s = 0;         // 마지막 리셋(또는 시작)으로부터 이 시간이 지나면 매 스텝 추론 전에 리셋. 0 = 끔(기본, 아래 "왜")
    int away_reset_s = 60;          // ToF 규칙 재실(coverage)이 이 시간 동안 계속 false 면 리셋, 비어 있는 동안 반복. 0 = 끔
    int present_min_mm = 150;       // 재실 coverage 에 세는 zone: status 5·6·9·10 이고 거리 [min, max] mm
    int present_max_mm = 1300;
    float present_min_coverage = 0.06f;   // 두 센서 중 큰 coverage 가 이 이상이면 재실 (64칸 중 4칸)
};

// 리셋: 모든 센서의 GRU h 를 0 으로, 융합 EMA 를 비움(smooth_argmax=-1 → 다음 스텝은 그 스텝의 융합 확률로 시작),
// 워밍업 없음. 세 가지 계기가 전부 같은 일을 하고 모두 "마지막 리셋 시각"을 갱신한다:
//   periodic  reset_period_s 마다 (사람이 앉아 있어도, 자세 중간이어도)
//   away      재실 규칙이 away_reset_s 동안 false — 재실 근거는 모델 출력 away 가 아니라 ToF coverage.
//             융합 결과 away 를 쓰면 GRU 가 빈 의자를 사람으로 보는 배치에서 리셋이 영영 안 걸린다
//   manual    POST /api/reset
// 왜: PC 시뮬(MuJoCo+GRU, 2026-10-24, 실측 전)에서 배치가 나쁜 장면의 GRU 가 거북목에 갇혀 못 나왔다(시드 1: 0.308→0.269).
//     주기 900 s + 자리 비움 60 s 리셋으로 그 장면은 0.334(+0.065) 회복. 그러나 최종 36개 장면에서는 주기 리셋이 좋은 배치에서
//     −0.038(시드 2·3) 손해라 전체 효과가 없었다(0.595→0.592) → 주기 리셋 기본값은 0(끔), 실측에서 고착이 보이면 켠다.
//     자리 비움 리셋은 60 s 유지(단독 효과는 PC 가 따로 재는 중).

inline const char* fusionName(FusionRule r) { return r == FusionRule::LogProb ? "logprob" : "mean"; }

struct SensorStatus {
    std::string name;
    bool online = false;
    bool ever = false;              // 프레임을 받아 본 적이 있나
    uint64_t frames = 0;            // 받은 프레임 수
    uint64_t steps = 0;             // 모델을 돌린 횟수
    uint64_t reused = 0;            // 새 프레임이 없어 직전 프레임을 다시 넣은 횟수
    uint64_t resets = 0;
    int64_t age_ms = -1;            // 마지막 프레임이 온 뒤 지난 시간
    double hz = 0.0;                // 최근 프레임 수신율
    int argmax = -1;
    float conf = 0.f;
    float probs[kMaxClasses] = {};
    std::string hello;              // 마지막 tof_hello (센서·hz·flip)
    std::string error;              // 마지막 tof_err
    std::string rule;               // 펌웨어 온보드 규칙 판정(있으면) — 모델과 비교용
    std::string info;               // 마지막 안내 줄 (# …)
    bool has_status = true;         // 프레임에 상태값이 있었나 (bring-up CSV 는 없을 수 있다)
    bool flip_rows = false, flip_cols = false;   // 마지막 프레임에 실제로 적용한 뒤집기
    uint64_t seq_gaps = 0;          // frame_seq 짧은 유실(2..10) 횟수
    uint64_t duplicates = 0;        // 같은 frame_seq 가 다시 와서 버린 횟수
    uint64_t idle_frames = 0;       // UART 0x05 valid=0 (센서는 살아 있고 새 측정 없음) 받은 수
    bool alive = false;             // 최근 stale_ms 안에 이 센서의 줄(valid 든 아니든)이 왔다
    bool stale = false;             // 살아 있지만 유효 프레임이 stale_ms 넘게 없다 → 판정에서 빠지고 GRU 리셋
    float coverage = 0.f;           // 마지막 프레임의 재실 coverage (status 5·6·9·10 ∧ [present_min_mm, present_max_mm] 인 zone / 전체)
};

struct ResetStats {
    uint64_t periodic = 0, away = 0, manual = 0;
    int64_t last_reset_ms = -1;     // 마지막 리셋 시각 (어떤 계기든). 리셋이 없었으면 첫 tick 시각
};

struct DeviceResult {
    bool valid = false;             // online 센서가 하나라도 있었나
    int argmax = -1;
    float conf = 0.f;
    float probs[kMaxClasses] = {};
    float smooth[kMaxClasses] = {}; // EMA
    int smooth_argmax = -1;
    int online_sensors = 0;
    uint64_t ticks = 0;
    int64_t last_tick_ms = -1;
    int64_t label_since_ms = -1;    // 현재 (smooth) 라벨이 시작된 시각
    bool present_rule = false;      // ToF 규칙 재실 (online 센서 coverage 최대 ≥ present_min_coverage)
    float coverage_max = 0.f;
    ResetStats resets;
};

class DevicePosture {
public:
    explicit DevicePosture(DeviceConfig cfg = {});

    bool loadModel(const std::string& path, std::string* err = nullptr);
    int addSensor(const std::string& name);                 // 센서 번호를 돌려준다
    void renameSensor(int sensor, const std::string& name);
    int sensorCount() const { return static_cast<int>(slots_.size()); }
    int classes() const { return net_classes_; }
    const DeviceConfig& config() const { return cfg_; }

    // 읽기 스레드에서: 파싱된 줄 하나
    void feed(int sensor, const TofLine& line, int64_t now_ms);

    // 메인 루프에서 주기적으로. 스텝을 돌렸으면 true (rate_hz 간격이 안 됐으면 false)
    bool tick(int64_t now_ms);

    void reset(int64_t now_ms = -1);                        // 모든 GRU 상태 0 + EMA 비움 (HTTP /api/reset). now_ms<0 면 마지막 tick 시각

    DeviceResult result() const;
    std::vector<SensorStatus> sensors(int64_t now_ms) const;
    std::string stateJson(int64_t now_ms) const;            // GET /api/state
    std::string healthJson(int64_t now_ms) const;           // GET /health

    static const char* labelName(int idx) { return idx >= 0 && idx < kMaxClasses ? kLabels[idx] : "unknown"; }
    static const char* labelKo(int idx) { return idx >= 0 && idx < kMaxClasses ? kLabelsKo[idx] : "모름"; }

private:
    struct Slot {
        SensorStatus st;
        PostureNet net;
        TofFrame last;
        bool has_frame = false;
        int64_t last_rx_ms = -1;
        int64_t first_rx_ms = -1;
        uint64_t frames_at_first = 0;
        uint32_t last_used_seq = 0;
        bool used_once = false;
        uint32_t last_seq = 0;
        bool seq16_known = false;       // UART 0x05 frame_seq 추적 (valid=0 프레임도 갱신)
        uint32_t last_seq16 = 0;
        int64_t last_alive_ms = -1;     // 유효든 valid=0 이든 마지막으로 줄이 온 시각
        int64_t hz_window_start = -1;
        uint64_t hz_window_frames = 0;
    };

    std::string modelPath_;
    DeviceConfig cfg_;
    int net_classes_ = 0;
    void resetLocked(const char* why, int64_t now_ms);     // mutex_ 를 잡은 채로

    std::vector<Slot> slots_;
    DeviceResult res_;
    int64_t absent_since_ms_ = -1;  // 재실 false 가 이어지기 시작한(또는 마지막 away 리셋) 시각. -1 = 재실 중/타이머 없음
    mutable std::mutex mutex_;
};

}  // namespace tof

#endif  // DESKMATE_TOF_DEVICE_POSTURE_H
