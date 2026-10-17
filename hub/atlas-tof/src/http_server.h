// 아주 작은 HTTP/1.0 서버. 앱·개발 PC 가 자세를 읽는 길이다 (camsvc 8770, hub 8765 와 같은 방식).
//   GET  /health       서비스·센서 상태
//   GET  /api/state    현재 자세 (device_posture.h 의 stateJson)
//   POST /api/reset    모든 GRU 상태 0 (센서를 옮겨 놓은 뒤 등)
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
