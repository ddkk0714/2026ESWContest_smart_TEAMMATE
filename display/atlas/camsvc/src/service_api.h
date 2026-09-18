// 앱이 읽는 HTTP API. 127.0.0.1 에만 연다.
//
// 앱(`display/atlas/app`)에는 이미 Pi 4 자세 노드를 긁던 클라이언트가 있다 -
// `/api/state` · `/health` · `/api/calibrate`. 그 셋을 여기서 그대로 낸다.
// **봉투 모양을 바꾸지 말 것**: `lib/posture_state.dart` 의 `fromEnvelope` 가
// `schema_version` 이 "1.0" 이 아니면 던진다.
//
// D-Bus 가 아니라 HTTP 인 이유는, 앱이 이미 이 길을 알고 있고 ATLAS 앱 사이의
// D-Bus 정책·AppArmor 를 새로 뚫지 않아도 되기 때문이다. hub 쪽 native service
// 도 같은 이유로 8765 를 쓴다.
#ifndef DESKMATE_SERVICE_API_H
#define DESKMATE_SERVICE_API_H

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace deskmate {

// 8765 는 Pi 4 자세 노드가 쓰는 번호다. 보드에서 그 포트로 SSH 포워딩이 살아
// 있는 것을 봤으므로(남의 세션을 끊을 수는 없다) camsvc 는 자기 번호를 쓴다.
// 바꾸려면 CAMSVC_PORT.
inline constexpr int kDefaultPort = 8770;

// 서비스가 지금까지 본 것. HTTP 스레드와 판정 루프가 같이 만진다.
class ServiceState {
 public:
  // 판정 루프가 부른다.
  void publish(const std::string& label, const std::string& candidate,
               bool present, bool valid, double held_for, double head_drop,
               double scale_ratio, const std::string& note);

  // 링크·성능 수치. 화면 아래 한 줄에 그대로 나간다.
  // `best_score` 는 검출기가 마지막으로 본 최고 점수, `presence` 는 랜드마크
  // 모델의 존재 점수다. 사람을 못 찾을 때 어디서 떨어졌는지 이것으로 가른다.
  void publishHealth(const std::string& port, std::size_t frames,
                     std::size_t dropped, std::size_t crc_errors, double fps,
                     double model_ms, bool model_ready, double best_score,
                     double presence, double frame_mean, double frame_stddev);

  // 앱이 기준 자세를 다시 잡으라고 했는지. 판정 루프가 가져가면서 내린다.
  void requestCalibration() { calibrate_requested_ = true; }
  bool takeCalibrationRequest() { return calibrate_requested_.exchange(false); }

  // 마지막 preview 한 장과 그 위의 랜드마크. 화면이 "내가 지금 어떻게 보이나" 를
  // 확인하는 데 쓴다.
  //
  // **그림과 뼈대를 한 번에 담는다.** 따로 내주면 화면이 두 번 물어야 하고 그
  // 사이에 프레임이 바뀌어 뼈대가 엉뚱한 자리에 얹힌다.
  //
  // 바이트 규약 (전부 리틀엔디안):
  //   u16 width | u16 height | u16 landmark_count
  //   landmark_count x (u16 x, u16 y)   0..65535 가 0..1 에 대응
  //   width*height 바이트 회색값
  void publishPreview(const std::uint8_t* grey, int width, int height,
                      const std::vector<std::pair<float, float>>& points);

  std::string previewBody() const;
  bool hasPreview() const { return has_preview_; }

  // `/api/state` 봉투. 아직 한 장도 못 봤으면 비어 있다.
  std::string stateJson() const;
  bool hasState() const { return has_state_; }

  // `/health` 본문.
  std::string healthJson() const;

 private:
  mutable std::mutex mutex_;
  std::string state_json_;
  std::string preview_body_;
  std::atomic<bool> has_preview_{false};
  std::string health_json_ = "{\"status\":\"starting\"}";
  std::atomic<bool> has_state_{false};
  std::atomic<bool> calibrate_requested_{false};
};

// 127.0.0.1:port 에서 요청을 받는다. `stop` 이 서면 돌아온다.
// 열지 못하면 false 를 돌려주고 이유를 `error` 에 남긴다.
bool runHttpServer(int port, ServiceState& state, const std::atomic<bool>& stop,
                   std::string& error);

}  // namespace deskmate

#endif  // DESKMATE_SERVICE_API_H
