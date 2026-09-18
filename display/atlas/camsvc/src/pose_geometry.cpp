#include "pose_geometry.h"

#include <algorithm>
#include <cmath>

namespace deskmate {
namespace {

constexpr double kPi = 3.14159265358979323846;

double normalizeRadians(double angle) {
  return angle - 2.0 * kPi * std::floor((angle + kPi) / (2.0 * kPi));
}

double intersectionOverUnion(const Detection& a, const Detection& b) {
  const float ax2 = a.xmin + a.width;
  const float ay2 = a.ymin + a.height;
  const float bx2 = b.xmin + b.width;
  const float by2 = b.ymin + b.height;
  const float x1 = std::max(a.xmin, b.xmin);
  const float y1 = std::max(a.ymin, b.ymin);
  const float x2 = std::min(ax2, bx2);
  const float y2 = std::min(ay2, by2);
  if (x2 <= x1 || y2 <= y1) return 0.0;
  const double inter = static_cast<double>(x2 - x1) * (y2 - y1);
  const double area_a = static_cast<double>(a.width) * a.height;
  const double area_b = static_cast<double>(b.width) * b.height;
  const double denom = area_a + area_b - inter;
  return denom > 0.0 ? inter / denom : 0.0;
}

}  // namespace

float sigmoidClipped(float logit) {
  const double clipped =
      std::max(-kScoreClip, std::min(kScoreClip, static_cast<double>(logit)));
  return static_cast<float>(1.0 / (1.0 + std::exp(-clipped)));
}

std::vector<Anchor> buildAnchors() {
  std::vector<Anchor> anchors;
  anchors.reserve(kDetectorAnchors);

  int layer = 0;
  while (layer < kNumLayers) {
    // 같은 stride 를 쓰는 층을 묶는다. 셀 하나가 갖는 앵커 수는 그 층 수의 2배다
    // (종횡비 1.0 하나 + 다음 스케일과의 기하평균 하나). stride 가 32 인 층이
    // 셋이라 마지막 묶음만 셀당 6개가 된다.
    int last = layer;
    int per_cell = 0;
    while (last < kNumLayers && kStrides[last] == kStrides[layer]) {
      per_cell += 2;
      ++last;
    }

    const int stride = kStrides[layer];
    const int rows = (kDetectorSize + stride - 1) / stride;
    const int cols = rows;
    for (int y = 0; y < rows; ++y) {
      for (int x = 0; x < cols; ++x) {
        for (int i = 0; i < per_cell; ++i) {
          Anchor anchor;
          anchor.x_center = static_cast<float>((x + kAnchorOffsetX) / cols);
          anchor.y_center = static_cast<float>((y + kAnchorOffsetY) / rows);
          // fixed_anchor_size 라 스케일은 쓰지 않는다. 박스 크기는 모델이 낸다.
          anchor.w = 1.0f;
          anchor.h = 1.0f;
          anchors.push_back(anchor);
        }
      }
    }
    layer = last;
  }
  return anchors;
}

Detection decodeDetections(const float* boxes, const float* scores,
                           const std::vector<Anchor>& anchors) {
  std::vector<Detection> keep;
  const double scale = kDetectorSize;

  for (std::size_t i = 0; i < anchors.size(); ++i) {
    const float score = sigmoidClipped(scores[i]);
    if (score < kMinScoreThresh) continue;

    const float* raw = boxes + i * kDetectorCoords;
    const Anchor& anchor = anchors[i];
    // reverse_output_order: 앞의 넷이 x, y, w, h 순서다.
    const double cx = raw[0] / scale * anchor.w + anchor.x_center;
    const double cy = raw[1] / scale * anchor.h + anchor.y_center;
    const double w = raw[2] / scale * anchor.w;
    const double h = raw[3] / scale * anchor.h;

    Detection det;
    det.score = score;
    det.xmin = static_cast<float>(cx - w * 0.5);
    det.ymin = static_cast<float>(cy - h * 0.5);
    det.width = static_cast<float>(w);
    det.height = static_cast<float>(h);
    for (int k = 0; k < 4; ++k) {
      det.kp[k][0] = static_cast<float>(raw[4 + k * 2] / scale * anchor.w +
                                        anchor.x_center);
      det.kp[k][1] = static_cast<float>(raw[4 + k * 2 + 1] / scale * anchor.h +
                                        anchor.y_center);
    }
    keep.push_back(det);
  }
  if (keep.empty()) return Detection{};

  // 가중 NMS. 겹치는 것들을 점수로 평균 내므로 좌표가 프레임마다 덜 떨린다.
  std::sort(keep.begin(), keep.end(), [](const Detection& a, const Detection& b) {
    return a.score > b.score;
  });

  const Detection top = keep.front();
  double total = 0.0;
  Detection sum{};
  for (const Detection& other : keep) {
    if (intersectionOverUnion(top, other) <= kNmsIou) continue;
    total += other.score;
    sum.xmin += other.xmin * other.score;
    sum.ymin += other.ymin * other.score;
    sum.width += other.width * other.score;
    sum.height += other.height * other.score;
    for (int k = 0; k < 4; ++k) {
      sum.kp[k][0] += other.kp[k][0] * other.score;
      sum.kp[k][1] += other.kp[k][1] * other.score;
    }
  }
  if (total <= 0.0) return top;

  Detection out = top;  // 점수는 가장 높은 것을 그대로 쓴다
  out.xmin = static_cast<float>(sum.xmin / total);
  out.ymin = static_cast<float>(sum.ymin / total);
  out.width = static_cast<float>(sum.width / total);
  out.height = static_cast<float>(sum.height / total);
  for (int k = 0; k < 4; ++k) {
    out.kp[k][0] = static_cast<float>(sum.kp[k][0] / total);
    out.kp[k][1] = static_cast<float>(sum.kp[k][1] / total);
  }
  return out;
}

Letterbox computeLetterbox(int src_width, int src_height) {
  Letterbox pad;
  if (src_width <= 0 || src_height <= 0) return pad;
  // 긴 쪽이 1.0 에 꽉 차도록 넣는다. 160x120 이면 위아래에 0.125 씩 남는다.
  const double scale = std::min(1.0 / src_width, 1.0 / src_height);
  const double fit_w = src_width * scale;
  const double fit_h = src_height * scale;
  pad.left = pad.right = static_cast<float>((1.0 - fit_w) * 0.5);
  pad.top = pad.bottom = static_cast<float>((1.0 - fit_h) * 0.5);
  return pad;
}

void removeLetterbox(Detection& det, const Letterbox& pad) {
  const double span_x = 1.0 - (pad.left + pad.right);
  const double span_y = 1.0 - (pad.top + pad.bottom);
  if (span_x <= 0.0 || span_y <= 0.0) return;

  det.xmin = static_cast<float>((det.xmin - pad.left) / span_x);
  det.ymin = static_cast<float>((det.ymin - pad.top) / span_y);
  det.width = static_cast<float>(det.width / span_x);
  det.height = static_cast<float>(det.height / span_y);
  for (int k = 0; k < 4; ++k) {
    det.kp[k][0] = static_cast<float>((det.kp[k][0] - pad.left) / span_x);
    det.kp[k][1] = static_cast<float>((det.kp[k][1] - pad.top) / span_y);
  }
}

RotatedRect roiFromDetection(const Detection& det, int src_width,
                             int src_height, double roi_scale) {
  RotatedRect rect;
  if (src_width <= 0 || src_height <= 0) return rect;

  // AlignmentPointsRectsCalculator: 중심은 키포인트 0, 크기는 0->1 거리의 2배.
  const double cx = static_cast<double>(det.kp[0][0]) * src_width;
  const double cy = static_cast<double>(det.kp[0][1]) * src_height;
  const double dx = static_cast<double>(det.kp[1][0]) * src_width - cx;
  const double dy = static_cast<double>(det.kp[1][1]) * src_height - cy;
  const double size = std::sqrt(dx * dx + dy * dy) * 2.0;

  rect.x_center = static_cast<float>(cx / src_width);
  rect.y_center = static_cast<float>(cy / src_height);
  rect.rotation = static_cast<float>(
      normalizeRadians(kTargetAngleDegrees * kPi / 180.0 - std::atan2(-dy, dx)));

  // RectTransformation: square_long 이라 픽셀 기준 정사각형으로 만든 뒤 1.25배.
  const double side = size * roi_scale;
  rect.width = static_cast<float>(side / src_width);
  rect.height = static_cast<float>(side / src_height);
  return rect;
}

void cropTransform(const RotatedRect& roi, int src_width, int src_height,
                   int out_size, float matrix[6]) {
  const double cx = static_cast<double>(roi.x_center) * src_width;
  const double cy = static_cast<double>(roi.y_center) * src_height;
  const double w = static_cast<double>(roi.width) * src_width;
  const double h = static_cast<double>(roi.height) * src_height;
  const double a = std::cos(roi.rotation);
  const double b = std::sin(roi.rotation);
  // 크롭 픽셀 u 의 중심은 (u + 0.5) / out_size - 0.5 자리에 해당한다.
  const double bias = 0.5 / out_size - 0.5;

  matrix[0] = static_cast<float>(w * a / out_size);
  matrix[1] = static_cast<float>(-h * b / out_size);
  matrix[2] = static_cast<float>(cx + bias * (w * a - h * b));
  matrix[3] = static_cast<float>(w * b / out_size);
  matrix[4] = static_cast<float>(h * a / out_size);
  matrix[5] = static_cast<float>(cy + bias * (w * b + h * a));
}

std::vector<Landmark> projectLandmarks(const float* raw, const RotatedRect& roi,
                                       int src_width, int src_height,
                                       int count) {
  // LandmarkProjectionCalculator 는 정규화 좌표에서 되돌린다. 크롭은 픽셀
  // 기준으로 돌렸으므로 정사각형이 아닌 이미지에서는 둘이 정확히 맞지 않는데,
  // MediaPipe 자체가 그렇게 동작한다. 원본과 같은 값을 내는 것이 목적이라
  // 여기서도 고치지 않는다.
  (void)src_width;
  (void)src_height;

  if (count < 0) count = 0;
  if (count > kLandmarkCount) count = kLandmarkCount;
  std::vector<Landmark> out;
  out.reserve(count);

  const double a = std::cos(roi.rotation);
  const double b = std::sin(roi.rotation);
  for (int i = 0; i < count; ++i) {
    const float* v = raw + i * kLandmarkValues;
    // 모델은 256 입력의 픽셀 단위로 낸다. 0~1 로 되돌린 뒤 중심 기준으로 돌린다.
    const double x = v[0] / kLandmarkSize - 0.5;
    const double y = v[1] / kLandmarkSize - 0.5;

    Landmark lm;
    lm.x = static_cast<float>((a * x - b * y) * roi.width + roi.x_center);
    lm.y = static_cast<float>((b * x + a * y) * roi.height + roi.y_center);
    lm.z = static_cast<float>(v[2] / kLandmarkSize * roi.width);
    lm.visibility = sigmoidClipped(v[3]);
    lm.presence = sigmoidClipped(v[4]);
    out.push_back(lm);
  }
  return out;
}

}  // namespace deskmate
