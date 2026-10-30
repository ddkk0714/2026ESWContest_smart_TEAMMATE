#include "tof_service_poll.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>

namespace deskmate {

namespace {

constexpr const char* kLinePrefix = "TOFSVC\t";
constexpr std::size_t kMaxResponseBytes = 64 * 1024;   // /api/state 는 수 KB. 폭주 방어

int envInt(const char* name, int fallback, int min_value, int max_value)
{
    const char* raw = std::getenv(name);
    if (raw == nullptr || *raw == '\0') return fallback;
    const int value = std::atoi(raw);
    return (value >= min_value && value <= max_value) ? value : fallback;
}

// 데드라인까지 fd 가 readable/writable 이 되기를 기다린다. 0 이하면 시간 초과·오류.
int waitFor(int fd, short events, std::chrono::steady_clock::time_point deadline)
{
    const auto now = std::chrono::steady_clock::now();
    if (now >= deadline) return 0;
    const int ms = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
    pollfd descriptor = {fd, events, 0};
    return poll(&descriptor, 1, std::max(1, ms));
}

}  // namespace

TofServicePollConfig tofServicePollConfigFromEnvironment()
{
    TofServicePollConfig config;
    if (const char* host = std::getenv("DESKMATE_TOF_SERVICE_HOST")) {
        if (*host != '\0') config.host = host;
    }
    if (const char* path = std::getenv("DESKMATE_TOF_SERVICE_PATH")) {
        if (*path == '/') config.path = path;
    }
    config.port = envInt("DESKMATE_TOF_SERVICE_PORT", config.port, 0, 65535);
    config.period_ms = envInt("DESKMATE_TOF_SERVICE_PERIOD_MS", config.period_ms, 100, 60000);
    config.timeout_ms = envInt("DESKMATE_TOF_SERVICE_TIMEOUT_MS", config.timeout_ms, 50, 10000);
    return config;
}

bool parseHttpResponseBody(const std::string& response, std::string& body, int& status)
{
    status = 0;
    // 상태줄: HTTP/1.x <code> ...
    if (response.compare(0, 5, "HTTP/") != 0) return false;
    const std::size_t space = response.find(' ');
    if (space == std::string::npos || space + 4 > response.size()) return false;
    status = std::atoi(response.c_str() + space + 1);
    const std::size_t header_end = response.find("\r\n\r\n");
    std::size_t body_start;
    if (header_end != std::string::npos) {
        body_start = header_end + 4;
    } else {
        const std::size_t lf_end = response.find("\n\n");
        if (lf_end == std::string::npos) return false;
        body_start = lf_end + 2;
    }
    body = response.substr(body_start);
    // Content-Length 가 있으면 그만큼만(뒤에 덧붙은 바이트 방어)
    const std::size_t cl = response.find("Content-Length:");
    if (cl != std::string::npos && cl < body_start) {
        const long declared = std::strtol(response.c_str() + cl + 15, nullptr, 10);
        if (declared >= 0 && static_cast<std::size_t>(declared) < body.size()) body.resize(static_cast<std::size_t>(declared));
    }
    return status == 200;
}

std::string makeTofServiceLine(const std::string& body)
{
    std::string line = kLinePrefix;
    line.reserve(body.size() + 8);
    for (const char value : body) {
        line += (value == '\n' || value == '\r') ? ' ' : value;
    }
    // 앞뒤 공백 정리(서비스가 본문 끝에 개행을 붙인다)
    while (line.size() > std::strlen(kLinePrefix) && line.back() == ' ') line.pop_back();
    line += '\n';
    return line;
}

TofServicePoller::TofServicePoller(TofServicePollConfig config, LineSink sink)
    : config_(std::move(config)), sink_(std::move(sink)) {}

TofServicePoller::~TofServicePoller()
{
    stop();
}

void TofServicePoller::start()
{
    if (config_.port <= 0 || running_) return;
    running_ = true;
    worker_ = std::thread(&TofServicePoller::run, this);
    std::cerr << "DESKMATE ToF service poll -> http://" << config_.host << ':' << config_.port << config_.path
              << " every " << config_.period_ms << " ms\n";
}

void TofServicePoller::stop()
{
    if (!running_) return;
    running_ = false;
    if (worker_.joinable()) worker_.join();
}

bool TofServicePoller::fetch(std::string& response, std::string& why)
{
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(config_.timeout_ms);
    const int fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        why = std::strerror(errno);
        return false;
    }
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<std::uint16_t>(config_.port));
    if (inet_pton(AF_INET, config_.host.c_str(), &address.sin_addr) != 1) {
        close(fd);
        why = "bad host";
        return false;
    }
    int result = connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address));
    if (result != 0 && errno == EINPROGRESS) {
        if (waitFor(fd, POLLOUT, deadline) > 0) {
            int error = 0;
            socklen_t length = sizeof(error);
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length);
            result = error == 0 ? 0 : -1;
            errno = error;
        } else {
            result = -1;
            errno = ETIMEDOUT;
        }
    }
    if (result != 0) {
        why = std::strerror(errno);
        close(fd);
        return false;
    }
    int nodelay = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));

    const std::string request = "GET " + config_.path + " HTTP/1.0\r\nHost: " + config_.host +
                                "\r\nConnection: close\r\n\r\n";
    std::size_t offset = 0;
    while (offset < request.size()) {
        const ssize_t written = send(fd, request.data() + offset, request.size() - offset, MSG_NOSIGNAL | MSG_DONTWAIT);
        if (written > 0) {
            offset += static_cast<std::size_t>(written);
            continue;
        }
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (waitFor(fd, POLLOUT, deadline) > 0) continue;
            why = "send timeout";
            close(fd);
            return false;
        }
        if (written < 0 && errno == EINTR) continue;
        why = written < 0 ? std::strerror(errno) : "send failed";
        close(fd);
        return false;
    }

    response.clear();
    char buffer[4096];
    for (;;) {
        const ssize_t count = recv(fd, buffer, sizeof(buffer), MSG_DONTWAIT);
        if (count > 0) {
            response.append(buffer, static_cast<std::size_t>(count));
            if (response.size() > kMaxResponseBytes) {
                why = "response too large";
                close(fd);
                return false;
            }
            continue;
        }
        if (count == 0) break;                      // 서비스가 응답 뒤 닫았다 - 끝
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            if (waitFor(fd, POLLIN, deadline) > 0) continue;
            why = response.empty() ? "read timeout" : "read timeout (partial)";
            close(fd);
            return false;
        }
        if (errno == EINTR) continue;
        why = std::strerror(errno);
        close(fd);
        return false;
    }
    close(fd);
    if (response.empty()) {
        why = "empty response";
        return false;
    }
    return true;
}

