#include "service_api.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <sstream>

namespace deskmate {
namespace {

double nowSeconds() {
  using namespace std::chrono;
  return duration<double>(system_clock::now().time_since_epoch()).count();
}

// 판정 라벨을 앱이 아는 이름으로. 앱 쪽 `_detailNames` 와 맞는 소문자다.
std::string detailName(const std::string& label) {
  if (label == "UPRIGHT") return "upright";
  if (label == "SLUMP") return "slump";
  if (label == "RECLINE") return "recline";
  if (label == "CHIN_REST") return "chin_rest";
  if (label == "ABSENT") return "absent";
  if (label == "BASELINE") return "baseline";
  return "unknown";
}

// 계약 enum(`docs/data-spec.md` 6.1). 여기에는 턱괴기가 없어서 앞으로 기운 것으로
// 좁혀 보낸다. 앱은 `posture_detail` 을 먼저 보므로 좁혀진 값은 예비다.
std::string contractName(const std::string& label) {
  if (label == "UPRIGHT") return "upright";
  if (label == "SLUMP") return "lean_forward";
  if (label == "CHIN_REST") return "lean_forward";
  if (label == "RECLINE") return "lean_back";
  if (label == "ABSENT") return "away";
  return "unknown";
}

std::string escape(const std::string& text) {
  std::string out;
  out.reserve(text.size() + 8);
  for (const char c : text) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out += c;
        }
    }
  }
  return out;
}

// epoch 초는 유효숫자가 10자리라 %.6g 로 적으면 1.78968e+09 로 뭉개진다.
// 앱이 이 값으로 데이터 나이를 재므로 밀리초까지 남긴다.
std::string timestamp(double seconds) {
  char buf[40];
  std::snprintf(buf, sizeof(buf), "%.3f", seconds);
  return buf;
}

// 유한하지 않은 값은 null 로 보낸다. 앱이 '못 잰 값'과 0 을 구분한다.
std::string number(double value) {
  if (!std::isfinite(value)) return "null";
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.6g", value);
  return buf;
}

void sendResponse(int fd, int code, const char* reason,
                  const std::string& body,
                  const char* type = "application/json; charset=utf-8") {
  std::ostringstream head;
  head << "HTTP/1.1 " << code << ' ' << reason << "\r\n"
       << "Content-Type: " << type << "\r\n"
       << "Content-Length: " << body.size() << "\r\n"
       << "Cache-Control: no-store\r\n"
       << "Connection: close\r\n\r\n";
  const std::string text = head.str() + body;
  std::size_t sent = 0;
  while (sent < text.size()) {
    const ssize_t n = ::send(fd, text.data() + sent, text.size() - sent, 0);
    if (n <= 0) return;
    sent += static_cast<std::size_t>(n);
  }
}

void handleClient(int fd, ServiceState& state) {
  // 요청 줄만 보면 된다. 헤더는 안 읽고 버린다.
  char buf[2048];
  const ssize_t n = ::recv(fd, buf, sizeof(buf) - 1, 0);
  if (n <= 0) return;
  buf[n] = '\0';

  std::istringstream request(buf);
  std::string method, path;
  request >> method >> path;

  if (method == "GET" && path == "/health") {
    sendResponse(fd, 200, "OK", state.healthJson());
    return;
  }
  if (method == "GET" && (path == "/api/state" || path == "/state")) {
    if (!state.hasState()) {
      // 앱이 이 503 을 '아직 첫 판정 전'으로 읽는다. 연결 실패와 구분된다.
      sendResponse(fd, 503, "Service Unavailable",
                   "{\"error\":\"아직 첫 판정이 없습니다\"}");
      return;
    }
    sendResponse(fd, 200, "OK", state.stateJson());
    return;
  }
  if (method == "GET" && (path == "/preview.raw" || path == "/preview")) {
    if (!state.hasPreview()) {
      sendResponse(fd, 503, "Service Unavailable",
                   "{\"error\":\"아직 프레임이 없습니다\"}");
      return;
    }
    sendResponse(fd, 200, "OK", state.previewBody(), "application/octet-stream");
    return;
  }
  if (method == "POST" && (path == "/api/calibrate" || path == "/calibrate")) {
    state.requestCalibration();
    // **202 여야 한다.** 앱(`posture_source.dart`)은 202 가 아니면 실패로 보고
    // 화면에 "기준 다시 잡기에 실패했습니다" 를 띄운다 - 기준은 멀쩡히 잡히는데
    // 사람은 실패했다고 읽는다. Pi 4 자세 노드가 정한 규약이다.
    sendResponse(fd, 202, "Accepted", "{\"ok\":true}");
    return;
  }
  sendResponse(fd, 404, "Not Found", "{\"error\":\"없는 경로\"}");
}

}  // namespace

