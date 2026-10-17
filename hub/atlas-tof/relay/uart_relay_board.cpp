// Pi 4(ATLAS) 안에서 도는 ToF UART 중계기 (hub 를 건드리지 않는 임시 경로, 2026-10-17).
//   /dev/ttyAMA0 (ESP32 UART2, COBS+CRC16 프레임) → TYPE 0x05 만 자세 서비스(deskmate_tofposture)가 읽는
//   UART<TAB>{"type":5,"seq":S,"ts_ms":T,"payload_hex":"..."} 줄로 바꿔 127.0.0.1:9100 에 넣는다.
//   펌웨어 0x05 payload(grid 배치)를 서비스 v1 배치로 변환한다 (pc/uart_relay_pc.py 와 같은 규칙).
//   덤으로 HTTP :8772 에서 센서별 마지막 원본 프레임(거리 mm·상태 64칸)을 JSON 으로 준다 (GET /raw, CORS *).
//   usage: uart_relay_board [dev=/dev/ttyAMA0] [host=127.0.0.1] [port=9100] [baud=115200] [http_port=8772]
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <string>
#include <sys/socket.h>
#include <termios.h>
#include <unistd.h>
#include <vector>

static uint16_t crc16(const uint8_t* d, size_t n) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < n; ++i) {
        crc ^= static_cast<uint16_t>(d[i] << 8);
        for (int b = 0; b < 8; ++b)
            crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021) : static_cast<uint16_t>(crc << 1);
    }
    return crc;
}

static bool cobsDecode(const std::vector<uint8_t>& in, std::vector<uint8_t>& out) {
    out.clear();
    size_t i = 0;
    const size_t n = in.size();
    while (i < n) {
        const uint8_t code = in[i];
        if (code == 0) return false;
        ++i;
        if (i + code - 1 > n) return false;
        out.insert(out.end(), in.begin() + static_cast<long>(i), in.begin() + static_cast<long>(i + code - 1));
        i += code - 1;
        if (code != 0xFF && i < n) out.push_back(0);
    }
    return true;
}

static speed_t baudConst(int b) {
    switch (b) {
    case 230400: return B230400;
    case 460800: return B460800;
    case 921600: return B921600;
    default: return B115200;
    }
}

static int openTty(const char* dev, int baud) {
    const int fd = ::open(dev, O_RDONLY | O_NOCTTY);
    if (fd < 0) return -1;
    termios t{};
    if (tcgetattr(fd, &t) == 0) {
        cfmakeraw(&t);
        cfsetispeed(&t, baudConst(baud));
        cfsetospeed(&t, baudConst(baud));
        t.c_cflag |= CLOCAL | CREAD;
        t.c_cflag &= ~CRTSCTS;
        t.c_cc[VMIN] = 1;
        t.c_cc[VTIME] = 0;
        tcsetattr(fd, TCSANOW, &t);
    }
    return fd;
}

static int connectTcp(const char* host, int port) {
    const int s = ::socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return -1;
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(static_cast<uint16_t>(port));
    if (inet_pton(AF_INET, host, &a.sin_addr) != 1) { ::close(s); return -1; }
    if (::connect(s, reinterpret_cast<sockaddr*>(&a), sizeof a) < 0) { ::close(s); return -1; }
    int one = 1;
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    return s;
}

static int listenTcp(int port) {
    const int s = ::socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return -1;
    int one = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    a.sin_port = htons(static_cast<uint16_t>(port));
    if (::bind(s, reinterpret_cast<sockaddr*>(&a), sizeof a) < 0 || ::listen(s, 8) < 0) { ::close(s); return -1; }
    return s;
}

// 센서별 u16 frame_seq: 펌웨어 frame_id(u8) 의 증가분을 누적해 랩어라운드 없이 잇는다.
struct SeqTrack {
    int last[2] = {-1, -1};
    uint16_t seq[2] = {0, 0};
    uint16_t next(int sensor, int fid) {
        if (sensor < 0 || sensor > 1) return 0;
        if (last[sensor] < 0) { last[sensor] = fid; seq[sensor] = 1; return 1; }
        const int d = (fid - last[sensor]) & 0xFF;
        last[sensor] = fid;
        seq[sensor] = static_cast<uint16_t>(seq[sensor] + (d ? d : 1));
        return seq[sensor];
    }
};

