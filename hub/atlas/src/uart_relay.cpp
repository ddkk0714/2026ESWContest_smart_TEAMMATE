#include "uart_relay.h"

#include <arpa/inet.h>
#include <fcntl.h>
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
#include <sstream>

namespace deskmate {

namespace {

constexpr const char* kLinePrefix = "UART\t{\"type\":";

}  // namespace

UartRelayConfig uartRelayConfigFromEnvironment()
{
    UartRelayConfig config;
    if (const char* host = std::getenv("DESKMATE_TOF_RELAY_HOST")) {
        if (*host != '\0') config.host = host;
    }
    if (const char* port = std::getenv("DESKMATE_TOF_RELAY_PORT")) {
        const int value = std::atoi(port);
        config.port = (value >= 0 && value <= 65535) ? value : 0;
    }
    if (const char* types = std::getenv("DESKMATE_TOF_RELAY_TYPES")) {
        config.types.clear();
        std::stringstream parser(types);
        std::string item;
        while (std::getline(parser, item, ',')) {
            if (item.empty()) continue;
            config.types.push_back(static_cast<unsigned>(std::strtoul(item.c_str(), nullptr, 0)));
        }
    }
    return config;
}

int uartLineType(const std::string& line)
{
    const std::size_t prefix_length = std::strlen(kLinePrefix);
    if (line.compare(0, prefix_length, kLinePrefix) != 0) return -1;
    std::size_t pos = prefix_length;
    int value = 0;
    bool any = false;
    while (pos < line.size() && line[pos] >= '0' && line[pos] <= '9') {
        value = value * 10 + (line[pos] - '0');
        ++pos;
        any = true;
        if (value > 255) return -1;
    }
    return any ? value : -1;
}

UartRelay::UartRelay(UartRelayConfig config) : config_(std::move(config)) {}

UartRelay::~UartRelay()
{
    stop();
}

void UartRelay::start()
{
    if (config_.port <= 0 || running_) return;
    running_ = true;
    connector_ = std::thread(&UartRelay::connectLoop, this);
    std::cerr << "DESKMATE UART relay -> " << config_.host << ':' << config_.port << " (types";
    if (config_.types.empty()) std::cerr << " all";
    for (const unsigned type : config_.types) std::cerr << ' ' << type;
    std::cerr << ")\n";
}

void UartRelay::stop()
{
    if (!running_) return;
    running_ = false;
    if (connector_.joinable()) connector_.join();
    disconnect(nullptr);
}

// 붙었으면 fd, 아니면 -1. 블로킹 connect 는 쓰지 않는다(hub 종료가 밀리면 안 됨).
int UartRelay::tryConnect()
{
    const int fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<std::uint16_t>(config_.port));
    if (inet_pton(AF_INET, config_.host.c_str(), &address.sin_addr) != 1) {
        close(fd);
        return -1;
    }
    int result = connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address));
    if (result != 0 && errno == EINPROGRESS) {
        pollfd descriptor = {fd, POLLOUT, 0};
        if (poll(&descriptor, 1, 1000) > 0) {
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
        const int saved = errno;
        close(fd);
        if (!refused_logged_) {
            std::cerr << "DESKMATE UART relay: " << config_.host << ':' << config_.port
                      << " not reachable (" << std::strerror(saved) << "), retrying every "
                      << config_.reconnect_ms << " ms\n";
            refused_logged_ = true;
        }
        return -1;
    }
    int nodelay = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
    return fd;
}

void UartRelay::connectLoop()
{
    while (running_) {
        if (socket_fd_.load() < 0) {
            const int fd = tryConnect();
            if (fd >= 0) {
                std::lock_guard<std::mutex> lock(write_mutex_);
                socket_fd_ = fd;
                ++connects_;
                refused_logged_ = false;
                std::cerr << "DESKMATE UART relay connected to " << config_.host << ':'
                          << config_.port << '\n';
            }
        } else {
            // 상대가 닫았는지 본다 (읽을 게 생기면 EOF 이거나 뜻밖의 데이터 - 둘 다 끊는다).
            pollfd descriptor = {socket_fd_.load(), POLLIN, 0};
            if (poll(&descriptor, 1, 0) > 0) disconnect("peer closed");
        }
        const auto step = std::chrono::milliseconds(100);
        const int steps = std::max(1, config_.reconnect_ms / 100);
        for (int i = 0; i < steps && running_; ++i) {
            if (socket_fd_.load() < 0 && i > 0 && i % 5 == 0) break;  // 끊겼을 땐 0.5 s 안에 재시도
            std::this_thread::sleep_for(step);
        }
    }
}

void UartRelay::disconnect(const char* why)
{
    std::lock_guard<std::mutex> lock(write_mutex_);
    const int fd = socket_fd_.exchange(-1);
    if (fd < 0) return;
    close(fd);
    if (why != nullptr) std::cerr << "DESKMATE UART relay disconnected (" << why << ")\n";
}

bool UartRelay::wants(const std::string& line) const
{
    const int type = uartLineType(line);
    if (type < 0) return false;
    if (config_.types.empty()) return true;
    for (const unsigned wanted : config_.types) {
        if (static_cast<int>(wanted) == type) return true;
    }
    return false;
}

void UartRelay::publish(const std::string& line)
{
    if (!running_ || !wants(line)) return;
    std::lock_guard<std::mutex> lock(write_mutex_);
    const int fd = socket_fd_.load();
    if (fd < 0) {
        ++dropped_;
        return;
    }
    // 한 줄은 0x05 기준 약 450 B 라 소켓 버퍼(수십 KB)에 통째로 들어간다. 부분 쓰기나
    // EAGAIN 은 받는 쪽이 밀린 것이니 연결을 끊고 다시 붙는다 - hub 의 UART 스레드를 막지 않는다.
    const ssize_t written = send(fd, line.data(), line.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
    if (written == static_cast<ssize_t>(line.size())) {
        ++forwarded_;
        return;
    }
    ++dropped_;
    const std::string why = written < 0 ? std::strerror(errno) : "short write";
    socket_fd_ = -1;
    close(fd);
    std::cerr << "DESKMATE UART relay disconnected (" << why << ")\n";
}

}  // namespace deskmate
