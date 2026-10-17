#include "line_source.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <termios.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>

#include "tof_line.h"

namespace tof {
namespace {

speed_t baudToSpeed(int baud) {
    switch (baud) {
    case 9600: return B9600;
    case 19200: return B19200;
    case 38400: return B38400;
    case 57600: return B57600;
    case 115200: return B115200;
#ifdef B230400
    case 230400: return B230400;
#endif
#ifdef B460800
    case 460800: return B460800;
#endif
#ifdef B921600
    case 921600: return B921600;
#endif
#ifdef B1000000
    case 1000000: return B1000000;
#endif
    default: return B115200;
    }
}

bool isCharDevice(const std::string& path) {
    struct stat st {};
    return stat(path.c_str(), &st) == 0 && S_ISCHR(st.st_mode);
}

}  // namespace

namespace {
const int kAutoBauds[] = {921600, 115200, 460800, 230400};
constexpr int kAutoBaudCount = 4;
}  // namespace

LineSource::LineSource(std::string path, int baud, Sink sink)
    : path_(std::move(path)), baud_(baud > 0 ? baud : kAutoBauds[0]), auto_baud_(baud <= 0), sink_(std::move(sink)) {
    open_path_ = path_;
    const size_t at = path_.rfind('@');
    struct stat st {};
    if (at != std::string::npos && at > 0 && stat(path_.c_str(), &st) != 0) {
        const std::string file = path_.substr(0, at);
        if (stat(file.c_str(), &st) == 0 && S_ISREG(st.st_mode)) {
            open_path_ = file;
            port_filter_ = path_.substr(at + 1);
        }
    }
}

LineSource::~LineSource() { stop(); }

void LineSource::start() {
    if (running_) return;
    running_ = true;
    worker_ = std::thread(&LineSource::run, this);
}

void LineSource::stop() {
    running_ = false;
    if (worker_.joinable()) worker_.join();
}

std::string LineSource::lastError() const {
    std::lock_guard<std::mutex> lock(err_mutex_);
    return last_error_;
}

void LineSource::setError(const std::string& e) {
    std::lock_guard<std::mutex> lock(err_mutex_);
    last_error_ = e;
}

// "tcp:<port>" — 그 포트에서 기다리다 들어온 연결 하나에서 줄을 읽는다(USB 드라이버가 없을 때 PC 가
// tof_live.py --forward 로 센서 줄을 넘겨 준다). 연결이 끊기면 다시 기다린다.
int LineSource::acceptTcp() {
    if (listen_fd_ < 0) {
        const int port = std::atoi(path_.c_str() + 4);
        listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
        if (listen_fd_ < 0) { setError(std::string("socket failed: ") + std::strerror(errno)); return -1; }
        int one = 1;
        ::setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(static_cast<uint16_t>(port));
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        if (port <= 0 || ::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 ||
            ::listen(listen_fd_, 1) != 0) {
            setError("listen failed on " + path_ + ": " + std::strerror(errno));
            ::close(listen_fd_);
            listen_fd_ = -1;
            return -1;
        }
        setError("waiting for a connection on " + path_);
    }
    pollfd pfd{listen_fd_, POLLIN, 0};
    if (::poll(&pfd, 1, 200) <= 0) return -2;                 // 아직 아무도 안 붙음
    const int fd = ::accept(listen_fd_, nullptr, nullptr);
    if (fd < 0) return -2;
    ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL, 0) | O_NONBLOCK);
    setError("");
    return fd;
}

int LineSource::openDevice() {
    if (path_.compare(0, 4, "tcp:") == 0) {
        is_tcp_ = true;
        is_tty_ = false;
        return acceptTcp();
    }
    is_tty_ = isCharDevice(open_path_);
    const int fd = ::open(open_path_.c_str(), O_RDONLY | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        setError(std::string("open failed: ") + std::strerror(errno));
        return -1;
    }
    if (is_tty_) {
        termios settings{};
        if (tcgetattr(fd, &settings) != 0) {
            setError(std::string("tcgetattr failed: ") + std::strerror(errno));
            ::close(fd);
            return -1;
        }
        cfmakeraw(&settings);
        const speed_t speed = baudToSpeed(baud_.load());
        cfsetispeed(&settings, speed);
        cfsetospeed(&settings, speed);
        settings.c_cflag |= (CLOCAL | CREAD);
#ifdef CRTSCTS
        settings.c_cflag &= ~CRTSCTS;
#endif
        settings.c_cc[VMIN] = 0;
        settings.c_cc[VTIME] = 0;
        if (tcsetattr(fd, TCSANOW, &settings) != 0) {
            setError(std::string("tcsetattr failed: ") + std::strerror(errno));
            ::close(fd);
            return -1;
        }
        tcflush(fd, TCIFLUSH);
    }
    setError("");
    return fd;
}

