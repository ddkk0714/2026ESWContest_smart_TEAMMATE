#include "pose_model.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string>

#include "tensorflow/lite/interpreter.h"
#include "tensorflow/lite/interpreter_builder.h"
#include "tensorflow/lite/kernels/register.h"
#include "tensorflow/lite/model_builder.h"

namespace deskmate {
namespace {

// CAMSVC_ROI_SCALE 로 덮어쓸 수 있다. 정답지와 맞춰 볼 때만 쓰고, 맞은 값은
// pose_geometry.h 의 기본값으로 올린다.
// 랜드마크 모델 입력 범위도 실측으로 정한다. CAMSVC_LM_RANGE=pm1 이면 -1~1.
bool landmarkRangeIsPlusMinusOne() {
  const char* value = std::getenv("CAMSVC_LM_RANGE");
  return value != nullptr && std::string(value) == "pm1";
}

// 자른 사각형과 되돌릴 때 쓰는 사각형을 일부러 갈라 놓고 비를 재기 위한 손잡이.
// 1.0 이면 둘이 같다(원래 동작).
double projectionScale() {
  const char* value = std::getenv("CAMSVC_PROJ_SCALE");
  if (value == nullptr || *value == 0) return 1.0;
  const double parsed = std::atof(value);
  return parsed > 0.0 ? parsed : 1.0;
}

double detectionRoiScale() {
  const char* value = std::getenv("CAMSVC_ROI_SCALE");
  if (value == nullptr || *value == '\0') return kDetectionRoiScale;
  const double parsed = std::atof(value);
  return parsed > 0.0 ? parsed : kDetectionRoiScale;
}

// 이미지 밖을 읽으면 0(검정). 레터박스 여백도 같은 값이라 따로 다루지 않는다.
inline float sampleBilinear(const std::uint8_t* grey, int width, int height,
                            double x, double y) {
  if (x < -0.5 || y < -0.5 || x > width - 0.5 || y > height - 0.5) return 0.0f;
  const double fx = std::min(std::max(x, 0.0), width - 1.0);
  const double fy = std::min(std::max(y, 0.0), height - 1.0);
  const int x0 = static_cast<int>(fx);
  const int y0 = static_cast<int>(fy);
  const int x1 = std::min(x0 + 1, width - 1);
  const int y1 = std::min(y0 + 1, height - 1);
  const double tx = fx - x0;
  const double ty = fy - y0;

  const double top = grey[y0 * width + x0] * (1.0 - tx) + grey[y0 * width + x1] * tx;
  const double bottom =
      grey[y1 * width + x0] * (1.0 - tx) + grey[y1 * width + x1] * tx;
  return static_cast<float>((top * (1.0 - ty) + bottom * ty) / 255.0);
}

}  // namespace

void letterboxToTensor(const std::uint8_t* grey, int width, int height, int size,
                       float* out, float range_min, float range_max) {
  const float span = range_max - range_min;
  // computeLetterbox 와 같은 배치여야 한다. 긴 쪽을 꽉 채우고 나머지는 가운데.
  const double scale = std::min(static_cast<double>(size) / width,
                                static_cast<double>(size) / height);
  const double fit_w = width * scale;
  const double fit_h = height * scale;
  const double off_x = (size - fit_w) * 0.5;
  const double off_y = (size - fit_h) * 0.5;

  for (int y = 0; y < size; ++y) {
    for (int x = 0; x < size; ++x) {
      // 출력 픽셀 중심을 원본 좌표로 되돌린다.
      const double sx = (x + 0.5 - off_x) / scale - 0.5;
      const double sy = (y + 0.5 - off_y) / scale - 0.5;
      // 이미지 밖은 0(검정)이 돌아온다. 그 값도 같이 옮겨야 여백이 검정이 된다.
      const float v = range_min + sampleBilinear(grey, width, height, sx, sy) * span;
      float* pixel = out + (static_cast<std::size_t>(y) * size + x) * 3;
      pixel[0] = v;
      pixel[1] = v;
      pixel[2] = v;
    }
  }
}

void cropToTensor(const std::uint8_t* grey, int width, int height,
                  const RotatedRect& roi, int size, float* out, float range_min,
                  float range_max) {
  const float span = range_max - range_min;
  float m[6];
  cropTransform(roi, width, height, size, m);
  for (int y = 0; y < size; ++y) {
    for (int x = 0; x < size; ++x) {
      const double sx = m[0] * x + m[1] * y + m[2];
      const double sy = m[3] * x + m[4] * y + m[5];
      const float v = range_min + sampleBilinear(grey, width, height, sx, sy) * span;
      float* pixel = out + (static_cast<std::size_t>(y) * size + x) * 3;
      pixel[0] = v;
      pixel[1] = v;
      pixel[2] = v;
    }
  }
}

RotatedRect roiFromLandmarks(const std::vector<Landmark>& landmarks,
                             int src_width, int src_height) {
  RotatedRect empty;
  if (landmarks.size() < 35) return empty;
  // 33 번이 중심, 34 번이 크기점. 검출기 키포인트 0·1 과 같은 역할이라 같은
  // 계산을 그대로 쓴다.
  Detection as_detection;
  as_detection.kp[0][0] = landmarks[33].x;
  as_detection.kp[0][1] = landmarks[33].y;
  as_detection.kp[1][0] = landmarks[34].x;
  as_detection.kp[1][1] = landmarks[34].y;
  return roiFromDetection(as_detection, src_width, src_height,
                          kLandmarkRoiScale);
}

// --------------------------------------------------------------------------

struct PoseModel::Impl {
  std::unique_ptr<tflite::FlatBufferModel> detector_model;
  std::unique_ptr<tflite::Interpreter> detector;
  std::unique_ptr<tflite::FlatBufferModel> landmark_model;
  std::unique_ptr<tflite::Interpreter> landmark;

