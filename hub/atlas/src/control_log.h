#pragma once

// 제어 진행 로그 링버퍼 (2026-10-31).
//
// Python 브리지(deskmate_hub/control/control_log.py)가 명령 하나의 단계가 바뀔 때마다 그 레코드 전체를
// `CTLLOG\t<json 한 줄>` 로 stdout 에 쓴다. 이 서비스는 그 줄을 command_id 마다 한 건으로 묶어 최근 N건(기본 200)을
// 들고 있다가 GET /api/control-log 로 JSON 배열을 돌려준다(오래된 것 먼저). 같은 command_id 가 다시 오면 최신 스냅샷으로
// 바꾸고 맨 뒤로 옮긴다 — 시연 중 노트북 devview 가 "방금 무엇이 왜 어떻게 됐나" 를 본다. MQTT 로는 내지 않는다.
//
// JSON 은 해석하지 않는다(Python 이 만든 한 줄을 그대로 보관). command_id 만 문자열 검색으로 꺼낸다.

#include <cstddef>
#include <deque>
#include <mutex>
#include <string>
#include <utility>

namespace deskmate {

class ControlLogBuffer {
public:
    explicit ControlLogBuffer(std::size_t capacity = 200);

    // CTLLOG 줄의 본문(JSON 객체 한 줄). command_id 가 없거나 객체가 아니면 false.
    bool add(const std::string& record);
    // 최근 limit 건(0 = 전부)을 JSON 배열로, 오래된 것 먼저.
    std::string toJsonArray(std::size_t limit = 0) const;
    std::size_t size() const;

private:
    std::size_t capacity_;
    mutable std::mutex mutex_;
    std::deque<std::pair<std::string, std::string>> records_;   // (command_id, json)
};

// `"command_id":"…"` 의 값. 없으면 빈 문자열.
std::string extractCommandId(const std::string& record);

}  // namespace deskmate