// 센서별 마지막 원본 프레임 (HTTP /raw 용)
struct LastFrame {
    bool have = false;
    uint16_t uart_seq = 0;
    uint32_t ts_ms = 0;
    int frame_id = 0, mask = 0, flags = 0, w = 0, h = 0;
    uint16_t frame_seq = 0;
    int16_t dist[64] = {};
    uint8_t st[64] = {};
    std::chrono::steady_clock::time_point at;
};

// 펌웨어 grid 배치(grid_w,grid_h,sensor,sensors,frame_id,mask,flags,res + i16 dist + u8 st)
// → 서비스 v1 배치(version=1,sensor_id,flags,w,h,res,frame_seq u16 + u16 dist + u8 st). 이미 v1 이면 그대로.
static bool toServicePayload(const uint8_t* p, size_t n, SeqTrack& trk, std::vector<uint8_t>& out, int& sensor, LastFrame* lf) {
    if (n >= 8 && p[0] == 1) {
        out.assign(p, p + n);
        sensor = p[1];
        if (lf && sensor >= 0 && sensor < 2) {
            LastFrame& f = lf[sensor];
            const size_t z = static_cast<size_t>(p[3]) * p[4];
            if (z > 0 && z <= 64 && n >= 8 + 3 * z) {
                f.have = true; f.w = p[3]; f.h = p[4]; f.flags = p[2]; f.frame_seq = static_cast<uint16_t>(p[6] | (p[7] << 8));
                for (size_t i = 0; i < z; ++i) { const uint16_t u = static_cast<uint16_t>(p[8 + 2 * i] | (p[9 + 2 * i] << 8)); f.dist[i] = (u == 0 || u == 0xFFFF) ? -1 : static_cast<int16_t>(u); f.st[i] = p[8 + 2 * z + i]; }
                f.at = std::chrono::steady_clock::now();
            }
        }
        return true;
    }
    if (n < 8) return false;
    const int gw = p[0], gh = p[1];
    sensor = p[2];
    const int fid = p[4];
    const int mask = p[5];
    const uint8_t flags = p[6];
    const size_t z = static_cast<size_t>(gw) * static_cast<size_t>(gh);
    if (z == 0 || z > 64 || n < 8 + 3 * z) return false;
    const uint8_t sflags = static_cast<uint8_t>(1 | 2 | ((flags & 1) ? 4 : 0) | ((flags & 2) ? 8 : 0));
    const uint16_t seq = trk.next(sensor, fid);
    out.clear();
    out.reserve(8 + 3 * z);
    out.push_back(1);
    out.push_back(static_cast<uint8_t>(sensor));
    out.push_back(sflags);
    out.push_back(static_cast<uint8_t>(gw));
    out.push_back(static_cast<uint8_t>(gh));
    out.push_back(0);
    out.push_back(static_cast<uint8_t>(seq & 0xFF));
    out.push_back(static_cast<uint8_t>(seq >> 8));
    LastFrame* f = (lf && sensor >= 0 && sensor < 2) ? &lf[sensor] : nullptr;
    for (size_t i = 0; i < z; ++i) {
        const int16_t d = static_cast<int16_t>(p[8 + 2 * i] | (p[9 + 2 * i] << 8));
        const uint16_t u = d < 0 ? 0 : static_cast<uint16_t>(d);
        out.push_back(static_cast<uint8_t>(u & 0xFF));
        out.push_back(static_cast<uint8_t>(u >> 8));
        if (f) { f->dist[i] = d; f->st[i] = p[8 + 2 * z + i]; }
    }
    out.insert(out.end(), p + 8 + 2 * z, p + 8 + 3 * z);
    if (f) { f->have = true; f->w = gw; f->h = gh; f->frame_id = fid; f->mask = mask; f->flags = flags; f->frame_seq = seq; f->at = std::chrono::steady_clock::now(); }
    return true;
}

