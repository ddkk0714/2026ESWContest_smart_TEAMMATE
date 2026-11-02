// 호스트 테스트: 제어 진행 로그 링버퍼 — command_id 마다 한 건(최신 스냅샷이 맨 뒤), 용량 초과 시 오래된 것부터 버림,
// limit, 잘못된 줄 거부, command_id 추출(공백·이스케이프).
#include "control_log.h"

#include <iostream>
#include <string>

namespace {

int failures = 0;

void check(bool condition, const char* what)
{
    std::cout << (condition ? "ok   " : "FAIL ") << what << '\n';
    if (!condition) ++failures;
}

std::string rec(const std::string& id, const std::string& status)
{
    return "{\"v\":1,\"command_id\":\"" + id + "\",\"status\":\"" + status + "\"}";
}

}  // namespace

int main()
{
    using deskmate::ControlLogBuffer;
    using deskmate::extractCommandId;

    check(extractCommandId(rec("abc123", "sent")) == "abc123", "command_id 추출");
    check(extractCommandId("{\"command_id\" : \"b-3-speak\"}") == "b-3-speak", "콜론 앞뒤 공백");
    check(extractCommandId("{\"command_id\":\"a\\\"b\"}") == "a\"b", "이스케이프된 따옴표");
    check(extractCommandId("{\"command_id\":12}").empty(), "문자열이 아니면 빈 값");
    check(extractCommandId("{\"status\":\"x\"}").empty(), "키가 없으면 빈 값");

    ControlLogBuffer buffer(3);
    check(buffer.toJsonArray() == "[]", "빈 버퍼는 []");
    check(!buffer.add("not json"), "객체가 아니면 거부");
    check(!buffer.add("{\"status\":\"x\"}"), "command_id 없으면 거부");
    check(buffer.add(rec("a", "executing")) && buffer.add(rec("b", "executing")), "두 건 추가");
    check(buffer.add(rec("a", "succeeded")), "같은 id 갱신");
    check(buffer.size() == 2, "같은 id 는 한 건");
    check(buffer.toJsonArray() == "[" + rec("b", "executing") + "," + rec("a", "succeeded") + "]",
          "갱신된 건이 맨 뒤, 최신 스냅샷");
    buffer.add(rec("c", "skipped"));
    buffer.add(rec("d", "timeout"));
    check(buffer.size() == 3, "용량 3 유지");
    check(buffer.toJsonArray().find("\"b\"") == std::string::npos, "가장 오래된 b 가 버려짐");
    check(buffer.toJsonArray(2) == "[" + rec("c", "skipped") + "," + rec("d", "timeout") + "]", "limit 은 최근 것");
    check(buffer.toJsonArray(10) == buffer.toJsonArray(), "limit 이 크면 전부");

    std::cout << (failures == 0 ? "ALL PASS" : "FAILURES") << '\n';
    return failures == 0 ? 0 : 1;
}
