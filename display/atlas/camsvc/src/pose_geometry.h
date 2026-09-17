// MediaPipe 포즈 파이프라인의 기하 부분. TFLite 를 안 쓰므로 보드 없이 검사된다.
//
// MediaPipe 라이브러리가 모델 앞뒤에서 하던 일을 여기로 옮긴다. `.task` 안에는
// 모델 둘만 들어 있고 이 접착부는 라이브러리 쪽에 있어서, 모델만 꺼내 돌리면
// 아무것도 안 나온다.
//
//   1) 앵커 2254개 생성            SsdAnchorsCalculator
//   2) 박스·키포인트 디코딩        TensorsToDetectionsCalculator
//   3) NMS                          NonMaxSuppressionCalculator
//   4) 레터박스 되돌리기            DetectionLetterboxRemovalCalculator
//   5) 검출 -> 회전 ROI             AlignmentPointsRectsCalculator + RectTransformation
//   6) 랜드마크 좌표 되돌리기       LandmarkProjectionCalculator
//
// 상수는 BlazePose(pose_detection) 설정값이다. 여기 숫자가 틀리면 모델은 멀쩡히
// 돌면서 좌표만 조용히 어긋난다. **MediaPipe 가 같은 이미지에서 내는 랜드마크와
// 맞춰 보는 것이 유일한 검증이다** - `tools/gen_pose_landmarks_golden.py`.
#ifndef DESKMATE_POSE_GEOMETRY_H
#define DESKMATE_POSE_GEOMETRY_H

#include <cstddef>
#include <vector>

namespace deskmate {

// --- 모델 입력 크기 -------------------------------------------------------
inline constexpr int kDetectorSize = 224;   // pose_detector 입력
inline constexpr int kLandmarkSize = 256;   // pose_landmarks_detector 입력
inline constexpr int kDetectorAnchors = 2254;
inline constexpr int kDetectorCoords = 12;  // 박스 4 + 키포인트 4쌍
inline constexpr int kLandmarkCount = 39;   // 모델이 내는 수. 앞의 33개만 쓴다
inline constexpr int kLandmarkValues = 5;   // x, y, z, visibility, presence

// --- 검출기 설정 (BlazePose) ---------------------------------------------
inline constexpr double kMinScale = 0.1484375;
inline constexpr double kMaxScale = 0.75;
inline constexpr int kNumLayers = 5;
inline constexpr int kStrides[kNumLayers] = {8, 16, 32, 32, 32};
inline constexpr double kAnchorOffsetX = 0.5;
inline constexpr double kAnchorOffsetY = 0.5;

inline constexpr double kMinScoreThresh = 0.5;
inline constexpr double kScoreClip = 100.0;
inline constexpr double kNmsIou = 0.3;

// 검출·랜드마크 -> ROI. 사람을 세우는 회전과 확대.
//
// MediaPipe 는 첫 ROI(pose_detection_to_roi)와 추적 ROI(pose_landmarks_to_roi)를
// 서로 다른 그래프에서 만든다. 그래서 상수도 갈라 둔다 - 다만 **정답지와 맞춰 본
// 결과 둘 다 1.25 가 가장 가깝다.** 1.4·1.5·1.6·1.75 를 넣어 보면 어긋남이
// 5.2px -> 8.8 -> 11.1 -> 12.4 -> 17.8px 로 단조 증가한다(160x120, 상반신 사진).
//
// 값을 바꾸고 싶으면 상상하지 말고 재 볼 것:
//   tools/gen_pose_landmarks_golden.py 로 정답지를 만들고
//   보드에서 CAMSVC_ROI_SCALE=<값> deskmate_camsvc --replay
//   tools/score_pose_landmarks.py 로 채점
inline constexpr double kDetectionRoiScale = 1.25;
inline constexpr double kLandmarkRoiScale = 1.25;
inline constexpr double kTargetAngleDegrees = 90.0;

// 원본 이미지를 224x224 정사각형에 넣을 때 생긴 여백. 비율을 유지하며 넣으므로
// 위아래 또는 좌우가 남는다. 검출 좌표는 이 여백을 빼야 원본 좌표가 된다.
struct Letterbox {
  float left = 0.0f;
  float top = 0.0f;
  float right = 0.0f;
  float bottom = 0.0f;
};

struct Anchor {
  float x_center = 0.0f;
  float y_center = 0.0f;
  float w = 1.0f;   // fixed_anchor_size
  float h = 1.0f;
};

struct Detection {
  float score = 0.0f;
  float xmin = 0.0f, ymin = 0.0f, width = 0.0f, height = 0.0f;  // 0~1
  // 키포인트 4개. 0 = 엉덩이 중앙, 1 = 몸 전체 크기점. 둘이 ROI 를 정한다.
  float kp[4][2] = {};
};

// 회전된 사각형. 좌표는 0~1 정규화.
struct RotatedRect {
  float x_center = 0.0f;
  float y_center = 0.0f;
  float width = 0.0f;
  float height = 0.0f;
  float rotation = 0.0f;  // 라디안
};

struct Landmark {
  float x = 0.0f;         // 0~1, 원본 이미지 기준
  float y = 0.0f;
  float z = 0.0f;
  float visibility = 0.0f;
  float presence = 0.0f;
};

// 1) 앵커. 모델이 2254개를 기대하므로 그 수가 안 맞으면 전부 어긋난다.
std::vector<Anchor> buildAnchors();

// 2)+3) 모델 출력 -> 검출 하나. 없으면 score 0 인 것을 돌려준다.
//
// `boxes` 는 [2254][12], `scores` 는 [2254]. 점수는 로짓이라 시그모이드를 건다.
Detection decodeDetections(const float* boxes, const float* scores,
                           const std::vector<Anchor>& anchors);

// 3) 비율을 지켜 정사각형에 넣었을 때의 여백. 160x120 이면 위아래가 남는다.
Letterbox computeLetterbox(int src_width, int src_height);

// 4) 여백을 빼고 원본 이미지 기준 0~1 좌표로 되돌린다.
void removeLetterbox(Detection& det, const Letterbox& pad);

// 5) 검출 -> 랜드마크 모델에 넣을 회전 ROI.
//
// 중심은 키포인트 0, 크기는 키포인트 0~1 거리의 2배, 회전은 그 둘을 잇는 선이
// 수직이 되도록. 그 뒤 1.25배로 넓힌다. 회전은 픽셀 기준이라 원본 크기가 필요하다.
RotatedRect roiFromDetection(const Detection& det, int src_width,
                             int src_height,
                             double roi_scale = kDetectionRoiScale);

// ROI 를 원본 이미지에서 256x256 으로 잘라 올 때 쓰는 아핀 변환.
//
// 반환은 행우선 2x3. (crop_x, crop_y) -> (src_x, src_y), 둘 다 픽셀 단위.
void cropTransform(const RotatedRect& roi, int src_width, int src_height,
                   int out_size, float matrix[6]);

// 6) 크롭 좌표의 랜드마크를 원본 이미지 좌표로 되돌린다.
//
// `raw` 는 모델의 [195] 출력. `count` 를 39 로 주면 보조점 33·34 번까지 나온다 -
// 다음 프레임 ROI 를 그 둘로 잡기 때문에 추적할 때 필요하다.
std::vector<Landmark> projectLandmarks(const float* raw, const RotatedRect& roi,
                                       int src_width, int src_height,
                                       int count = 33);

// 시그모이드. 로짓을 자르는 폭까지 MediaPipe 와 같게 둔다.
float sigmoidClipped(float logit);

}  // namespace deskmate

#endif  // DESKMATE_POSE_GEOMETRY_H