static std::string rawJson(const LastFrame* lf, const char* dev, unsigned long frames, unsigned long t5, unsigned long sent, unsigned long bad) {
    const auto now = std::chrono::steady_clock::now();
    std::string j = "{\"dev\":\"" + std::string(dev) + "\",\"frames\":" + std::to_string(frames) + ",\"type5\":" + std::to_string(t5) +
                    ",\"sent\":" + std::to_string(sent) + ",\"bad\":" + std::to_string(bad) + ",\"sensors\":[";
    for (int s = 0; s < 2; ++s) {
        const LastFrame& f = lf[s];
        if (s) j += ",";
        j += "{\"id\":" + std::to_string(s) + ",\"have\":" + (f.have ? "true" : "false");
        if (f.have) {
            const long age = std::chrono::duration_cast<std::chrono::milliseconds>(now - f.at).count();
            j += ",\"age_ms\":" + std::to_string(age) + ",\"uart_seq\":" + std::to_string(f.uart_seq) + ",\"ts_ms\":" + std::to_string(f.ts_ms) +
                 ",\"frame_id\":" + std::to_string(f.frame_id) + ",\"frame_seq\":" + std::to_string(f.frame_seq) + ",\"mask\":" + std::to_string(f.mask) +
                 ",\"flags\":" + std::to_string(f.flags) + ",\"flip_rows\":" + ((f.flags & 1) ? "true" : "false") + ",\"flip_cols\":" + ((f.flags & 2) ? "true" : "false") +
                 ",\"w\":" + std::to_string(f.w) + ",\"h\":" + std::to_string(f.h) + ",\"dist_mm\":[";
            const int z = f.w * f.h;
            for (int i = 0; i < z; ++i) { if (i) j += ","; j += std::to_string(f.dist[i]); }
            j += "],\"status\":[";
            for (int i = 0; i < z; ++i) { if (i) j += ","; j += std::to_string(f.st[i]); }
            j += "]";
        }
        j += "}";
    }
    j += "]}";
    return j;
}

static void serveHttp(int lfd, const LastFrame* lf, const char* dev, unsigned long frames, unsigned long t5, unsigned long sent, unsigned long bad) {
    const int c = ::accept(lfd, nullptr, nullptr);
    if (c < 0) return;
    pollfd p{c, POLLIN, 0};
    char req[1024];
    ssize_t n = 0;
    if (::poll(&p, 1, 300) > 0) n = ::read(c, req, sizeof req - 1);
    if (n < 0) n = 0;
    req[n] = 0;
    std::string body, status = "200 OK", type = "application/json";
    if (std::strncmp(req, "GET /raw", 8) == 0 || std::strncmp(req, "GET / ", 6) == 0) body = rawJson(lf, dev, frames, t5, sent, bad);
    else if (std::strncmp(req, "OPTIONS", 7) == 0) body = "";
    else { status = "404 Not Found"; type = "text/plain"; body = "GET /raw\n"; }
    const std::string resp = "HTTP/1.0 " + status + "\r\nContent-Type: " + type + "\r\nContent-Length: " + std::to_string(body.size()) +
                             "\r\nAccess-Control-Allow-Origin: *\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" + body;
    size_t off = 0;
    while (off < resp.size()) {
        const ssize_t w = ::send(c, resp.data() + off, resp.size() - off, MSG_NOSIGNAL);
        if (w <= 0) break;
        off += static_cast<size_t>(w);
    }
    ::close(c);
}

