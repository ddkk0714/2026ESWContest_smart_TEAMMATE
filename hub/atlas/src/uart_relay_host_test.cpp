// 호스트 테스트: 중계기가 기다리는 서비스(127.0.0.1:<port>)에 붙어 type 5 줄만 밀어 넣고,
// 서비스가 끊기면 다시 붙는지. 받는 쪽(deskmate_tofposture 의 tcp:<port>)은 여기서 흉내 낸다.
#include "uart_relay.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

namespace {

int failures = 0;

void check(bool condition, const char* what)
{
    if (!condition) {
        std::cerr << "FAIL: " << what << '\n';
        ++failures;
    }
}

int listenOn(int port)
{
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    int reuse = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<std::uint16_t>(port));
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 || listen(fd, 1) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int acceptWithin(int listener, int timeout_ms)
{
    pollfd descriptor = {listener, POLLIN, 0};
    if (poll(&descriptor, 1, timeout_ms) <= 0) return -1;
    return accept(listener, nullptr, nullptr);
}

std::string readLine(int fd, int timeout_ms)
{
    std::string line;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (std::chrono::steady_clock::now() < deadline) {
        pollfd descriptor = {fd, POLLIN, 0};
        if (poll(&descriptor, 1, 50) <= 0) continue;
        char byte = 0;
        const ssize_t count = recv(fd, &byte, 1, 0);
        if (count <= 0) break;
        line += byte;
        if (byte == '\n') break;
    }
    return line;
}

}  // namespace

int main()
{
    check(deskmate::uartLineType("UART\t{\"type\":5,\"seq\":1}") == 5, "type 5 parsed");
    check(deskmate::uartLineType("UART\t{\"type\":32,\"seq\":1}") == 32, "type 32 parsed");
    check(deskmate::uartLineType("STATE\t{}") == -1, "non-UART line rejected");
    check(deskmate::uartLineType("UART\t{\"type\":x}") == -1, "non-numeric type rejected");

    setenv("DESKMATE_TOF_RELAY_PORT", "0", 1);
    check(deskmate::uartRelayConfigFromEnvironment().port == 0, "port 0 disables");
    setenv("DESKMATE_TOF_RELAY_TYPES", "5,3", 1);
    check(deskmate::uartRelayConfigFromEnvironment().types.size() == 2, "types parsed");
    unsetenv("DESKMATE_TOF_RELAY_TYPES");
    unsetenv("DESKMATE_TOF_RELAY_PORT");
    check(deskmate::uartRelayConfigFromEnvironment().port == 9100, "default port 9100");
    check(deskmate::uartRelayConfigFromEnvironment().host == "127.0.0.1", "default host loopback");

    const int port = 19100 + (getpid() % 400);
    const std::string other = "UART\t{\"type\":32,\"seq\":7,\"ts_ms\":1,\"payload_hex\":\"ff\"}\n";
    const std::string tof = "UART\t{\"type\":5,\"seq\":8,\"ts_ms\":2,\"payload_hex\":\"0102\"}\n";

    // 1) 받는 쪽이 아직 없을 때: 조용히 버리고 재시도만 한다.
    deskmate::UartRelayConfig config;
    config.port = port;
    config.reconnect_ms = 300;
    deskmate::UartRelay relay(config);
    relay.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(400));
    check(!relay.connected(), "not connected without a listener");
    relay.publish(tof);
    check(relay.forwarded() == 0 && relay.dropped() == 1, "line dropped while disconnected");

    // 2) 서비스가 뜨면 붙는다.
    const int listener = listenOn(port);
    check(listener >= 0, "test listener");
    const int peer = acceptWithin(listener, 3000);
    check(peer >= 0, "relay connected after listener appeared");
    for (int i = 0; i < 20 && !relay.connected(); ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    check(relay.connected(), "relay reports connected");

    relay.publish(other);
    relay.publish("STATE\t{}\n");
    relay.publish(tof);
    check(readLine(peer, 1000) == tof, "only the type 5 line arrives");
    check(relay.forwarded() == 1, "forwarded counter");

    // 3) 서비스가 연결을 닫으면 다시 붙는다 (tofposture 는 한 번에 한 연결만 받고 끊기면 다시 기다린다).
    close(peer);
    const int peer2 = acceptWithin(listener, 3000);
    check(peer2 >= 0, "relay reconnected after peer closed");
    for (int i = 0; i < 40 && relay.connects() < 2; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    check(relay.connects() == 2, "two connects counted");
    relay.publish(tof);
    check(readLine(peer2, 1000) == tof, "line arrives on the new connection");
    close(peer2);
    close(listener);

    relay.stop();
    check(!relay.connected(), "relay stopped");

    if (failures == 0) std::cout << "uart_relay host test: ok\n";
    return failures == 0 ? 0 : 1;
}
