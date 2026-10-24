// deskmate_tofposture — Pi 4 ToF 자세 서비스 (ATLAS native-service, 의존성 없음).
//
//   VL53L8CX ─I2C─ ESP32(env:tof_logger) ─USB 921600─► /dev/ttyUSB0 ┐
//   VL53L8CX ─I2C─ ESP32(env:tof_logger) ─USB 921600─► /dev/ttyUSB1 ┼─► 이 서비스 ─HTTP :8771─► hub / 앱 / curl
//                                                                     │   센서마다 PostureNet(GRU) → softmax 평균
//                                                                     └─  stdout: 자세가 바뀔 때 한 줄 JSON
//
// 환경변수 (전부 선택):
//   TOFPOSTURE_PORTS   콤마로 나눈 장치/파일 목록. 비우면 /dev/ttyUSB* /dev/ttyACM* 를 찾아 쓴다(최대 4개).
//                      보통 파일을 주면 리플레이(줄의 ms 로 속도 조절).
//   TOFPOSTURE_BAUD    auto(기본: 921600→115200→460800→230400 중 센서 줄이 읽히는 값) 또는 숫자.
//                      tof_logger 펌웨어는 921600, bring-up 펌웨어(tof-bringup-full-4MB.bin)는 미확인
//   TOFPOSTURE_MODEL   posture_net.bin 경로. 기본 <실행파일 폴더>/models/posture_net.bin
//   TOFPOSTURE_HTTP    HTTP 포트. 기본 8771 (8765 hub, 8770 camsvc 와 겹치지 않게). 0 이면 끔
//   TOFPOSTURE_BIND    0.0.0.0 (개발 PC 에서 curl). 보드 안에서만 쓰면 127.0.0.1
//   TOFPOSTURE_RATE_HZ 10. 모델 학습 프레임률. 바꾸면 안 된다
//   TOFPOSTURE_STALE_MS 1000. 이보다 오래 프레임이 없는 센서는 offline
//   TOFPOSTURE_SMOOTH_S 1.0. 화면용 확률 EMA 시간상수(초)
//   TOFPOSTURE_FUSION   logprob(기본, log-softmax 합) | mean(softmax 평균). device_posture.h 의 측정값 참고
//   TOFPOSTURE_FLIP_ROWS / TOFPOSTURE_FLIP_COLS  1 이면 받은 격자를 위아래/좌우로 뒤집는다(장착 방향 보정), 0 이면 안 뒤집음,
//                      비우면 자동: UART 0x05 의 flags b2/b3(조립 때 펌웨어에 박은 값)대로, 다른 형식은 안 뒤집음.
//                      맞는 값은 tof_live.py --show 로 손을 센서 위쪽·왼쪽에 대 보고 정한다
//   포트 목록에 "tcp:9001" 을 주면 그 포트에서 PC 가 보내는 센서 줄을 받는다(tof_live.py --forward). USB 직결이 안 될 때
//   운영(UART2): hub/atlas 가 TYPE 0x05 줄을 tcp:9100 으로 넘긴다 → TOFPOSTURE_PORTS=tcp:9100 하나에 두 센서(번호로 나눔)
//   TOFPOSTURE_RESET_PERIOD_S 900. 마지막 리셋(또는 시작)으로부터 이 시간이 지나면 추론 전에 GRU h·EMA 리셋. 0 = 끔
//   TOFPOSTURE_AWAY_RESET_S   60. ToF 규칙 재실(coverage)이 이 시간 동안 false 면 리셋, 비어 있는 동안 반복. 0 = 끔.
//                      재실 한 프레임이면 타이머를 지운다. 모델 출력 away 는 근거로 쓰지 않는다(device_posture.h "리셋")
//   TOFPOSTURE_PRESENT_MIN_MM / TOFPOSTURE_PRESENT_MAX_MM  150 / 1300. 재실 coverage 에 세는 zone 의 거리 범위(mm)
//   TOFPOSTURE_PRESENT_MIN_COVERAGE  0.06. 두 센서 중 큰 coverage(status 5·6·9·10 ∧ 거리 범위 인 zone / 64)가 이 이상이면 재실
//   TOFPOSTURE_STDOUT   change(기본: 자세가 바뀔 때만) | tick(매 스텝) | off
//   TOFPOSTURE_EXIT_ON_EOF 1 이면 리플레이 파일이 끝났을 때 종료 (테스트용)
// 같은 키를 <실행파일 폴더>/tofposture.env (KEY=VALUE 한 줄씩) 에 적어도 된다 — D-Bus 로 뜬 서비스는
// 셸 환경이 없으므로 보드에서는 이 파일을 쓴다(hub/atlas 의 hub.env 와 같은 방식). 환경변수가 우선.
#include <dirent.h>
#include <signal.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "device_posture.h"
#include "http_server.h"
#include "line_source.h"
#include "tof_line.h"

