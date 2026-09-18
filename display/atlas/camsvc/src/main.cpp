// ESP32-CAM 스켈레톤 자세 판정 서비스.
//
//   /dev/ttyACM* (Vision Stream) -> preview 160x120 -> BlazePose 두 모델
//     -> 랜드마크 33개 -> posture_pose 판정 -> 127.0.0.1:8770 HTTP
//
// 앱(`display/atlas/camtest`)은 판정을 보여 주기만 한다. 판정이 여기 있는 이유는
// ATLAS 에 파이썬이 없고, Dart 에서 TFLite 를 돌릴 길도 없기 때문이다.
//
// 모드
//   (없음)            서비스로 돈다
//   --record <dir>    preview 프레임을 PGM 으로 저장한다. 정답지 만들 때 쓴다
//   --replay <dir>    저장한 PGM 을 다시 돌려 랜드마크를 찍는다. 채점용
//
// 환경변수: CAMSVC_PORT, CAMSVC_DEVICE, CAMSVC_MODELS
#include <dirent.h>
#include <signal.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "pose_model.h"
#include "posture_pose.h"
#include "service_api.h"
#include "vision_link.h"

namespace {

std::atomic<bool> g_stop{false};

void handleSignal(int) { g_stop = true; }

double steadySeconds() {
  using namespace std::chrono;
  static const auto start = steady_clock::now();
  return duration<double>(steady_clock::now() - start).count();
}

std::string environmentOr(const char* name, const std::string& fallback) {
  const char* value = std::getenv(name);
  return (value != nullptr && *value != '\0') ? std::string(value) : fallback;
}

// 추적을 끄면 매 프레임 검출기부터 다시 돈다. 느리지만 직전 프레임의 결과가
// 다음 ROI 에 되먹임되지 않으므로, 좌표가 흘러갈 때 원인이 추적에 있는지 가른다.
bool noTracking() { return !environmentOr("CAMSVC_NO_TRACKING", "").empty(); }

// 실행 파일이 있는 자리. 모델은 서비스와 같이 설치되므로 여기서 찾는다.
std::string executableDirectory() {
  char buffer[4096];
  const ssize_t length = ::readlink("/proc/self/exe", buffer, sizeof(buffer) - 1);
  if (length <= 0) return ".";
  buffer[length] = '\0';
  std::string path(buffer);
  const auto slash = path.find_last_of('/');
  return slash == std::string::npos ? std::string(".") : path.substr(0, slash);
}

// 판정이 쓰는 7개만 넘긴다. 나머지 26개는 안 본다.
deskmate::Landmarks toJudgementPoints(
    const std::vector<deskmate::Landmark>& landmarks) {
  static const int wanted[] = {deskmate::kNose,     deskmate::kLeftShoulder,
                               deskmate::kRightShoulder, deskmate::kLeftElbow,
                               deskmate::kRightElbow,    deskmate::kLeftWrist,
                               deskmate::kRightWrist};
  deskmate::Landmarks points;
  for (const int index : wanted) {
    if (index < 0 || index >= static_cast<int>(landmarks.size())) continue;
    deskmate::Point point;
    point.x = landmarks[index].x;
    point.y = landmarks[index].y;
    points[index] = point;
  }
  return points;
}

bool writePgm(const std::string& path, const std::uint8_t* grey, int width,
              int height) {
  std::ofstream file(path, std::ios::binary);
  if (!file) return false;
  file << "P5\n" << width << ' ' << height << "\n255\n";
  file.write(reinterpret_cast<const char*>(grey),
             static_cast<std::streamsize>(width) * height);
  return file.good();
}

bool readPgm(const std::string& path, std::vector<std::uint8_t>& grey,
             int& width, int& height) {
  std::ifstream file(path, std::ios::binary);
  if (!file) return false;
  std::string magic;
  int maximum = 0;
  file >> magic >> width >> height >> maximum;
  if (magic != "P5" || width <= 0 || height <= 0 || maximum != 255) return false;
  file.get();  // 헤더 뒤 공백 하나
  grey.assign(static_cast<std::size_t>(width) * height, 0);
  file.read(reinterpret_cast<char*>(grey.data()),
            static_cast<std::streamsize>(grey.size()));
  return file.good() || file.gcount() == static_cast<std::streamsize>(grey.size());
}

std::vector<std::string> sortedPgmFiles(const std::string& directory) {
  std::vector<std::string> files;
  DIR* dir = opendir(directory.c_str());
  if (dir == nullptr) return files;
  while (const dirent* entry = readdir(dir)) {
    const std::string name = entry->d_name;
    if (name.size() > 4 && name.compare(name.size() - 4, 4, ".pgm") == 0) {
      files.push_back(directory + "/" + name);
    }
  }
  closedir(dir);
  std::sort(files.begin(), files.end());
  return files;
}

struct Paths {
  std::string detector;
  std::string landmark;
};

Paths modelPaths() {
  const std::string root =
      environmentOr("CAMSVC_MODELS", executableDirectory() + "/models");
  return {root + "/pose_detector.tflite", root + "/pose_landmarks_detector.tflite"};
}

// 링크를 열고 preview 를 켠다. 못 열면 false.
bool openLink(deskmate::VisionLink& link) {
  std::string device = environmentOr("CAMSVC_DEVICE", "");
  if (device.empty()) device = deskmate::VisionLink::findVisionPort();
  if (device.empty()) {
    std::cerr << "camsvc: Vision Stream 포트를 못 찾았습니다\n";
    return false;
  }
  if (!link.open(device)) {
    std::cerr << "camsvc: " << device << " " << link.error() << "\n";
    return false;
  }
  // `p1` 이 매 프레임 preview 를 보내라는 명령이다. 이게 없으면 coverage 만 온다.
  link.writeLine("p1");
  std::cerr << "camsvc: " << device << " 에서 preview 를 켰습니다\n";
  return true;
}

int runRecord(const std::string& directory, int wanted) {
  deskmate::VisionLink link;
  if (!openLink(link)) return 1;

  deskmate::FrameDecoder decoder;
  std::vector<std::uint8_t> buffer(16384);
  std::vector<deskmate::VisionFrame> frames;
  int saved = 0;
  const double deadline = steadySeconds() + 120.0;

  while (!g_stop && saved < wanted && steadySeconds() < deadline) {
    const std::ptrdiff_t n = link.read(buffer.data(), buffer.size());
    if (n < 0) break;
    if (n == 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
      continue;
    }
    frames.clear();
    decoder.feed(buffer.data(), static_cast<std::size_t>(n), frames);
    for (const deskmate::VisionFrame& frame : frames) {
      if (frame.type != deskmate::kTypePreview) continue;
      char name[512];
      std::snprintf(name, sizeof(name), "%s/frame_%04d.pgm", directory.c_str(),
                    saved);
      if (!writePgm(name, frame.payload.data(), frame.width, frame.height)) {
        std::cerr << "camsvc: 저장 실패 " << name << "\n";
        return 1;
      }
      ++saved;
      if (saved >= wanted) break;
    }
  }
  std::cerr << "camsvc: preview " << saved << "장 저장했습니다\n";
  return saved > 0 ? 0 : 1;
}

// 저장한 프레임을 다시 돌려 랜드마크를 찍는다. MediaPipe 정답지와 줄 단위로 비교한다.
int runReplay(const std::string& directory) {
  const Paths paths = modelPaths();
  deskmate::PoseModel model;
  if (!model.load(paths.detector, paths.landmark)) {
    std::cerr << "camsvc: " << model.error() << "\n";
    return 1;
  }
  const bool no_tracking = noTracking();
  const std::vector<std::string> files = sortedPgmFiles(directory);
  if (files.empty()) {
    std::cerr << "camsvc: " << directory << " 에 PGM 이 없습니다\n";
    return 1;
  }

  for (const std::string& file : files) {
    std::vector<std::uint8_t> grey;
    int width = 0;
    int height = 0;
    if (!readPgm(file, grey, width, height)) {
      std::cerr << "camsvc: 못 읽음 " << file << "\n";
      return 1;
    }
    const auto slash = file.find_last_of('/');
    const std::string name =
        slash == std::string::npos ? file : file.substr(slash + 1);

    if (no_tracking) model.resetTracking();
    const deskmate::PoseResult result = model.run(grey.data(), width, height);
    // 이름 찾음 검출점수 존재점수 [x:y ...]. 못 찾아도 점수는 남긴다 - 어디서
    // 떨어졌는지는 그 두 수로만 갈린다.
    std::printf("%s %d %.6f %.6f roi=%.4f,%.4f,%.4f,%.4f,%.4f", name.c_str(),
                result.found ? 1 : 0, result.best_score, result.presence,
                result.roi.x_center, result.roi.y_center, result.roi.width,
                result.roi.height, result.roi.rotation);
    for (const deskmate::Landmark& lm : result.landmarks) {
      std::printf(" %.6f:%.6f", lm.x, lm.y);
    }
    std::printf("\n");
  }
  return 0;
}

int runService() {
  const Paths paths = modelPaths();
  deskmate::ServiceState state;
  std::string http_error;
  std::thread http([&] {
    const int port = std::atoi(
        environmentOr("CAMSVC_PORT", std::to_string(deskmate::kDefaultPort))
            .c_str());
    // 포트를 못 열어도 서비스를 죽이지 않는다. 화면은 못 붙어도 판정은 돌고,
    // 포트를 잡고 있던 쪽이 사라지면 저절로 붙는다.
    bool complained = false;
    while (!g_stop) {
      if (deskmate::runHttpServer(port, state, g_stop, http_error)) return;
      if (!complained) {
        std::cerr << "camsvc: " << http_error << " (5초마다 다시 시도합니다)\n";
        complained = true;
      }
      for (int i = 0; i < 25 && !g_stop; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
      }
    }
  });

  deskmate::PoseModel model;
  const bool model_ready = model.load(paths.detector, paths.landmark);
  if (!model_ready) {
    // 모델이 없어도 링크 상태는 보여 준다. 화면이 통째로 죽는 것보다 낫다.
    std::cerr << "camsvc: " << model.error() << "\n";
  }

  const bool no_tracking = noTracking();
  if (no_tracking) std::cerr << "camsvc: 추적을 끄고 매 프레임 검출합니다\n";

  deskmate::VisionLink link;
  deskmate::FrameDecoder decoder;
  deskmate::PostureTracker tracker;

  std::vector<std::uint8_t> buffer(16384);
  std::vector<deskmate::VisionFrame> frames;
  std::size_t preview_frames = 0;
  double model_ms = 0.0;
  double fps = 0.0;
  double last_frame_at = 0.0;
  double next_health = 0.0;
  double next_retry = 0.0;
  double best_score = 0.0;   // 검출기가 마지막으로 본 최고 점수
  double presence = 0.0;     // 랜드마크 모델의 존재 점수
  double frame_mean = 0.0;   // 마지막 preview 의 밝기
  double frame_stddev = 0.0; // 그 대비

  while (!g_stop) {
    if (!link.isOpen()) {
      if (steadySeconds() < next_retry) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        continue;
      }
      next_retry = steadySeconds() + 5.0;  // 케이블을 다시 꽂을 시간은 준다
      if (!openLink(link)) {
        state.publishHealth("(없음)", preview_frames, decoder.dropped(),
                            decoder.badCrc(), 0.0, model_ms, model_ready,
                            best_score, presence, frame_mean, frame_stddev);
        continue;
      }
    }

    const std::ptrdiff_t n = link.read(buffer.data(), buffer.size());
    if (n < 0) {
      link.close();
      continue;
    }
    if (n == 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    } else {
      frames.clear();
      decoder.feed(buffer.data(), static_cast<std::size_t>(n), frames);
      for (const deskmate::VisionFrame& frame : frames) {
        if (frame.type != deskmate::kTypePreview) continue;
        ++preview_frames;
        const double now = steadySeconds();
        if (last_frame_at > 0.0) {
          const double gap = now - last_frame_at;
          if (gap > 0.0) fps = fps == 0.0 ? 1.0 / gap : fps * 0.8 + 0.2 / gap;
        }
        last_frame_at = now;

        // 밝기·대비. 영상은 어디에도 남기지 않고 이 두 수만 남긴다.
        double sum = 0.0;
        double sum_squares = 0.0;
        for (const std::uint8_t pixel : frame.payload) {
          sum += pixel;
          sum_squares += static_cast<double>(pixel) * pixel;
        }
        const double count = static_cast<double>(frame.payload.size());
        if (count > 0.0) {
          frame_mean = sum / count;
          const double variance = sum_squares / count - frame_mean * frame_mean;
          frame_stddev = variance > 0.0 ? std::sqrt(variance) : 0.0;
        }
        if (!model_ready) continue;

        const double started = steadySeconds();
        if (no_tracking) model.resetTracking();
        const deskmate::PoseResult pose =
            model.run(frame.payload.data(), frame.width, frame.height);
        model_ms = (steadySeconds() - started) * 1000.0;
        if (pose.ran_detector) best_score = pose.best_score;
        presence = pose.presence;

        // 화면이 "내가 지금 어떻게 보이나" 를 확인할 수 있게 그림과 뼈대를
        // 같이 남긴다. 127.0.0.1 밖으로는 안 나간다.
        std::vector<std::pair<float, float>> overlay;
        overlay.reserve(pose.landmarks.size());
        for (const deskmate::Landmark& lm : pose.landmarks) {
          overlay.emplace_back(lm.x, lm.y);
        }
        state.publishPreview(frame.payload.data(), frame.width, frame.height,
                             overlay);

        const deskmate::Landmarks points =
            pose.found ? toJudgementPoints(pose.landmarks) : deskmate::Landmarks{};
        if (state.takeCalibrationRequest()) {
          if (!tracker.captureReference(points)) {
            state.publish("BASELINE", "BASELINE", pose.found, false, 0.0, 0.0,
                          1.0, "어깨가 둘 다 보이게 앉아 주세요");
            continue;
          }
        }
        if (!tracker.hasReference()) {
          state.publish("BASELINE", "BASELINE", pose.found, false, 0.0, 0.0, 1.0,
                        pose.found ? "바르게 앉고 기준 잡기를 눌러 주세요"
                                   : "사람을 못 찾았습니다");
          continue;
        }

        const deskmate::State judged = tracker.update(points, now);
        state.publish(judged.label, judged.candidate, pose.found, true,
                      judged.held_for, judged.metrics.head_drop,
                      judged.metrics.scale_ratio, judged.note);
      }
    }

    if (steadySeconds() >= next_health) {
      next_health = steadySeconds() + 1.0;
      state.publishHealth(link.path(), preview_frames, decoder.dropped(),
                          decoder.badCrc(), fps, model_ms, model_ready,
                          best_score, presence, frame_mean, frame_stddev);
    }
  }

  g_stop = true;
  http.join();
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  struct sigaction action = {};
  action.sa_handler = handleSignal;
  sigemptyset(&action.sa_mask);
  sigaction(SIGINT, &action, nullptr);
  sigaction(SIGTERM, &action, nullptr);
  signal(SIGPIPE, SIG_IGN);

  std::string mode;
  std::string directory;
  int count = 60;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    if ((argument == "--record" || argument == "--replay") && i + 1 < argc) {
      mode = argument;
      directory = argv[++i];
    } else if (argument == "--count" && i + 1 < argc) {
      count = std::atoi(argv[++i]);
    } else {
      std::cerr << "camsvc: 모르는 인자 " << argument << "\n";
      return 2;
    }
  }

  if (mode == "--record") return runRecord(directory, count);
  if (mode == "--replay") return runReplay(directory);
  return runService();
}
