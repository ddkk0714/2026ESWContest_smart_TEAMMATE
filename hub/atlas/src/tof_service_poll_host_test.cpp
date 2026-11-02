// 호스트 테스트: 자세 서비스(/api/state) 를 흉내 내는 HTTP 서버에 폴러가 붙어 본문을 TOFSVC 줄로 넘기는지,
// 서비스가 없으면 조용히 실패하고 다시 붙는지, HTTP 응답 파싱·줄 만들기.
#include "tof_service_poll.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

int failures = 0;

void check(bool condition, const char* what)
{
    std::cout << (condition ? "ok   " : "FAIL ") << what << '\n';
    if (!condition) ++failures;
}

int listenOn(int port)
{
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    int reuse = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<std::uint16_t>(port));
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 || listen(fd, 4) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int boundPort(int fd)
{
    sockaddr_in address = {};
    socklen_t length = sizeof(address);
    getsockname(fd, reinterpret_cast<sockaddr*>(&address), &length);
    return ntohs(address.sin_port);
}

// 요청 한 개를 받아 body 를 돌려 주고 닫는 서버(서비스처럼 응답 뒤 연결을 닫는다). `requests` 개만 처리한다.
void serve(int listener, std::string body, int requests, std::atomic<int>& served, bool chunk_body)
{
    for (int i = 0; i < requests; ++i) {
        pollfd descriptor = {listener, POLLIN, 0};
        if (poll(&descriptor, 1, 5000) <= 0) return;
        const int client = accept(listener, nullptr, nullptr);
        if (client < 0) return;
        char buffer[1024];
        std::string request;
        for (int tries = 0; tries < 50 && request.find("\r\n\r\n") == std::string::npos; ++tries) {
            pollfd rd = {client, POLLIN, 0};
            if (poll(&rd, 1, 100) <= 0) continue;
            const ssize_t n = recv(client, buffer, sizeof(buffer), 0);
            if (n <= 0) break;
            request.append(buffer, static_cast<std::size_t>(n));
        }
        const bool wants_state = request.rfind("GET /api/state ", 0) == 0;
        const std::string head = wants_state
            ? "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + std::to_string(body.size()) + "\r\n\r\n"
            : std::string("HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n");
        send(client, head.data(), head.size(), MSG_NOSIGNAL);
        if (wants_state) {
            if (chunk_body) {
                // 본문을 둘로 나눠 보내 부분 수신을 흉내 낸다
                const std::size_t half = body.size() / 2;
                send(client, body.data(), half, MSG_NOSIGNAL);
                std::this_thread::sleep_for(std::chrono::milliseconds(60));
                send(client, body.data() + half, body.size() - half, MSG_NOSIGNAL);
            } else {
                send(client, body.data(), body.size(), MSG_NOSIGNAL);
            }
        }
        close(client);
        ++served;
    }
}

}  // namespace