int main(int argc, char** argv) {
    const char* dev = argc > 1 ? argv[1] : "/dev/ttyAMA0";
    const char* host = argc > 2 ? argv[2] : "127.0.0.1";
    const int port = argc > 3 ? std::atoi(argv[3]) : 9100;
    const int baud = argc > 4 ? std::atoi(argv[4]) : 115200;
    const int http_port = argc > 5 ? std::atoi(argv[5]) : 8772;
    static const char hexd[] = "0123456789abcdef";
    int tty = -1, sock = -1;
    const int lfd = listenTcp(http_port);
    if (lfd < 0) std::fprintf(stderr, "relay: http :%d bind failed (%s) — /raw 없이 계속\n", http_port, std::strerror(errno));
    else std::fprintf(stderr, "relay: http GET /raw on :%d\n", http_port);
    std::vector<uint8_t> enc, raw, conv;
    enc.reserve(512);
    SeqTrack trk;
    LastFrame last[2];
    unsigned long frames = 0, t5 = 0, sent = 0, bad = 0, bytes = 0;
    auto last_report = std::chrono::steady_clock::now();
    std::string line;
    line.reserve(512);
    uint8_t chunk[512];
    for (;;) {
        if (tty < 0) {
            tty = openTty(dev, baud);
            if (tty < 0) { std::fprintf(stderr, "relay: open %s failed: %s\n", dev, std::strerror(errno)); sleep(2); continue; }
            std::fprintf(stderr, "relay: reading %s @%d -> %s:%d\n", dev, baud, host, port);
        }
        if (sock < 0) {
            sock = connectTcp(host, port);
            if (sock >= 0) std::fprintf(stderr, "relay: connected %s:%d\n", host, port);
        }
        pollfd pf[2] = {{tty, POLLIN, 0}, {lfd, POLLIN, 0}};
        const int ready = ::poll(pf, lfd >= 0 ? 2 : 1, 1000);
        if (ready < 0) { if (errno == EINTR) continue; }
        if (lfd >= 0 && (pf[1].revents & POLLIN)) serveHttp(lfd, last, dev, frames, t5, sent, bad);
        if (!(pf[0].revents & (POLLIN | POLLHUP | POLLERR))) {
            const auto now = std::chrono::steady_clock::now();
            if (now - last_report >= std::chrono::seconds(10)) { last_report = now; std::fprintf(stderr, "relay: bytes=%lu frames=%lu type5=%lu sent=%lu bad=%lu (idle)\n", bytes, frames, t5, sent, bad); }
            continue;
        }
        const ssize_t n = ::read(tty, chunk, sizeof chunk);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            std::fprintf(stderr, "relay: tty read ended (%s), reopening\n", n < 0 ? std::strerror(errno) : "eof");
            ::close(tty); tty = -1; sleep(1); continue;
        }
        bytes += static_cast<unsigned long>(n);
        for (ssize_t i = 0; i < n; ++i) {
            const uint8_t b = chunk[i];
            if (b != 0) { if (enc.size() < 4096) enc.push_back(b); continue; }
            if (enc.empty()) continue;
            const bool ok = cobsDecode(enc, raw);
            enc.clear();
            if (!ok || raw.size() < 13) { ++bad; continue; }
            const uint8_t* r = raw.data();
            // header: SOF u8 | VER u8 | TYPE u8 | SEQ u16 | LEN u16 | TS_MS u32 (little-endian) = 11 B, 그 뒤 payload, CRC16
            constexpr size_t H = 11;
            const uint16_t len = static_cast<uint16_t>(r[5] | (r[6] << 8));
            if (r[0] != 0xA5 || r[1] != 0x01 || raw.size() < H + len + 2u) { ++bad; continue; }
            const uint16_t crc = static_cast<uint16_t>(r[H + len] | (r[H + len + 1] << 8));
            if (crc != crc16(r, H + len)) { ++bad; continue; }
            ++frames;
            if (r[2] != 0x05) continue;
            ++t5;
            const uint16_t seq = static_cast<uint16_t>(r[3] | (r[4] << 8));
            const uint32_t ts_ms = static_cast<uint32_t>(r[7]) | (static_cast<uint32_t>(r[8]) << 8) |
                                   (static_cast<uint32_t>(r[9]) << 16) | (static_cast<uint32_t>(r[10]) << 24);
            int sensor = -1;
            if (!toServicePayload(r + H, len, trk, conv, sensor, last)) { ++bad; continue; }
            if (sensor >= 0 && sensor < 2) { last[sensor].uart_seq = seq; last[sensor].ts_ms = ts_ms; }
            line.assign("UART\t{\"type\":5,\"seq\":");
            line += std::to_string(seq);
            line += ",\"ts_ms\":";
            line += std::to_string(ts_ms);
            line += ",\"payload_hex\":\"";
            for (const uint8_t c : conv) { line.push_back(hexd[c >> 4]); line.push_back(hexd[c & 15]); }
            line += "\"}\n";
            if (sock < 0) { sock = connectTcp(host, port); if (sock < 0) continue; }
            if (::send(sock, line.data(), line.size(), MSG_NOSIGNAL) < 0) {
                std::fprintf(stderr, "relay: send failed (%s), reconnecting\n", std::strerror(errno));
                ::close(sock); sock = -1; continue;
            }
            ++sent;
        }
        const auto now = std::chrono::steady_clock::now();
        if (now - last_report >= std::chrono::seconds(10)) {
            last_report = now;
            std::fprintf(stderr, "relay: bytes=%lu frames=%lu type5=%lu sent=%lu bad=%lu\n", bytes, frames, t5, sent, bad);
        }
    }
}
