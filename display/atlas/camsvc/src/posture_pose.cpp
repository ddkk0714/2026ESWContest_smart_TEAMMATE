#include "posture_pose.h"

#include <limits>

namespace deskmate {
namespace {

double dist(const Point& a, const Point& b) {
  return std::hypot(a.x - b.x, a.y - b.y);
}

const Point* find(const Landmarks& pts, int index) {
  const auto it = pts.find(index);
  return it == pts.end() ? nullptr : &it->second;
}

}  // namespace

Metrics measure(const Landmarks& pts) {
  Metrics m;
  const Point* ls = find(pts, kLeftShoulder);
  const Point* rs = find(pts, kRightShoulder);
  const Point* nose = find(pts, kNose);
  if (ls == nullptr || rs == nullptr || nose == nullptr) return m;

  const double width = dist(*ls, *rs);
  if (width < 1e-6) return m;

  const double mid_y = (ls->y + rs->y) / 2.0;
  m.shoulder_width = width;
  m.head_drop = (nose->y - mid_y) / width;

  double near = std::numeric_limits<double>::infinity();
  bool folded = false;
  const int arms[2][3] = {{kLeftWrist, kLeftElbow, kLeftShoulder},
                          {kRightWrist, kRightElbow, kRightShoulder}};
  for (const auto& arm : arms) {
    const Point* wrist = find(pts, arm[0]);
    if (wrist == nullptr) continue;
    const double d = dist(*wrist, *nose) / width;
    if (d < near) {
      near = d;
      // y 는 아래로 커진다. 팔꿈치가 어깨보다 아래면 접힌 팔 - 책상에 괸 것이다.
      // 손을 들어 흔들어도 손목이 머리 근처를 지나지만 그때는 팔꿈치가 위에 있다.
      const Point* elbow = find(pts, arm[1]);
      const Point* shoulder = find(pts, arm[2]);
      folded = elbow != nullptr && shoulder != nullptr && elbow->y > shoulder->y;
    }
  }
  m.wrist_to_head = near;
  m.arm_folded = folded;
  m.valid = true;
  return m;
}

bool PostureTracker::captureReference(const Landmarks& pts) {
  const Metrics m = measure(pts);
  if (!m.valid) return false;
  ref_ = m;
  was_dropped_ = was_chin_ = was_receded_ = false;
  return true;
}

State PostureTracker::update(const Landmarks& pts, double now) {
  Metrics m = pts.empty() ? Metrics{} : measure(pts);
  if (!m.valid) {
    commit(kAbsent, now);
    State out;
    out.label = label_;
    out.candidate = kAbsent;
    out.held_for = held(now);
    out.has_reference = ref_.valid;
    out.note = "no shoulders in frame";
    return out;
  }

  if (!ref_.valid) {
    label_ = kUpright;
    State out;
    out.label = kUpright;
    out.candidate = kUpright;
    out.held_for = 0.0;
    out.metrics = m;
    out.has_reference = false;
    out.note = "no reference captured";
    return out;
  }

  m.head_drop_delta = m.head_drop - ref_.head_drop;
  m.scale_ratio = m.shoulder_width / ref_.shoulder_width;

  std::string note;
  const std::string candidate = classify(m, note);

  commit(candidate, now);
  State out;
  out.label = label_;
  out.candidate = candidate;
  out.held_for = held(now);
  out.metrics = m;
  out.has_reference = true;
  out.note = note;
  return out;
}

std::string PostureTracker::classify(const Metrics& m, std::string& note) {
  // 두 스위치 모두 히스테리시스를 둔다. 문턱에 걸친 손목이나 그 위를 오르내리는
  // 머리는 매 프레임 라벨을 뒤집어 hold 타이머가 영영 안 찬다.
  const double near = was_chin_ ? kChinRelease : kChinNear;
  was_chin_ = m.wrist_to_head < near && m.arm_folded;

  const double drop_gate = was_dropped_ ? kSlumpDropOff : kSlumpDropOn;
  was_dropped_ = m.head_drop_delta > drop_gate;

  const double shrink_gate = was_receded_ ? kShrinkOff : kShrinkOn;
  was_receded_ = m.scale_ratio < shrink_gate;

  if (was_chin_ && !was_dropped_) {
    note = "wrist beside the head, elbow dropped";
    return kChinRest;
  }

  // 젖힘은 어깨가 물러나는 것만으로 읽는다.
  //
  // 원래는 목 단축도 같이 요구했는데 실기에서 두 번 틀렸다 - 그 부호는 카메라
  // 높이에 달려 있고(책상보다 낮은 카메라는 젖힐 때 목이 길어 보인다), 크기도
  // 어깨 변화에 묻힌다. 누운 사람이 기준 어깨폭의 0.19 로 측정됐는데 목 항이
  // 반대로 가서 거부했다.
  //
  // 의자만 뒤로 민 것도 이제 RECLINE 으로 읽힌다. 그쪽이 나은 실수다 - 드물고
  // 짧으며 hold 타이머가 대부분 흡수하는 반면, 진짜 젖힘을 놓치면 그 상태는
  // 영영 보고되지 않는다.
  if (was_receded_) {
    note = "shoulders narrower - further from the camera";
    return kRecline;
  }

  if (was_dropped_) {
    note = "head down to the shoulder line, shoulders where they were";
    return kSlump;
  }

  note.clear();
  return kUpright;
}

void PostureTracker::commit(const std::string& candidate, double now) {
  // 자세는 자리를 지킨 뒤에야 보고한다. 꾸벅임의 바닥은 기하가 엎드림과 같고,
  // 둘은 모양이 아니라 시간으로 갈린다. 한 프레임짜리 오류도 여기서 걸러진다.
  if (candidate != seen_) {
    seen_ = candidate;
    since_ = now;
    since_set_ = true;
  }
  if (since_set_ && now - since_ >= hold_) {
    label_ = candidate;
  }
}

double PostureTracker::held(double now) const {
  if (!since_set_) return 0.0;
  const double elapsed = now - since_;
  return elapsed < 0.0 ? 0.0 : elapsed;
}

}  // namespace deskmate