#ifdef TOFPOSTURE_HAVE_SDBUS
#include <sdbus-c++/sdbus-c++.h>
#endif

namespace {

constexpr const char* kServiceName = "com.deskmate.tofposture1";
std::atomic<bool> g_stop{false};

void handleSignal(int) { g_stop = true; }

std::string envOr(const char* name, const std::string& fallback) {
    const char* v = std::getenv(name);
    return (v != nullptr && *v) ? std::string(v) : fallback;
}

int envInt(const char* name, int fallback) {
    const char* v = std::getenv(name);
    return (v != nullptr && *v) ? std::atoi(v) : fallback;
}

double envDouble(const char* name, double fallback) {
    const char* v = std::getenv(name);
    return (v != nullptr && *v) ? std::atof(v) : fallback;
}

std::string executableDir() {
    char buf[4096];
    const ssize_t n = ::readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n <= 0) return ".";
    buf[n] = '\0';
    std::string p(buf);
    const size_t slash = p.find_last_of('/');
    return slash == std::string::npos ? "." : p.substr(0, slash);
}

std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    while (start <= s.size()) {
        size_t end = s.find(sep, start);
        if (end == std::string::npos) end = s.size();
        std::string item = s.substr(start, end - start);
        const size_t a = item.find_first_not_of(" \t"), b = item.find_last_not_of(" \t");
        if (a != std::string::npos) out.push_back(item.substr(a, b - a + 1));
        start = end + 1;
    }
    return out;
}

// /dev/ttyUSB* /dev/ttyACM* 를 이름순으로. ESP32 DevKit(CP2102/CH340) 은 ttyUSB, 일부 보드는 ttyACM.
std::vector<std::string> scanPorts(size_t max_ports) {
    std::vector<std::string> found;
    DIR* d = ::opendir("/dev");
    if (d == nullptr) return found;
    while (dirent* e = ::readdir(d)) {
        const std::string name = e->d_name;
        if (name.rfind("ttyUSB", 0) == 0 || name.rfind("ttyACM", 0) == 0) found.push_back("/dev/" + name);
    }
    ::closedir(d);
    std::sort(found.begin(), found.end());
    if (found.size() > max_ports) found.resize(max_ports);
    return found;
}

int64_t nowMs() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

// KEY=VALUE 파일. 이미 있는 환경변수는 덮지 않는다. '#' 줄은 주석.
void loadEnvFile(const std::string& path) {
    std::ifstream f(path);
    if (!f) return;
    std::string line;
    while (std::getline(f, line)) {
        const size_t a = line.find_first_not_of(" \t");
        if (a == std::string::npos || line[a] == '#') continue;
        const size_t eq = line.find('=', a);
        if (eq == std::string::npos) continue;
        std::string key = line.substr(a, eq - a), value = line.substr(eq + 1);
        const size_t ke = key.find_last_not_of(" \t");
        key = ke == std::string::npos ? "" : key.substr(0, ke + 1);
        const size_t vb = value.find_first_not_of(" \t"), ve = value.find_last_not_of(" \t\r");
        value = vb == std::string::npos ? "" : value.substr(vb, ve - vb + 1);
        if (value.size() >= 2 && (value.front() == '"' || value.front() == '\'') && value.back() == value.front())
            value = value.substr(1, value.size() - 2);
        if (!key.empty()) ::setenv(key.c_str(), value.c_str(), 0);
    }
}

}  // namespace

int main() {
    struct sigaction action = {};
    action.sa_handler = handleSignal;
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, nullptr);
    sigaction(SIGTERM, &action, nullptr);
    signal(SIGPIPE, SIG_IGN);

#ifdef TOFPOSTURE_HAVE_SDBUS
    // ATLAS 는 D-Bus activation 으로 서비스를 띄운다(busctl --system status <이름>). 이름을 가져야 활성화가 완료된다.
    std::unique_ptr<sdbus::IConnection> dbus;
    try {
        dbus = sdbus::createSystemBusConnection(sdbus::ServiceName(kServiceName));
        dbus->enterEventLoopAsync();
    } catch (const std::exception& e) {
        std::cerr << "tofposture: D-Bus name not acquired (" << e.what() << "); continuing without it\n";
    }
