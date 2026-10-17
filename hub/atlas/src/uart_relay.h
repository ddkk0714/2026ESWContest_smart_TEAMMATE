#pragma once

// UART 프레임 줄을 로컬 TCP 로 넘겨 주는 중계기 (2026-10-17).
//
// hub/atlas-tof 의 deskmate_tofposture 는 ESP32 가 UART2 로 보내는 TYPE 0x05 "L8 원본 zone"
// 프레임을 `UART\t{"type":5,...,"payload_hex":"..."}` 한 줄 형식으로 받는다. 그 서비스가
// tcp:9100 에서 **기다리고**(tofposture.env 의 TOFPOSTURE_PORTS=tcp:9100), 이 중계기가 127.0.0.1:9100
// 으로 **접속해** 줄을 밀어 넣는다. /dev/serial0 은 hub 가 혼자 열어야 하므로(두 프로세스가 같이
// 읽으면 바이트가 갈려 둘 다 프레임을 못 세운다) 수신한 줄을 여기서 복사해 준다.
//
// - 환경변수 DESKMATE_TOF_RELAY_PORT (기본 9100, 0 = 끔), DESKMATE_TOF_RELAY_HOST (기본 127.0.0.1).
// - 받는 쪽이 안 떠 있으면 2 s 마다 조용히 다시 붙어 본다. 끊기면 그 줄은 버리고 다시 붙는다.
// - 기본은 type 5 만 넘긴다. DESKMATE_TOF_RELAY_TYPES="5,3" 처럼 바꿀 수 있다(비우면 전부).
// - 쓰기는 논블로킹이다. 받는 쪽이 밀려 버퍼가 차면 연결을 끊는다(hub 의 UART 스레드를 막지 않는다).

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace deskmate {

struct UartRelayConfig {
    std::string host{"127.0.0.1"};
    int port{9100};                       // 0 이면 중계를 띄우지 않는다
    std::vector<unsigned> types{5};       // 넘길 프레임 TYPE. 비어 있으면 모든 UART 줄
    int reconnect_ms{2000};
};

UartRelayConfig uartRelayConfigFromEnvironment();

// "UART\t{"type":N,..." 줄에서 N 을 읽는다. 형식이 아니면 -1.
int uartLineType(const std::string& line);

class UartRelay {
public:
    explicit UartRelay(UartRelayConfig config);
    ~UartRelay();

    void start();
    void stop();

    // UartReceiver 의 sink 에서 부른다. 붙어 있으면 줄을 쓰고, 아니면 버린다.
    void publish(const std::string& line);

    bool connected() const { return socket_fd_.load() >= 0; }
    std::uint64_t forwarded() const { return forwarded_.load(); }
    std::uint64_t dropped() const { return dropped_.load(); }
    std::uint64_t connects() const { return connects_.load(); }

private:
    void connectLoop();
    int tryConnect();
    void disconnect(const char* why);
    bool wants(const std::string& line) const;

    UartRelayConfig config_;
    std::atomic<bool> running_{false};
    std::thread connector_;
    std::mutex write_mutex_;
    std::atomic<int> socket_fd_{-1};
    std::atomic<std::uint64_t> forwarded_{0};
    std::atomic<std::uint64_t> dropped_{0};
    std::atomic<std::uint64_t> connects_{0};
    bool refused_logged_{false};
};

}  // namespace deskmate
