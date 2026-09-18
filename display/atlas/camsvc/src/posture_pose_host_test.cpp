// 스켈레톤 자세 판정 포팅이 파이썬 원본과 같은 결과를 내는지 채점한다.
//
//     g++ -std=c++17 -O2 posture_pose.cpp posture_pose_host_test.cpp -o t
//     ./t ../test/pose_golden.txt
//
// 정답지는 `tools/gen_pose_golden.py` 가 원본(`tools/posture_pose.py`)을 돌려 만든다.
// **이 테스트가 깨지면 포팅이 틀린 것이다. 정답지를 고쳐서 맞추지 말 것.**
#include "posture_pose.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace {

// 정답지가 소수 6자리로 적혀 있으므로 그 이하로 두면 반올림 자체에 걸린다.
constexpr double kTolerance = 1e-5;

int failures = 0;

double parseNumber(const std::string& text) {
  if (text == "inf") return std::numeric_limits<double>::infinity();
  if (text == "nan") return std::numeric_limits<double>::quiet_NaN();
  return std::strtod(text.c_str(), nullptr);
}

void expectNear(const char* where, const std::string& field, double got,
                double want) {
  if (std::isinf(want) && std::isinf(got)) return;
  if (std::isnan(want) && std::isnan(got)) return;
  const double gap = std::fabs(got - want);
  if (gap <= kTolerance + kTolerance * std::fabs(want)) return;
  std::printf("  %s %s: 포팅 %.9f != 원본 %.9f\n", where, field.c_str(), got,
              want);
  ++failures;
}

void expectEq(const char* where, const std::string& field,
              const std::string& got, const std::string& want) {
  if (got == want) return;
  std::printf("  %s %s: 포팅 %s != 원본 %s\n", where, field.c_str(),
              got.c_str(), want.c_str());
  ++failures;
}

// "0:0.5:0.2 11:0.41:0.42 …" 을 랜드마크로.
deskmate::Landmarks parsePoints(std::istringstream& in) {
  deskmate::Landmarks pts;
  std::string token;
  while (in >> token) {
    if (token == "|") break;
    const auto first = token.find(':');
    const auto second = token.find(':', first + 1);
    if (first == std::string::npos || second == std::string::npos) continue;
    const int index = std::atoi(token.substr(0, first).c_str());
    deskmate::Point p;
    p.x = std::strtod(token.substr(first + 1, second - first - 1).c_str(), nullptr);
    p.y = std::strtod(token.substr(second + 1).c_str(), nullptr);
    pts[index] = p;
  }
  return pts;
}

}  // namespace

int main(int argc, char** argv) {
  const std::string path = argc > 1 ? argv[1] : "../test/pose_golden.txt";
  std::ifstream file(path);
  if (!file) {
    std::printf("정답지를 못 읽습니다: %s\n", path.c_str());
    return 2;
  }

  deskmate::PostureTracker tracker;
  std::string case_name = "(없음)";
  int steps = 0;
  int cases = 0;
  int case_failures = 0;
  std::string line;

  while (std::getline(file, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream in(line);
    std::string kind;
    in >> kind;

    if (kind == "CASE") {
      if (cases > 0) {
        std::printf("%-18s %s\n", case_name.c_str(),
                    case_failures == 0 ? "ok" : "다름");
      }
      in >> case_name;
      tracker = deskmate::PostureTracker();
      case_failures = failures;
      ++cases;
      continue;
    }

    if (kind == "REF") {
      const deskmate::Landmarks pts = parsePoints(in);
      if (!tracker.captureReference(pts)) {
        std::printf("  %s: 기준 자세를 못 잡았다\n", case_name.c_str());
        ++failures;
      }
      continue;
    }

    if (kind != "STEP") continue;

    double now = 0.0;
    in >> now;
    const deskmate::Landmarks pts = parsePoints(in);

    std::string label, candidate, held, drop, delta, scale, wrist, folded;
    in >> label >> candidate >> held >> drop >> delta >> scale >> wrist >> folded;

    const deskmate::State state = tracker.update(pts, now);
    char where[64];
    std::snprintf(where, sizeof(where), "%s[%d]", case_name.c_str(), steps);

    expectEq(where, "label", state.label, label);
    expectEq(where, "candidate", state.candidate, candidate);
    expectNear(where, "held_for", state.held_for, parseNumber(held));
    expectNear(where, "head_drop", state.metrics.head_drop, parseNumber(drop));
    expectNear(where, "head_drop_delta", state.metrics.head_drop_delta,
               parseNumber(delta));
    expectNear(where, "scale_ratio", state.metrics.scale_ratio,
               parseNumber(scale));
    expectNear(where, "wrist_to_head", state.metrics.wrist_to_head,
               parseNumber(wrist));
    expectEq(where, "arm_folded", state.metrics.arm_folded ? "1" : "0", folded);
    ++steps;
  }
  if (cases > 0) {
    std::printf("%-18s %s\n", case_name.c_str(),
                failures == case_failures ? "ok" : "다름");
  }

  std::printf("\n시나리오 %d개 · %d프레임 비교\n", cases, steps);
  if (failures != 0) {
    std::printf("차이 %d건 — 포팅이 원본과 다릅니다\n", failures);
    return 1;
  }
  std::printf("판정이 원본과 같다.\n");
  return 0;
}