#endif

    const std::string exe_dir = executableDir();
    loadEnvFile(exe_dir + "/tofposture.env");
    tof::DeviceConfig cfg;
    cfg.rate_hz = envInt("TOFPOSTURE_RATE_HZ", tof::kModelRateHz);
    cfg.stale_ms = envInt("TOFPOSTURE_STALE_MS", 1000);
    cfg.smooth_seconds = static_cast<float>(envDouble("TOFPOSTURE_SMOOTH_S", 1.0));
    cfg.fusion = envOr("TOFPOSTURE_FUSION", "logprob") == "mean" ? tof::FusionRule::MeanProb : tof::FusionRule::LogProb;
    cfg.flip_rows = envInt("TOFPOSTURE_FLIP_ROWS", -1);   // 비우면 자동(UART 0x05 flags), 0/1 이면 강제
    cfg.flip_cols = envInt("TOFPOSTURE_FLIP_COLS", -1);
    if (cfg.flip_rows > 1) cfg.flip_rows = 1;
    if (cfg.flip_cols > 1) cfg.flip_cols = 1;
    cfg.reset_period_s = std::max(0, envInt("TOFPOSTURE_RESET_PERIOD_S", 900));
    cfg.away_reset_s = std::max(0, envInt("TOFPOSTURE_AWAY_RESET_S", 60));
    cfg.present_min_mm = envInt("TOFPOSTURE_PRESENT_MIN_MM", 150);
    cfg.present_max_mm = envInt("TOFPOSTURE_PRESENT_MAX_MM", 1300);
    cfg.present_min_coverage = static_cast<float>(envDouble("TOFPOSTURE_PRESENT_MIN_COVERAGE", 0.06));
    const std::string baud_env = envOr("TOFPOSTURE_BAUD", "auto");
    const int baud = baud_env == "auto" ? 0 : std::atoi(baud_env.c_str());
    const int http_port = envInt("TOFPOSTURE_HTTP", 8771);
    const std::string bind_addr = envOr("TOFPOSTURE_BIND", "0.0.0.0");
    const std::string model_path = envOr("TOFPOSTURE_MODEL", exe_dir + "/models/posture_net.bin");
    const std::string stdout_mode = envOr("TOFPOSTURE_STDOUT", "change");
    const bool exit_on_eof = envInt("TOFPOSTURE_EXIT_ON_EOF", 0) != 0;
    const std::string ports_env = envOr("TOFPOSTURE_PORTS", "");

    tof::DevicePosture device(cfg);
    std::string err;
    if (!device.loadModel(model_path, &err)) {
        std::cerr << "tofposture: model load failed: " << err << " (" << model_path << ")\n"
                  << "  tools/push-model.sh 로 posture_net.bin 을 넣는다\n";
        // 모델 없이도 HTTP 는 띄워 둔다 — /health 가 원인을 말해 준다.
    } else {
        std::cerr << "tofposture: model " << model_path << " (" << device.classes() << " classes, "
                  << cfg.rate_hz << " Hz, fusion " << tof::fusionName(cfg.fusion) << ", flip_rows " << cfg.flip_rows
                  << " flip_cols " << cfg.flip_cols << ", reset period " << cfg.reset_period_s << " s, away "
                  << cfg.away_reset_s << " s, present " << cfg.present_min_mm << "-" << cfg.present_max_mm << " mm cov>="
                  << cfg.present_min_coverage << ")\n";
    }

    std::vector<std::unique_ptr<tof::LineSource>> sources;
    // 한 입력에 센서 번호가 실려 오는 경우(UART 0x05, hub/atlas 가 tcp 로 넘김): 처음 본 센서 번호는 그 입력의 자리를
    // 쓰고, 다음 번호부터는 자리를 새로 만든다. 입력 스레드에서만 부르므로 입력별 표에는 잠금이 필요 없다.
    struct Demux { std::map<int, int> slot; };
    std::vector<std::unique_ptr<Demux>> demux;
    auto attach = [&](const std::string& path) {
        const int idx = device.addSensor(path);
        demux.emplace_back(std::make_unique<Demux>());
        Demux* dm = demux.back().get();
        sources.emplace_back(std::make_unique<tof::LineSource>(path, baud, [&device, idx, dm, path](const std::string& line) {
            tof::TofLine parsed;
            if (!tof::parseTofLine(line, parsed)) return;
            int slot = idx;
            if (parsed.sensor_id >= 0) {
                auto it = dm->slot.find(parsed.sensor_id);
                if (it == dm->slot.end()) {
                    const std::string name = path + "#s" + std::to_string(parsed.sensor_id);
                    if (dm->slot.empty()) device.renameSensor(idx, name);
                    else slot = device.addSensor(name);
                    it = dm->slot.emplace(parsed.sensor_id, dm->slot.empty() ? idx : slot).first;
                }
                slot = it->second;
            }
            device.feed(slot, parsed, nowMs());
        }));
        sources.back()->start();
        std::cerr << "tofposture: sensor " << idx << " <- " << path << "\n";
    };
    // 자동 모드(TOFPOSTURE_PORTS 비움): 지금 있는 포트를 붙이고, 이후 5초마다 새로 꽂힌 포트도 붙인다(최대 4개).
    // 한 번 붙인 포트는 뽑혀도 그 자리를 유지한다 — 다시 꽂으면 LineSource 가 같은 경로를 다시 연다.
    auto attachNew = [&](const std::vector<std::string>& found) {
        for (const std::string& p : found) {
            if (sources.size() >= 4) break;
            bool known = false;
            for (const auto& s : sources) known = known || s->path() == p;
            if (!known) attach(p);
        }
    };
    if (ports_env.empty()) attachNew(scanPorts(4));
    else for (const std::string& p : split(ports_env, ',')) attach(p);
    if (sources.empty()) std::cerr << "tofposture: no serial port yet; scanning /dev/ttyUSB* /dev/ttyACM* every 5 s\n";

    tof::HttpServer http(bind_addr, http_port,
                         tof::HttpHandlers{[&device] { return device.healthJson(nowMs()); },
                                           [&device] { return device.stateJson(nowMs()); },
                                           [&device] { device.reset(nowMs()); }});
    if (http_port > 0) {
        if (!http.start(&err)) {
            std::cerr << "tofposture: HTTP listen failed on " << bind_addr << ":" << http_port << " (" << err << ")\n";
            return 1;
        }
        std::cerr << "tofposture: HTTP on " << bind_addr << ":" << http_port << "  (GET /api/state, /health, POST /api/reset)\n";
    }

    int last_label = -2;
    int64_t last_summary = nowMs(), last_scan = nowMs();
    while (!g_stop) {
        const int64_t now = nowMs();
        if (device.tick(now)) {
            const tof::DeviceResult r = device.result();
            const bool changed = r.smooth_argmax != last_label;
            if (stdout_mode == "tick" || (stdout_mode == "change" && changed)) {
                std::cout << device.stateJson(now) << std::endl;
            }
            if (changed) {
                std::cerr << "tofposture: " << tof::DevicePosture::labelName(r.smooth_argmax) << " ("
                          << tof::DevicePosture::labelKo(r.smooth_argmax) << ") conf "
                          << (r.smooth_argmax >= 0 ? r.smooth[r.smooth_argmax] : 0.f)
                          << " online " << r.online_sensors << "\n";
                last_label = r.smooth_argmax;
            }
        }
        if (now - last_summary >= 10000) {
            last_summary = now;
            std::cerr << "tofposture: health " << device.healthJson(now) << "\n";
            for (const auto& s : sources)
                if (s->isTty() || s->isTcp())
                    std::cerr << "tofposture:   " << s->path() << " baud " << s->baud() << " open=" << s->open()
                              << " bytes=" << s->bytes() << " lines=" << s->lines()
                              << (s->lastError().empty() ? "" : " (" + s->lastError() + ")") << "\n";
        }
        if (ports_env.empty() && sources.size() < 4 && now - last_scan >= 5000) {
            last_scan = now;
            attachNew(scanPorts(8));
        }
        if (exit_on_eof && !sources.empty()) {
            bool all_done = true;
            for (const auto& s : sources) all_done = all_done && s->finished();
            if (all_done) break;
        }
        usleep(5000);
    }
    std::cerr << "tofposture: stopping\n";
    for (auto& s : sources) s->stop();
    http.stop();
#ifdef TOFPOSTURE_HAVE_SDBUS
    if (dbus) dbus->leaveEventLoop();
#endif
    return 0;
}
