// 아주 작은 HTTP/1.0 서버. 앱·개발 PC 가 자세를 읽는 길이다 (camsvc 8770, hub 8765 와 같은 방식).
//   GET  /health       서비스·센서 상태
//   GET  /api/state    현재 자세 (device_posture.h 의 stateJson)
//   POST /api/reset    모든 GRU 상태 0 (센서를 옮겨 놓은 뒤 등)
//   GET  /raw          센서 원본 격자 (uart_relay_board 와 같은 스키마). TOFPOSTURE_RAW_HTTP 포트의 두 번째 리스너는 이것만 낸다
//   GET/POST /api/placement  장치 위치(left·center·right)와 위치별 모델 전환 (placement.h). 기능이 꺼져 있으면 404
#ifndef DESKMATE_TOF_HTTP_SERVER_H
#define DESKMATE_TOF_HTTP_SERVER_H

#include <atomic>
#include <functional>
#include <string>
#include <thread>

namespace tof {

struct HttpHandlers {
    std::function<std::string()> health;
    std::function<std::string()> state;
    std::function<void()> reset;
    std::function<std::string()> raw;      // 비우면 /raw 는 404
    std::function<std::string()> placement_get;                                  // 비우면 /api/placement 는 404
    std::function<int(const std::string& body, std::string& out)> placement_post; // HTTP 코드와 본문을 돌려준다
};

class HttpServer {
public:
    HttpServer(std::string bind_addr, int port, HttpHandlers handlers);
    ~HttpServer();
    bool start(std::string* err = nullptr);        // listen 실패면 false
    void stop();
    uint64_t requests() const { return requests_; }

private:
    void run();
    void handle(int client);

    std::string bind_addr_;
    int port_;
    HttpHandlers handlers_;
    int listen_fd_ = -1;
    std::atomic<bool> running_{false};
    std::atomic<uint64_t> requests_{0};
    std::thread worker_;
};

}  // namespace tof

#endif  // DESKMATE_TOF_HTTP_SERVER_H
