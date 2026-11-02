// 장치 위치(설치 때 사용자 자리에서 본 방향: left · center · right)별 모델 전환.
//
// 사용자가 Pi 5 설치 화면에서 위치를 고르면 앱이 POST /api/placement {"placement":"left"} 를 부르고, 이 서비스는 위치별
// posture_net.bin(같은 구조 — H·W·classes·hidden 이 같아야 한다)으로 바꿔 끼운 뒤 GRU h·융합 EMA 를 비우고(리셋 경로 재사용,
// resets.model_switch), 고른 위치를 파일에 남겨 다음 시작 때 그 모델로 뜬다.
//
//   TOFPOSTURE_PLACEMENT_MODELS  "left=<경로>,center=<경로>,right=<경로>" (실행 파일 폴더 기준 상대 경로 허용).
//                                비우면 기능 꺼짐 = 이전과 완전히 같음 (/api/placement 404, /api/state 에 placement 없음)
//   TOFPOSTURE_PLACEMENT_FILE    기본 <실행 파일 폴더>/placement.txt. 내용은 left|center|right 한 줄. tmp+rename 으로 쓴다
//
// 시작: 파일의 위치가 맵에 있으면 그 모델(TOFPOSTURE_MODEL 보다 우선). 없으면 TOFPOSTURE_MODEL 로 시작하고 placement 는
// 그 경로가 맵의 어느 값과 같으면 그 키, 아니면 "unknown".
// 지금(2026-10-25)은 셋 다 opt_l8_c256 으로 자리만 잡아 두고, PC 가 위치별 모델을 만들면 경로만 바꾼다.
#ifndef DESKMATE_TOF_PLACEMENT_H
#define DESKMATE_TOF_PLACEMENT_H

#include <cstdint>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace tof {

class DevicePosture;

struct PlacementConfig {
    std::vector<std::pair<std::string, std::string>> models;   // (left|center|right, 모델 경로) — 맵 순서 = options 순서
    std::string file;                                          // 고른 위치를 남기는 파일
    bool enabled() const { return !models.empty(); }
    const std::string* modelFor(const std::string& key) const;
    std::string keyForModel(const std::string& path) const;    // 경로가 맵의 값과 같으면 그 키, 아니면 "unknown"
};

// "left=a,center=b,right=c" → 맵. 키는 left|center|right 만(그 밖은 실패), 같은 키 두 번도 실패, 값이 비면 실패.
// 상대 경로는 base_dir 아래로. spec 이 비면(공백만) 성공하되 맵이 빈다(기능 꺼짐).
bool parsePlacementModels(const std::string& spec, const std::string& base_dir, PlacementConfig& out, std::string* err = nullptr);

// 파일에서 위치 키 읽기(공백·개행 제거). 없거나 비면 "".
std::string readPlacementFile(const std::string& path);
// tmp+rename 으로 저장. 실패면 false.
bool writePlacementFile(const std::string& path, const std::string& key, std::string* err = nullptr);

// POST 본문 {"placement":"left"} 에서 값. 없으면 "".
std::string parsePlacementBody(const std::string& body);

class Placement {
public:
    Placement(PlacementConfig cfg, DevicePosture& device);
    bool enabled() const { return cfg_.enabled(); }

    // 시작 때: 파일 → 맵 → 모델 경로. 돌려준 경로로 loadModel 한다. 파일에 맞는 키가 없으면 fallback_model 을 돌려주고
    // placement 는 keyForModel(fallback_model).
    std::string initialModelPath(const std::string& fallback_model);

    std::string json(int64_t now_ms) const;                 // GET /api/placement
    // POST /api/placement. HTTP 코드(200·400·500)와 본문(200 이면 json(), 아니면 {"error":...}).
    int apply(const std::string& body, int64_t now_ms, std::string& response);

    std::string current() const;

private:
    PlacementConfig cfg_;
    DevicePosture& device_;
    mutable std::mutex mutex_;
    std::string current_ = "unknown";
    std::string model_;
    int64_t changed_ms_ = -1;
    uint64_t switches_ = 0;
};

}  // namespace tof

#endif  // DESKMATE_TOF_PLACEMENT_H
