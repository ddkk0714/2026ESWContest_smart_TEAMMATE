// 포즈 기하 계산을 보드 없이 검사한다. TFLite 도 모델도 필요 없다.
//
//     g++ -std=c++17 -O2 pose_geometry.cpp pose_geometry_host_test.cpp -o t && ./t
//
// 여기서 보는 것은 "모델 앞뒤 계산이 제정신인가" 뿐이다. 숫자가 MediaPipe 와
// 같은지는 실제 모델을 돌려 랜드마크를 맞춰 봐야 안다(`tools/gen_pose_landmarks_golden.py`).
// 다만 앵커 수·레터박스·좌표 왕복은 여기서 틀리면 그쪽도 볼 것 없이 어긋난다.
#include "pose_geometry.h"

#include <cmath>
#include <cstdio>
#include <vector>

namespace {

int failures = 0;

void check(bool ok, const char* what) {
  std::printf("  %-46s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) ++failures;
}

void checkNear(double got, double want, double tol, const char* what) {
  const bool ok = std::fabs(got - want) <= tol;
  std::printf("  %-46s %s", what, ok ? "ok" : "FAIL");
  if (!ok) std::printf("  (%.9f != %.9f)", got, want);
  std::printf("\n");
  if (!ok) ++failures;
}

// 검출기 출력 두 장을 흉내 낸다. 지정한 앵커에만 점수를 준다.
struct FakeTensors {
  std::vector<float> boxes;
  std::vector<float> scores;

  FakeTensors()
      : boxes(deskmate::kDetectorAnchors * deskmate::kDetectorCoords, 0.0f),
        scores(deskmate::kDetectorAnchors, -20.0f) {}

  // 중심 오프셋과 크기는 224 픽셀 단위로 준다(모델이 그렇게 낸다).
  void put(int index, float logit, float dx, float dy, float w, float h) {
    scores[index] = logit;
    float* raw = boxes.data() + index * deskmate::kDetectorCoords;
    raw[0] = dx;
    raw[1] = dy;
    raw[2] = w;
    raw[3] = h;
    // 키포인트 0 은 중심, 1 은 그 위로 몸 크기만큼.
    raw[4] = dx;
    raw[5] = dy;
    raw[6] = dx;
    raw[7] = dy - h * 0.5f;
  }
};

}  // namespace

int main() {
  std::printf("앵커\n");
  const std::vector<deskmate::Anchor> anchors = deskmate::buildAnchors();
  check(anchors.size() == deskmate::kDetectorAnchors,
        "앵커가 2254개다 (모델이 기대하는 수)");
  if (anchors.size() == deskmate::kDetectorAnchors) {
    // stride 8 -> 28x28 격자, 셀당 2개.
    checkNear(anchors[0].x_center, 0.5 / 28.0, 1e-6, "첫 앵커는 28격자 첫 셀 중심");
    check(anchors[1].x_center == anchors[0].x_center &&
              anchors[1].y_center == anchors[0].y_center,
          "셀마다 앵커가 둘씩 겹쳐 있다");
    checkNear(anchors[2].x_center, 1.5 / 28.0, 1e-6, "셋째 앵커는 다음 셀로 넘어간다");
    // 1568 = 28*28*2 부터 stride 16 층.
    checkNear(anchors[1568].x_center, 0.5 / 14.0, 1e-6, "1568번부터 14격자");
    // 1960 = 1568 + 14*14*2 부터 stride 32 층 셋이 묶여 셀당 6개.
    checkNear(anchors[1960].x_center, 0.5 / 7.0, 1e-6, "1960번부터 7격자");
    bool six_same = true;
    for (int i = 1; i < 6; ++i) {
      if (anchors[1960 + i].x_center != anchors[1960].x_center) six_same = false;
    }
    check(six_same && anchors[1966].x_center != anchors[1960].x_center,
          "stride 32 층 셋이 묶여 셀당 6개");
    check(anchors[0].w == 1.0f && anchors[0].h == 1.0f,
          "fixed_anchor_size 라 크기는 1.0");
  }

  std::printf("\n점수와 디코딩\n");
  checkNear(deskmate::sigmoidClipped(0.0f), 0.5, 1e-6, "시그모이드 0 -> 0.5");
  check(deskmate::sigmoidClipped(-1000.0f) >= 0.0f &&
            std::isfinite(deskmate::sigmoidClipped(-1000.0f)),
        "큰 음수 로짓에서 터지지 않는다");

  {
    FakeTensors t;  // 아무도 문턱을 못 넘는다
    const deskmate::Detection det =
        deskmate::decodeDetections(t.boxes.data(), t.scores.data(), anchors);
    check(det.score == 0.0f, "점수가 낮으면 검출 없음");
  }

  {
    FakeTensors t;
    const float side = 0.2f * deskmate::kDetectorSize;  // 폭·높이 0.2
    t.put(0, 4.0f, 0.0f, 0.0f, side, side);
    const deskmate::Detection det =
        deskmate::decodeDetections(t.boxes.data(), t.scores.data(), anchors);
    checkNear(det.width, 0.2, 1e-6, "박스 폭이 224 픽셀 단위에서 환산된다");
    checkNear(det.xmin + det.width * 0.5, anchors[0].x_center, 1e-6,
              "박스 중심이 앵커 중심에 실린다");
    checkNear(det.kp[0][1], anchors[0].y_center, 1e-6, "키포인트도 앵커 기준이다");
  }

  {
    // 겹치는 둘을 점수로 평균 낸다. 0번과 1번은 같은 셀이라 겹친다.
    FakeTensors t;
    const float side = 0.2f * deskmate::kDetectorSize;
    const float shift = 0.02f * deskmate::kDetectorSize;
    t.put(0, 2.0f, 0.0f, 0.0f, side, side);
    t.put(1, 1.0f, shift, 0.0f, side, side);
    const deskmate::Detection det =
        deskmate::decodeDetections(t.boxes.data(), t.scores.data(), anchors);

    const double s0 = deskmate::sigmoidClipped(2.0f);
    const double s1 = deskmate::sigmoidClipped(1.0f);
    const double x0 = anchors[0].x_center - 0.1;
    const double x1 = anchors[1].x_center + 0.02 - 0.1;
    checkNear(det.xmin, (x0 * s0 + x1 * s1) / (s0 + s1), 1e-6,
              "겹친 박스를 점수 가중으로 평균한다");
    checkNear(det.score, s0, 1e-6, "점수는 가장 높은 것을 쓴다");
  }

  {
    // 문턱을 넘어도 안 겹치면 끌려오지 않는다.
    FakeTensors t;
    const float side = 0.05f * deskmate::kDetectorSize;
    t.put(0, 2.0f, 0.0f, 0.0f, side, side);
    t.put(2000, 1.0f, 0.0f, 0.0f, side, side);
    const deskmate::Detection det =
        deskmate::decodeDetections(t.boxes.data(), t.scores.data(), anchors);
    checkNear(det.xmin + det.width * 0.5, anchors[0].x_center, 1e-6,
              "멀리 떨어진 검출은 평균에 섞이지 않는다");
  }

  std::printf("\n레터박스\n");
  {
    const deskmate::Letterbox pad = deskmate::computeLetterbox(160, 120);
    checkNear(pad.left, 0.0, 1e-6, "160x120 은 좌우 여백이 없다");
    checkNear(pad.top, 0.125, 1e-6, "위아래에 0.125 씩 남는다");

    deskmate::Detection det;
    det.ymin = 0.125f;
    det.height = 0.75f;
    det.xmin = 0.25f;
    det.width = 0.5f;
    det.kp[0][1] = 0.5f;
    deskmate::removeLetterbox(det, pad);
    checkNear(det.ymin, 0.0, 1e-6, "여백을 빼면 세로가 0 에서 시작한다");
    checkNear(det.height, 1.0, 1e-6, "세로가 이미지 전체로 펴진다");
    checkNear(det.xmin, 0.25, 1e-6, "가로는 건드리지 않는다");
    checkNear(det.kp[0][1], 0.5, 1e-6, "가운데 키포인트는 가운데로 남는다");
  }

  std::printf("\nROI\n");
  {
    // 키포인트 1 이 0 의 바로 위 -> 사람이 서 있다 -> 회전 0.
    deskmate::Detection det;
    det.kp[0][0] = 0.5f;
    det.kp[0][1] = 0.7f;
    det.kp[1][0] = 0.5f;
    det.kp[1][1] = 0.3f;
    const deskmate::RotatedRect roi = deskmate::roiFromDetection(det, 200, 200);
    checkNear(roi.rotation, 0.0, 1e-6, "똑바로 선 사람은 회전이 0");
    checkNear(roi.x_center, 0.5, 1e-6, "ROI 중심은 키포인트 0");
    checkNear(roi.y_center, 0.7, 1e-6, "ROI 중심 세로도 키포인트 0");
    // 거리 80px * 2 = 160, 거기에 검출 확대율.
    checkNear(roi.width * 200.0, 160.0 * deskmate::kDetectionRoiScale, 1e-4,
              "크기는 두 점 거리의 2배에 검출 확대율");
    checkNear(roi.height * 200.0, roi.width * 200.0, 1e-4, "픽셀 기준 정사각형이다");
  }
  {
    // 오른쪽으로 누운 사람 -> 90도.
    deskmate::Detection det;
    det.kp[0][0] = 0.5f;
    det.kp[0][1] = 0.5f;
    det.kp[1][0] = 0.9f;
    det.kp[1][1] = 0.5f;
    const deskmate::RotatedRect roi = deskmate::roiFromDetection(det, 200, 200);
    checkNear(roi.rotation, M_PI / 2.0, 1e-6, "옆으로 누우면 90도로 돈다");
  }

  {
    // 확대율은 인자로 들어간다. 이 값이 틀리면 랜드마크가 통째로 축소되거나
    // 확대돼 찍히는데, 모양은 멀쩡해서 화면만 봐서는 안 보인다 - 그래서 여기서
    // 상수가 실제로 전달되는지만 잡아 두고, 값 자체는 정답지로 잰다.
    deskmate::Detection det;
    det.kp[0][0] = 0.5f;
    det.kp[0][1] = 0.7f;
    det.kp[1][0] = 0.5f;
    det.kp[1][1] = 0.3f;
    const deskmate::RotatedRect wide =
        deskmate::roiFromDetection(det, 200, 200, deskmate::kDetectionRoiScale);
    const deskmate::RotatedRect narrow =
        deskmate::roiFromDetection(det, 200, 200, deskmate::kLandmarkRoiScale);
    check(deskmate::kDetectionRoiScale >= deskmate::kLandmarkRoiScale,
          "첫 ROI 가 추적 ROI 보다 좁지는 않다");
    checkNear(wide.width / narrow.width,
              deskmate::kDetectionRoiScale / deskmate::kLandmarkRoiScale, 1e-5,
              "확대율이 인자로 그대로 들어간다");
  }

  std::printf("\n크롭과 되돌리기\n");
  {
    // 잘라 올 때 쓴 변환과 되돌릴 때 쓴 계산이 서로 역이어야 한다. 아니면
    // 랜드마크가 통째로 밀린 자리에 찍히는데, 판정은 그래도 돌아가서 안 보인다.
    // 정사각형 이미지에서만 정확히 맞는다(윗 주석 참고).
    const int src = 240;
    deskmate::RotatedRect roi;
    roi.x_center = 0.5f;
    roi.y_center = 0.45f;
    roi.width = 0.4f;
    roi.height = 0.4f;
    roi.rotation = 0.3f;

    float m[6];
    deskmate::cropTransform(roi, src, src, deskmate::kLandmarkSize, m);

    bool all_ok = true;
    double worst = 0.0;
    const int probes[][2] = {{0, 0}, {100, 70}, {255, 255}, {128, 10}};
    for (const auto& p : probes) {
      const double u = p[0];
      const double v = p[1];
      const double want_x = m[0] * u + m[1] * v + m[2];
      const double want_y = m[3] * u + m[4] * v + m[5];

      std::vector<float> raw(33 * deskmate::kLandmarkValues, 0.0f);
      raw[0] = static_cast<float>(u + 0.5);
      raw[1] = static_cast<float>(v + 0.5);
      const std::vector<deskmate::Landmark> lm =
          deskmate::projectLandmarks(raw.data(), roi, src, src);
      const double got_x = lm[0].x * src;
      const double got_y = lm[0].y * src;
      worst = std::max(worst, std::fabs(got_x - want_x));
      worst = std::max(worst, std::fabs(got_y - want_y));
      if (std::fabs(got_x - want_x) > 2e-3 || std::fabs(got_y - want_y) > 2e-3) {
        all_ok = false;
      }
    }
    std::printf("  %-46s %s  (최대 %.2e px)\n", "크롭 좌표를 원본 좌표로 되돌린다",
                all_ok ? "ok" : "FAIL", worst);
    if (!all_ok) ++failures;

    std::vector<float> raw(33 * deskmate::kLandmarkValues, 0.0f);
    raw[3] = 3.0f;  // visibility 로짓
    const std::vector<deskmate::Landmark> lm =
        deskmate::projectLandmarks(raw.data(), roi, src, src);
    check(lm.size() == 33, "랜드마크는 앞의 33개만 쓴다");
    checkNear(lm[0].visibility, deskmate::sigmoidClipped(3.0f), 1e-6,
              "visibility 에 시그모이드를 건다");
  }

  std::printf("\n%s\n", failures == 0 ? "전부 통과" : "실패 있음");
  return failures == 0 ? 0 : 1;
}
