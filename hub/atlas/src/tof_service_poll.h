#pragma once

// 별도 자세 서비스(deskmate_tofposture, HTTP 8771) 의 /api/state 를 주기 폴링해 Python 브리지에 넘기는 폴러 (2026-10-23).
//
// hub 의 FSM 은 Python 브리지(deskmate_hub) 안에서 돈다. 그 Python 은 보드의 제한 빌드(/restricted/python3) 라
// `_socket` 이 없어 HTTP 를 직접 못 읽는다. 그래서 이 네이티브 서비스가 1 s 마다 GET /api/state 를 받아
// `TOFSVC\t<json 한 줄>\n` 로 브리지 stdin 에 써 준다(UART\t · MQTT\t 와 같은 줄 프로토콜). Python 쪽은
// deskmate_hub/ingest/tof_service.py 가 받아 `tof_service` 표본으로 넣고, ingest.yaml `tof.posture_source: service`
// 면 그 자세로 posture Signal 을 만든다(없으면 MLP → 0x03 규칙으로 폴백).
//
// - 환경변수 DESKMATE_TOF_SERVICE_PORT (기본 8771, 0 = 끔), DESKMATE_TOF_SERVICE_HOST (기본 127.0.0.1),
//   DESKMATE_TOF_SERVICE_PATH (기본 /api/state), DESKMATE_TOF_SERVICE_PERIOD_MS (기본 1000), DESKMATE_TOF_SERVICE_TIMEOUT_MS (기본 500).
// - 서비스가 없으면 첫 실패만 로그에 남기고 조용히 주기마다 다시 시도한다. 그동안 hub 는 MLP/규칙 판정으로 돈다.
// - 요청마다 새로 연결한다(서비스는 HTTP/1.0 처럼 응답 뒤 연결을 닫는다). 블로킹 connect/read 는 쓰지 않는다.
// - 응답 본문의 개행은 공백으로 바꿔 한 줄로 만든다(줄 프로토콜).

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <thread>

namespace deskmate {

struct TofServicePollConfig {
    std::string host{"127.0.0.1"};
    int port{8771};                 // 0 이면 폴러를 띄우지 않는다
    std::string path{"/api/state"};
    int period_ms{1000};
    int timeout_ms{500};
};

TofServicePollConfig tofServicePollConfigFromEnvironment();

// HTTP 응답 전체(상태줄·헤더·본문) → 본문. 상태가 200 이 아니거나 형식이 아니면 false.
bool parseHttpResponseBody(const std::string& response, std::string& body, int& status);

// 본문을 TOFSVC 줄로 만든다: 개행·캐리지리턴은 공백, 끝에 '\n'.
std::string makeTofServiceLine(const std::string& body);

class TofServicePoller {
public:
    // sink 는 완성된 "TOFSVC\t...\n" 줄을 받는다(브리지 stdin 쓰기). 반환값은 쓰였는지.
    using LineSink = std::function<bool(const std::string&)>;

    TofServicePoller(TofServicePollConfig config, LineSink sink);
    ~TofServicePoller();

    void start();
    void stop();

    // 한 번 받아 sink 에 넘긴다. 테스트·수동 호출용. 성공이면 true.
    bool pollOnce();

    std::uint64_t polls() const { return polls_.load(); }
    std::uint64_t ok() const { return ok_.load(); }
    std::uint64_t errors() const { return errors_.load(); }

private:
    void run();
    bool fetch(std::string& response, std::string& why);

    TofServicePollConfig config_;
    LineSink sink_;
    std::atomic<bool> running_{false};
    std::thread worker_;
    std::atomic<std::uint64_t> polls_{0};
    std::atomic<std::uint64_t> ok_{0};
    std::atomic<std::uint64_t> errors_{0};
    bool down_logged_{false};
    bool up_logged_{false};
};

}  // namespace deskmate
