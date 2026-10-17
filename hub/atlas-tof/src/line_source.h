// 줄 단위 입력 하나 = 센서 하나. tty(ESP32 USB, 8N1 raw)이거나 리플레이용 보통 파일.
// 자기 스레드에서 읽어 줄마다 sink 를 부른다. tty 는 못 열거나 끊기면 3초마다 다시 연다
// (USB 를 뽑았다 꽂아도 서비스는 살아 있다). 보통 파일은 줄의 "ms" 로 실제 속도를 흉내 낸다.
// baud 0 = 자동: 바이트는 오는데 센서 줄로 안 읽히면 3초마다 다음 보드율로 바꾼다
// (tof_logger 펌웨어 921600, bring-up 펌웨어는 미확인).
// path 가 "tcp:<port>" 면 그 포트에서 연결을 기다려 줄을 받는다 (USB 직결이 안 될 때 PC 가 넘겨 줌).
// path 가 "녹화.jsonl@COM5" 면 tof_live.py --record 녹화에서 그 포트 줄만 리플레이한다.
#ifndef DESKMATE_TOF_LINE_SOURCE_H
#define DESKMATE_TOF_LINE_SOURCE_H

#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace tof {

class LineSource {
public:
    using Sink = std::function<void(const std::string& line)>;

    LineSource(std::string path, int baud, Sink sink);
    ~LineSource();

    void start();
    void stop();

    const std::string& path() const { return path_; }
    int baud() const { return baud_; }                   // 지금 쓰는 보드율 (auto 면 찾은 값)
    bool open() const { return open_; }
    bool isTty() const { return is_tty_; }
    bool isTcp() const { return is_tcp_; }
    bool finished() const { return finished_; }          // 리플레이 파일을 끝까지 읽음
    uint64_t bytes() const { return bytes_; }
    uint64_t lines() const { return lines_; }
    std::string lastError() const;

private:
    void run();
    int openDevice();
    int acceptTcp();
    void setError(const std::string& e);

    std::string path_;
    std::string open_path_;          // 실제로 여는 경로 ("녹화@포트" 면 녹화 파일)
    std::string port_filter_;        // 녹화 리플레이에서 고를 포트 (비면 전부)
    std::atomic<int> baud_;          // 0 = auto: 921600 → 115200 → 460800 → 230400 순으로 돌며 줄이 읽히는 값을 찾는다
    bool auto_baud_ = false;
    int baud_index_ = 0;
    Sink sink_;
    std::atomic<bool> running_{false}, open_{false}, is_tty_{false}, is_tcp_{false}, finished_{false};
    int listen_fd_ = -1;
    std::atomic<uint64_t> bytes_{0}, lines_{0};
    mutable std::mutex err_mutex_;
    std::string last_error_;
    std::thread worker_;
};

}  // namespace tof

#endif  // DESKMATE_TOF_LINE_SOURCE_H