void LineSource::run() {
    using clock = std::chrono::steady_clock;
    std::string buffer;
    char chunk[4096];
    int fd = -1;
    // 리플레이 파일: 첫 줄의 ms 와 실제 시각을 맞춰 두고 그 차이를 유지한다.
    bool pace_started = false;
    uint32_t pace_ms0 = 0;
    clock::time_point pace_t0;
    // 보드율 자동: 열린 뒤 바이트는 왔는데 센서 줄이 하나도 없으면 다음 후보로
    clock::time_point opened_at;
    uint64_t bytes_at_open = 0;
    bool valid_seen = false;

    while (running_) {
        if (fd < 0) {
            fd = openDevice();
            if (fd == -2) continue;                          // tcp: 연결 대기 중 (poll 200 ms)
            if (fd < 0) {
                open_ = false;
                for (int i = 0; i < 30 && running_; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
                continue;
            }
            open_ = true;
            buffer.clear();
            opened_at = clock::now();
            bytes_at_open = bytes_;
            valid_seen = false;
        }
        if (is_tty_ && auto_baud_ && !valid_seen && bytes_ > bytes_at_open &&
            clock::now() - opened_at > std::chrono::seconds(3)) {
            baud_index_ = (baud_index_ + 1) % kAutoBaudCount;
            baud_ = kAutoBauds[baud_index_];
            setError("no sensor lines, trying baud " + std::to_string(baud_.load()));
            ::close(fd); fd = -1; open_ = false;
            continue;
        }
        pollfd pfd{fd, POLLIN, 0};
        const int ready = ::poll(&pfd, 1, 100);
        if (ready < 0) {
            if (errno == EINTR) continue;
            setError(std::string("poll failed: ") + std::strerror(errno));
            ::close(fd); fd = -1; open_ = false;
            continue;
        }
        if (ready == 0) continue;
        const ssize_t n = ::read(fd, chunk, sizeof chunk);
        if (n < 0) {
            if (errno == EAGAIN || errno == EINTR) continue;
            setError(std::string("read failed: ") + std::strerror(errno));
            ::close(fd); fd = -1; open_ = false;
            continue;
        }
        if (n == 0) {
            if (is_tcp_) {                               // 보내던 쪽이 끊음 → 다음 연결을 기다린다
                setError("tcp client disconnected");
                ::close(fd); fd = -1; open_ = false;
                continue;
            }
            if (!is_tty_) {                              // 리플레이 끝
                finished_ = true;
                ::close(fd); fd = -1; open_ = false;
                break;
            }
            // tty 의 EOF = 장치가 빠짐. 다시 연다.
            setError("device disconnected");
            ::close(fd); fd = -1; open_ = false;
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }
        bytes_ += static_cast<uint64_t>(n);
        buffer.append(chunk, static_cast<size_t>(n));
        size_t start = 0;
        for (;;) {
            const size_t nl = buffer.find('\n', start);
            if (nl == std::string::npos) break;
            std::string line = buffer.substr(start, nl - start);
            start = nl + 1;
            if (line.empty()) continue;
            if (!port_filter_.empty()) {                 // 녹화에서 한 포트만
                std::string inner, port;
                if (!unwrapRecordLine(line, inner, port) || port != port_filter_) continue;
            }
            ++lines_;
            if (!valid_seen && looksLikeTofLine(line)) valid_seen = true;
            if (!is_tty_ && !is_tcp_) {                  // 파일: 줄의 ms 로 속도를 흉내 낸다
                uint32_t ms = 0;
                if (peekLineMs(line, ms)) {
                    if (!pace_started) { pace_started = true; pace_ms0 = ms; pace_t0 = clock::now(); }
                    const auto due = pace_t0 + std::chrono::milliseconds(static_cast<int64_t>(ms) - pace_ms0);
                    while (running_ && clock::now() < due) std::this_thread::sleep_for(std::chrono::milliseconds(2));
                }
            }
            if (sink_) sink_(line);
        }
        buffer.erase(0, start);
        if (buffer.size() > 65536) buffer.clear();      // 줄바꿈 없는 쓰레기
    }
    if (fd >= 0) ::close(fd);
    if (listen_fd_ >= 0) { ::close(listen_fd_); listen_fd_ = -1; }
    open_ = false;
}

}  // namespace tof