int main()
{
    using deskmate::TofServicePollConfig;
    using deskmate::TofServicePoller;

    // 1. HTTP 파싱·줄 만들기
    {
        std::string body;
        int status = 0;
        check(deskmate::parseHttpResponseBody("HTTP/1.0 200 OK\r\nContent-Length: 14\r\n\r\n{\"valid\":true}\n", body, status)
                  && status == 200 && body == "{\"valid\":true}", "parse 200 with Content-Length trims trailing bytes");
        check(!deskmate::parseHttpResponseBody("HTTP/1.1 404 Not Found\r\n\r\n", body, status) && status == 404,
              "parse non-200 is rejected with status");
        check(!deskmate::parseHttpResponseBody("garbage", body, status) && status == 0, "parse garbage rejected");
        check(deskmate::parseHttpResponseBody("HTTP/1.1 200 OK\n\n{\"a\":1}", body, status) && body == "{\"a\":1}",
              "parse LF-only headers");
        check(deskmate::makeTofServiceLine("{\"a\":1,\r\n \"b\":2}\n") == "TOFSVC\t{\"a\":1,   \"b\":2}\n",
              "line replaces newlines and ends with one LF");
    }

    // 2. 서비스 없음 → 실패를 세고 sink 는 안 부른다
    {
        TofServicePollConfig config;
        config.port = 9;          // discard 포트 - 보통 닫혀 있다
        config.timeout_ms = 300;
        int sink_calls = 0;
        TofServicePoller poller(config, [&sink_calls](const std::string&) { ++sink_calls; return true; });
        check(!poller.pollOnce(), "pollOnce fails when service is down");
        check(poller.errors() == 1 && poller.ok() == 0 && sink_calls == 0, "down counters");
    }

    // 3. 서비스 흉내 → 본문이 TOFSVC 줄로 sink 에 닿는다(부분 수신 포함), 404 는 실패, 스레드 폴링도 돈다
    {
        const int listener = listenOn(0);
        check(listener >= 0, "listen on loopback");
        const int port = boundPort(listener);
        const std::string body = "{\"schema\":\"deskmate/tof_posture/1\",\"valid\":true,\"posture\":\"drowsy\",\"conf\":0.91,\n"
                                 " \"online_sensors\":2}\n";
        std::atomic<int> served{0};
        std::thread server(serve, listener, body, 4, std::ref(served), true);

        std::mutex lines_mutex;
        std::vector<std::string> lines;
        TofServicePollConfig config;
        config.port = port;
        config.period_ms = 150;
        config.timeout_ms = 1500;
        TofServicePoller poller(config, [&](const std::string& line) {
            std::lock_guard<std::mutex> lock(lines_mutex);
            lines.push_back(line);
            return true;
        });
        check(poller.pollOnce(), "pollOnce succeeds against mock service");
        {
            std::lock_guard<std::mutex> lock(lines_mutex);
            check(lines.size() == 1, "one line per poll");
            check(!lines.empty() && lines[0].rfind("TOFSVC\t{\"schema\"", 0) == 0, "line has TOFSVC prefix and body");
            check(!lines.empty() && lines[0].find('\n') == lines[0].size() - 1, "line is single-line");
            check(!lines.empty() && lines[0].find("\"online_sensors\":2}") != std::string::npos, "chunked body fully received");
        }
        // 404 경로
        TofServicePollConfig bad = config;
        bad.path = "/nope";
        TofServicePoller bad_poller(bad, [](const std::string&) { return true; });
        check(!bad_poller.pollOnce() && bad_poller.errors() == 1, "404 counts as error");

        // 스레드: 남은 요청 2개를 주기 폴링으로 소비
        poller.start();
        for (int i = 0; i < 40 && served.load() < 4; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(50));
        poller.stop();
        check(served.load() == 4, "threaded poller keeps polling");
        {
            std::lock_guard<std::mutex> lock(lines_mutex);
            check(lines.size() >= 3, "threaded polls reached the sink");
        }
        server.join();
        close(listener);
    }

    // 4. 환경변수
    {
        setenv("DESKMATE_TOF_SERVICE_PORT", "0", 1);
        setenv("DESKMATE_TOF_SERVICE_PERIOD_MS", "250", 1);
        setenv("DESKMATE_TOF_SERVICE_PATH", "nopath", 1);          // '/' 로 시작하지 않으면 무시
        const TofServicePollConfig config = deskmate::tofServicePollConfigFromEnvironment();
        check(config.port == 0 && config.period_ms == 250 && config.path == "/api/state" && config.host == "127.0.0.1",
              "environment overrides");
        TofServicePoller off(config, [](const std::string&) { return true; });
        off.start();                                                // port 0 → 띄우지 않는다
        check(off.polls() == 0, "port 0 disables the poller");
        off.stop();
        unsetenv("DESKMATE_TOF_SERVICE_PORT");
        unsetenv("DESKMATE_TOF_SERVICE_PERIOD_MS");
        unsetenv("DESKMATE_TOF_SERVICE_PATH");
        check(deskmate::tofServicePollConfigFromEnvironment().port == 8771, "default port 8771");
    }

    if (failures == 0) std::cout << "PASS tof_service_poll_host_test\n";
    else std::cout << failures << " failure(s)\n";
    return failures == 0 ? 0 : 1;
}
