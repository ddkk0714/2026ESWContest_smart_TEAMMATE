// 포즈 랜드마크로 자세를 판정한다: 바른자세 · 젖힘 · 엎드림 · 턱괴기 · 자리비움.
//
// `76EHwan/pico_esp32-cam_ftdi` 의 `tools/posture_pose.py` 를 그대로 옮긴 것이다.
// 그쪽이 다른 노트북에서 검증된 판정이라, 옮기면서 결과가 달라지면 안 된다.
// `test/pose_golden.txt` 가 원본을 돌려 만든 정답지이고 `posture_pose_host_test`
// 가 프레임마다 채점한다.
//
// **임계값과 판정 순서를 임의로 고치지 말 것.** 왜 그 값인지는 원본 주석에 있다.
//
// 왜 이런 측정인가 (원본 설명 요약):
//
//   * 뒤로 젖히는 것과 앞으로 접는 것은 **둘 다 화면에서 머리를 내린다.** 높이만으로는
//     '얼마나 벗어났나' 만 알 뿐 '어느 쪽인가' 를 모른다. 가르는 것은 겉보기 크기다 -
//     젖히면 카메라에서 멀어져 어깨가 좁아지고, 접으면 안 그렇다. **어깨 폭은 엎드려도
//     유지되는 유일한 길이**라 그 변화는 자세가 아니라 거리다.
//   * 턱괴기는 머리가 아니라 **팔의 사실**이다. 머리는 거의 안 움직이고 손이 옆에 온다.
//     그래서 머리 규칙보다 **먼저** 본다 - 턱을 괴면 머리가 조금 내려가 엎드림의 시작처럼
//     읽히기 때문이다.
//   * 모든 문턱은 **캡처한 기준 자세에 대한 비율**이다. 절대 픽셀로 두면 사람마다,
//     의자 높이마다, 카메라 거리마다 다시 맞춰야 한다.
#ifndef DESKMATE_POSTURE_POSE_H
#define DESKMATE_POSTURE_POSE_H

#include <cmath>
#include <map>
#include <string>

namespace deskmate {

// MediaPipe pose 랜드마크 번호.
inline constexpr int kNose = 0;
inline constexpr int kLeftShoulder = 11;
inline constexpr int kRightShoulder = 12;
inline constexpr int kLeftElbow = 13;
inline constexpr int kRightElbow = 14;
inline constexpr int kLeftWrist = 15;
inline constexpr int kRightWrist = 16;

inline constexpr const char* kUpright = "UPRIGHT";
inline constexpr const char* kRecline = "RECLINE";
inline constexpr const char* kSlump = "SLUMP";
inline constexpr const char* kChinRest = "CHIN_REST";
inline constexpr const char* kAbsent = "ABSENT";

// 아래 거리는 전부 어깨 폭 단위다. 픽셀이 아니라 비율이다.
//
// 머리 낙차 두 값이 일부러 멀다. 어깨 폭으로 나누면 거리가 상쇄되므로 - 사람이 멀어지면
// 분자와 분모가 같이 줄어 head_drop 은 제자리다 - 젖힘은 보기만큼 '머리가 내려간' 것으로
// 잡히지 않는다. 정규화를 견디고 남는 것은 상체가 기울며 생기는 목 단축뿐이고, 그건
// 책상에 접는 것의 3분의 1이다. 문턱 하나로 둘 다 잡을 수 없다.
inline constexpr double kSlumpDropOn = 0.35;
inline constexpr double kSlumpDropOff = 0.20;   // 히스테리시스
inline constexpr double kShrinkOn = 0.90;       // 기준보다 이만큼 좁아지면 멀어진 것
inline constexpr double kShrinkOff = 0.95;      // 히스테리시스
inline constexpr double kChinNear = 0.70;       // 손목이 머리에서 이 안이면 옆에 있는 것
inline constexpr double kChinRelease = 0.90;    // 이만큼 떨어져야 해제
inline constexpr double kHoldSeconds = 1.5;     // 이만큼 유지돼야 보고한다

struct Point {
  double x = 0.0;
  double y = 0.0;
};

using Landmarks = std::map<int, Point>;

// 판정이 쓰는, 크기에 무관한 수치들.
struct Metrics {
  double shoulder_width = 0.0;
  double head_drop = 0.0;         // (코y - 어깨중앙y) / 어깨폭
  double head_drop_delta = 0.0;   // 기준값과의 차
  double scale_ratio = 1.0;       // 어깨폭 / 기준 어깨폭
  double wrist_to_head = std::numeric_limits<double>::infinity();
  bool arm_folded = false;
  bool valid = false;             // 파이썬의 None 자리
};

struct State {
  std::string label = kAbsent;
  std::string candidate = kAbsent;  // 지금 보이는 것. hold 를 채우기 전
  double held_for = 0.0;
  Metrics metrics;
  bool has_reference = false;
  std::string note;
};

// 랜드마크를 판정이 쓰는 비율로 줄인다.
//
// 어깨 둘이 안 보이면 무효다 - 어깨 폭이 다른 모든 값의 자이고, 어깨 하나로는 머리를
// 견줄 중앙점도 없다.
Metrics measure(const Landmarks& pts);

class PostureTracker {
 public:
  explicit PostureTracker(double hold_seconds = kHoldSeconds)
      : hold_(hold_seconds) {}

  // 사람이 앉을 자세로 앉은 상태에서 부른다. 어깨가 둘 다 보여야 성공한다.
  bool captureReference(const Landmarks& pts);

  State update(const Landmarks& pts, double now);

  bool hasReference() const { return ref_.valid; }

 private:
  std::string classify(const Metrics& m, std::string& note);
  void commit(const std::string& candidate, double now);
  double held(double now) const;

  double hold_;
  Metrics ref_;
  std::string label_ = kAbsent;
  std::string seen_ = kAbsent;      // hold 타이머가 도는 후보
  double since_ = 0.0;
  bool since_set_ = false;
  bool was_dropped_ = false;
  bool was_chin_ = false;
  bool was_receded_ = false;
};

}  // namespace deskmate

#endif  // DESKMATE_POSTURE_POSE_H
