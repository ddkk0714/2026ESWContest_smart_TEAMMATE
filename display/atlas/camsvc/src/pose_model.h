// BlazePose 두 모델을 TFLite 로 돌린다. 앞뒤 계산은 `pose_geometry.h` 가 한다.
//
// 원본(`thermal-pose/thermal_pose.py`)은 MediaPipe PoseLandmarker 를
// `RunningMode.VIDEO` 로 쓴다. 그건 **매 프레임 검출기를 돌리지 않는다** -
// 사람을 한 번 찾으면 그 다음부터는 직전 랜드마크에서 ROI 를 잡고 랜드마크
// 모델만 돌리다가, 존재 점수가 떨어지면 그때 다시 검출기로 돌아간다.
// 여기서도 같게 한다. 매 프레임 검출을 돌리면 속도도 두 배로 들고, 무엇보다
// 원본과 다른 궤적이 나온다.
//
//   처음/놓쳤을 때 : 224 입력 -> 검출기 -> NMS -> ROI
//   추적 중        : 직전 랜드마크 33·34번 -> ROI
//   공통           : ROI -> 256 크롭 -> 랜드마크 모델 -> 원본 좌표로 되돌리기
//
// 임계값은 원본의 `min_pose_detection_confidence` / `min_pose_presence_confidence`
// 와 같은 0.5 다.
#ifndef DESKMATE_POSE_MODEL_H
#define DESKMATE_POSE_MODEL_H

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "pose_geometry.h"

namespace deskmate {

// 원본의 min_pose_presence_confidence.
inline constexpr float kPresenceThreshold = 0.5f;

// 이만큼 추적했으면 검출기로 한 번 확인한다.
//
// **원본에 없는 장치다.** MediaPipe 는 존재 점수만 보고 추적을 이어가는데, 실기에서
// 사람이 자리를 떠도 그 점수가 0.5 밑으로 안 내려가 판정이 "바른 자세" 로 굳는 것을
// 봤다. 랜드마크 모델은 크롭 안에 사람이 없어도 **무언가는 반드시 찍기** 때문이다.
// 검출기는 그런 성질이 없으므로 주기적으로 독립된 확인을 받는다.
// preview 가 약 4fps 라 10프레임이면 2초쯤이고, 그때만 24ms 를 더 쓴다.
inline constexpr int kRecheckFrames = 10;

struct PoseResult {
  bool found = false;
  float presence = 0.0f;
  bool ran_detector = false;  // 이번 프레임에 검출기를 돌렸는지(성능 확인용)
  // 검출기가 본 최고 점수. 문턱을 못 넘어도 남는다 - 사람을 못 찾을 때
  // '거의 찾았다' 와 '아무것도 없다' 는 완전히 다른 문제다.
  float best_score = 0.0f;
  std::vector<Landmark> landmarks;  // 33개. found 가 false 면 비어 있다
  // 이번 프레임에 쓴 크롭 사각형. 좌표가 통째로 밀릴 때 원인이 여기라서 밖에서
  // 볼 수 있어야 한다.
  RotatedRect roi;
};

class PoseModel {
 public:
  PoseModel();
  ~PoseModel();
  PoseModel(const PoseModel&) = delete;
  PoseModel& operator=(const PoseModel&) = delete;

  // 두 `.tflite` 를 읽는다. `threads` 는 인터프리터 스레드 수.
  bool load(const std::string& detector_path, const std::string& landmark_path,
            int threads = 2);

  // 회색 이미지 한 장(폭*높이 바이트). 카메라가 흑백이라 세 채널에 같은 값을 넣는다.
  PoseResult run(const std::uint8_t* grey, int width, int height);

  // 추적을 끊는다. 다음 프레임에 검출기부터 다시 돈다.
  void resetTracking();

  const std::string& error() const { return error_; }

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
  std::string error_;
};

// --- 아래는 테스트에서도 쓰려고 꺼내 둔 전처리 -----------------------------

// **두 모델의 입력 범위가 다르다.** 검출기는 -1~1, 랜드마크 모델은 0~1 이다
// (MediaPipe ImageToTensorCalculator 의 output_tensor_float_range). 여기를
// 맞추지 않으면 모델은 멀쩡히 돌면서 아무것도 못 찾는다 - 오류도 안 난다.
inline constexpr float kDetectorRangeMin = -1.0f;
inline constexpr float kDetectorRangeMax = 1.0f;
inline constexpr float kLandmarkRangeMin = 0.0f;
inline constexpr float kLandmarkRangeMax = 1.0f;

// 회색 이미지를 비율 유지로 정사각형에 넣고 실수 RGB 로 편다. 이미지 밖은 검정
// (= range_min)이다. `out` 크기는 size*size*3.
void letterboxToTensor(const std::uint8_t* grey, int width, int height, int size,
                       float* out, float range_min = kDetectorRangeMin,
                       float range_max = kDetectorRangeMax);

// 회전 ROI 를 잘라 실수 RGB 로 편다. `out` 크기는 size*size*3.
void cropToTensor(const std::uint8_t* grey, int width, int height,
                  const RotatedRect& roi, int size, float* out,
                  float range_min = kLandmarkRangeMin,
                  float range_max = kLandmarkRangeMax);

// 직전 프레임 랜드마크(39개)의 보조점 33·34 번으로 다음 ROI 를 잡는다.
RotatedRect roiFromLandmarks(const std::vector<Landmark>& landmarks,
                             int src_width, int src_height);

}  // namespace deskmate

#endif  // DESKMATE_POSE_MODEL_H