void ServiceState::publish(const std::string& label,
                           const std::string& candidate, bool present,
                           bool valid, double held_for, double head_drop,
                           double scale_ratio, const std::string& note) {
  static std::size_t sequence = 0;
  ++sequence;

  std::ostringstream out;
  out << "{\"schema_version\":\"1.0\""
      << ",\"seq\":" << sequence
      << ",\"ts\":" << timestamp(nowSeconds())
      << ",\"node\":\"camsvc\""
      << ",\"data\":{"
      << "\"fsm_state\":\"POSTURE_" << detailName(label) << '"'
      << ",\"c_focus\":0,\"c_fatigue\":0"
      << ",\"reasons\":[";
  if (!note.empty()) out << '"' << escape(note) << '"';
  out << "],\"sensor_summary\":{"
      << "\"present\":" << (present ? "true" : "false")
      << ",\"scenario\":\"" << escape(note) << '"'
      << ",\"posture\":{"
      << "\"posture_detail\":\"" << detailName(label) << '"'
      << ",\"posture\":\"" << contractName(label) << '"'
      << ",\"candidate\":\"" << detailName(candidate) << '"'
      << ",\"present\":" << (present ? "true" : "false")
      << ",\"valid\":" << (valid ? "true" : "false")
      << ",\"held_for\":" << number(held_for)
      << ",\"head_drop\":" << number(head_drop)
      << ",\"scale_ratio\":" << number(scale_ratio)
      << ",\"motion_score\":0,\"coverage_ratio\":0"
      << ",\"phi\":0,\"delta\":0"
      << ",\"head_delta_mm\":null,\"nod_rate_hz\":null"
      << "}}}}";

  std::lock_guard<std::mutex> guard(mutex_);
  state_json_ = out.str();
  has_state_ = true;
}

void ServiceState::publishPreview(
    const std::uint8_t* grey, int width, int height,
    const std::vector<std::pair<float, float>>& points) {
  if (grey == nullptr || width <= 0 || height <= 0) return;

  const std::size_t pixels = static_cast<std::size_t>(width) * height;
  std::string body;
  body.reserve(6 + points.size() * 4 + pixels);

  auto push16 = [&body](unsigned value) {
    body.push_back(static_cast<char>(value & 0xFF));
    body.push_back(static_cast<char>((value >> 8) & 0xFF));
  };
  push16(static_cast<unsigned>(width));
  push16(static_cast<unsigned>(height));
  push16(static_cast<unsigned>(points.size()));
  for (const auto& point : points) {
    // 화면 밖으로 나간 랜드마크가 흔하다(몸이 프레임을 벗어난다). 안 자르면
    // u16 이 넘쳐 반대쪽 끝에 찍힌다.
    const double x = std::min(std::max(static_cast<double>(point.first), 0.0), 1.0);
    const double y = std::min(std::max(static_cast<double>(point.second), 0.0), 1.0);
    push16(static_cast<unsigned>(x * 65535.0 + 0.5));
    push16(static_cast<unsigned>(y * 65535.0 + 0.5));
  }
  body.append(reinterpret_cast<const char*>(grey), pixels);

  std::lock_guard<std::mutex> guard(mutex_);
  preview_body_ = std::move(body);
  has_preview_ = true;
}

std::string ServiceState::previewBody() const {
  std::lock_guard<std::mutex> guard(mutex_);
  return preview_body_;
}

void ServiceState::publishHealth(const std::string& port, std::size_t frames,
                                 std::size_t dropped, std::size_t crc_errors,
                                 double fps, double model_ms, bool model_ready,
                                 double best_score, double presence,
                                 double frame_mean, double frame_stddev) {
  std::ostringstream out;
  out << "{\"status\":\"ok\""
      << ",\"port\":\"" << escape(port) << '"'
      << ",\"frames\":" << frames
      << ",\"dropped\":" << dropped
      << ",\"crc_errors\":" << crc_errors
      << ",\"fps\":" << number(fps)
      << ",\"model_ms\":" << number(model_ms)
      << ",\"model_ready\":" << (model_ready ? "true" : "false")
      << ",\"best_score\":" << number(best_score)
      << ",\"presence\":" << number(presence)
      // 화면을 안 보고도 '카메라 앞에 뭔가 있나' 를 가르는 두 수. 벽이나 천장은
      // 대비가 거의 0 이고, 어두우면 평균이 바닥에 붙는다.
      << ",\"frame_mean\":" << number(frame_mean)
      << ",\"frame_stddev\":" << number(frame_stddev)
      << '}';

  std::lock_guard<std::mutex> guard(mutex_);
  health_json_ = out.str();
}

std::string ServiceState::stateJson() const {
  std::lock_guard<std::mutex> guard(mutex_);
  return state_json_;
}

std::string ServiceState::healthJson() const {
  std::lock_guard<std::mutex> guard(mutex_);
  return health_json_;
}

bool runHttpServer(int port, ServiceState& state, const std::atomic<bool>& stop,
                   std::string& error) {
  const int server = ::socket(AF_INET, SOCK_STREAM, 0);
  if (server < 0) {
    error = std::string("소켓을 못 만듭니다: ") + std::strerror(errno);
    return false;
  }
  int reuse = 1;
  ::setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(static_cast<std::uint16_t>(port));
  // 루프백에만 연다. 이 API 는 같은 보드의 앱만 쓴다.
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (::bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
    error = std::string("포트 ") + std::to_string(port) +
            " 를 못 엽니다: " + std::strerror(errno);
    ::close(server);
    return false;
  }
  if (::listen(server, 8) < 0) {
    error = std::string("listen: ") + std::strerror(errno);
    ::close(server);
    return false;
  }

  while (!stop) {
    pollfd waiting{server, POLLIN, 0};
    const int ready = ::poll(&waiting, 1, 200);  // stop 을 200ms 안에 본다
    if (ready <= 0) continue;
    const int client = ::accept(server, nullptr, nullptr);
    if (client < 0) continue;
    handleClient(client, state);
    ::close(client);
  }
  ::close(server);
  return true;
}

}  // namespace deskmate