  std::vector<Anchor> anchors;
  std::vector<float> detector_input;
  std::vector<float> landmark_input;

  RotatedRect roi;
  bool has_roi = false;
  int tracked = 0;  // 검출기 확인 없이 추적만으로 지나온 프레임 수
};

PoseModel::PoseModel() : impl_(new Impl) {}
PoseModel::~PoseModel() = default;

bool PoseModel::load(const std::string& detector_path,
                     const std::string& landmark_path, int threads) {
  tflite::ops::builtin::BuiltinOpResolver resolver;

  impl_->detector_model =
      tflite::FlatBufferModel::BuildFromFile(detector_path.c_str());
  if (impl_->detector_model == nullptr) {
    error_ = "검출기 모델을 못 읽습니다: " + detector_path;
    return false;
  }
  if (tflite::InterpreterBuilder(*impl_->detector_model,
                                 resolver)(&impl_->detector) != kTfLiteOk ||
      impl_->detector == nullptr) {
    error_ = "검출기 인터프리터를 못 만듭니다";
    return false;
  }
  impl_->detector->SetNumThreads(threads);
  if (impl_->detector->AllocateTensors() != kTfLiteOk) {
    error_ = "검출기 텐서를 못 잡습니다";
    return false;
  }

  impl_->landmark_model =
      tflite::FlatBufferModel::BuildFromFile(landmark_path.c_str());
  if (impl_->landmark_model == nullptr) {
    error_ = "랜드마크 모델을 못 읽습니다: " + landmark_path;
    return false;
  }
  if (tflite::InterpreterBuilder(*impl_->landmark_model,
                                 resolver)(&impl_->landmark) != kTfLiteOk ||
      impl_->landmark == nullptr) {
    error_ = "랜드마크 인터프리터를 못 만듭니다";
    return false;
  }
  impl_->landmark->SetNumThreads(threads);
  if (impl_->landmark->AllocateTensors() != kTfLiteOk) {
    error_ = "랜드마크 텐서를 못 잡습니다";
    return false;
  }

  // 출력 개수는 모델이 바뀌면 조용히 달라진다. 여기서 한 번 확인해 둔다.
  if (impl_->detector->outputs().size() < 2 ||
      impl_->landmark->outputs().size() < 2) {
    error_ = "모델 출력 구성이 예상과 다릅니다";
    return false;
  }

  impl_->anchors = buildAnchors();
  if (impl_->anchors.size() != kDetectorAnchors) {
    error_ = "앵커 수가 안 맞습니다";
    return false;
  }
  impl_->detector_input.assign(
      static_cast<std::size_t>(kDetectorSize) * kDetectorSize * 3, 0.0f);
  impl_->landmark_input.assign(
      static_cast<std::size_t>(kLandmarkSize) * kLandmarkSize * 3, 0.0f);
  impl_->has_roi = false;
  error_.clear();
  return true;
}

void PoseModel::resetTracking() {
  impl_->has_roi = false;
  impl_->tracked = 0;
}

PoseResult PoseModel::run(const std::uint8_t* grey, int width, int height) {
  PoseResult result;
  if (impl_->detector == nullptr || impl_->landmark == nullptr) {
    error_ = "모델이 아직 안 올라왔습니다";
    return result;
  }

  // 추적이 오래되면 검출기에게 다시 물어본다. 사람이 나갔는데도 랜드마크가
  // 계속 나오는 것을 끊는 유일한 장치다.
  const bool recheck = impl_->has_roi && impl_->tracked >= kRecheckFrames;
  if (!impl_->has_roi || recheck) {
    result.ran_detector = true;
    letterboxToTensor(grey, width, height, kDetectorSize,
                      impl_->detector_input.data());
    std::copy(impl_->detector_input.begin(), impl_->detector_input.end(),
              impl_->detector->typed_input_tensor<float>(0));
    if (impl_->detector->Invoke() != kTfLiteOk) {
      error_ = "검출기 실행 실패";
      return result;
    }
    const float* boxes = impl_->detector->typed_output_tensor<float>(0);
    const float* scores = impl_->detector->typed_output_tensor<float>(1);
    for (int i = 0; i < kDetectorAnchors; ++i) {
      result.best_score = std::max(result.best_score, sigmoidClipped(scores[i]));
    }

    Detection det = decodeDetections(boxes, scores, impl_->anchors);
    if (det.score < kMinScoreThresh) {
      // 사람이 없다. 확인하러 왔다가 못 찾았으면 물고 있던 것도 놓는다.
      impl_->has_roi = false;
      impl_->tracked = 0;
      return result;
    }

    removeLetterbox(det, computeLetterbox(width, height));
    // 값을 바로 바꿔 가며 실측할 수 있게 열어 둔다. 기본값이 곧 정답이다.
    impl_->roi = roiFromDetection(det, width, height, detectionRoiScale());
    impl_->has_roi = true;
    impl_->tracked = 0;
  } else {
    ++impl_->tracked;
  }

  const bool pm1 = landmarkRangeIsPlusMinusOne();
  cropToTensor(grey, width, height, impl_->roi, kLandmarkSize,
               impl_->landmark_input.data(), pm1 ? -1.0f : kLandmarkRangeMin,
               kLandmarkRangeMax);
  std::copy(impl_->landmark_input.begin(), impl_->landmark_input.end(),
            impl_->landmark->typed_input_tensor<float>(0));
  if (impl_->landmark->Invoke() != kTfLiteOk) {
    error_ = "랜드마크 실행 실패";
    impl_->has_roi = false;
    return result;
  }

  const float* raw = impl_->landmark->typed_output_tensor<float>(0);
  const float* presence_logit = impl_->landmark->typed_output_tensor<float>(1);
  result.presence = sigmoidClipped(presence_logit[0]);
  if (result.presence < kPresenceThreshold) {
    // 놓쳤다. 다음 프레임은 검출기부터.
    impl_->has_roi = false;
    impl_->tracked = 0;
    return result;
  }

  // 보조점까지 39개를 되돌린 뒤, 판정에 쓸 33개만 남기고 나머지는 다음 ROI 로.
  RotatedRect projection = impl_->roi;
  const double projection_scale = projectionScale();
  projection.width = static_cast<float>(projection.width * projection_scale);
  projection.height = static_cast<float>(projection.height * projection_scale);
  std::vector<Landmark> all =
      projectLandmarks(raw, projection, width, height, kLandmarkCount);
  impl_->roi = roiFromLandmarks(all, width, height);
  impl_->has_roi = impl_->roi.width > 0.0f;

  result.roi = impl_->roi;
  all.resize(33);
  result.landmarks = std::move(all);
  result.found = true;
  return result;
}

}  // namespace deskmate