bool TofServicePoller::pollOnce()
{
    ++polls_;
    std::string response;
    std::string why;
    bool good = fetch(response, why);
    std::string body;
    int status = 0;
    if (good && !parseHttpResponseBody(response, body, status)) {
        good = false;
        why = status == 0 ? "not an HTTP response" : "HTTP " + std::to_string(status);
    }
    if (good && body.empty()) {
        good = false;
        why = "empty body";
    }
    if (!good) {
        ++errors_;
        if (!down_logged_) {
            std::cerr << "DESKMATE ToF service poll: " << config_.host << ':' << config_.port << config_.path
                      << " unavailable (" << why << "), retrying every " << config_.period_ms
                      << " ms; hub keeps MLP/rule posture\n";
            down_logged_ = true;
            up_logged_ = false;
        }
        return false;
    }
    ++ok_;
    if (!up_logged_) {
        std::cerr << "DESKMATE ToF service poll: receiving " << config_.host << ':' << config_.port << config_.path
                  << " (" << body.size() << " B)\n";
        up_logged_ = true;
        down_logged_ = false;
    }
    if (sink_) sink_(makeTofServiceLine(body));
    return true;
}

void TofServicePoller::run()
{
    auto next = std::chrono::steady_clock::now();
    while (running_) {
        pollOnce();
        next += std::chrono::milliseconds(config_.period_ms);
        // 100 ms 조각으로 자며 stop 에 바로 반응한다
        while (running_ && std::chrono::steady_clock::now() < next) {
            std::this_thread::sleep_for(std::chrono::milliseconds(
                std::min<long long>(100, std::chrono::duration_cast<std::chrono::milliseconds>(
                                             next - std::chrono::steady_clock::now()).count() + 1)));
        }
        if (std::chrono::steady_clock::now() > next + std::chrono::milliseconds(config_.period_ms)) {
            next = std::chrono::steady_clock::now();   // 많이 밀렸으면 따라잡지 않고 다시 맞춘다
        }
    }
}

}  // namespace deskmate
