// UART 수신부 호스트 테스트 — 보드 없이 COBS·CRC·프레임 파싱·브리지 라인 형식을 검증한다.
// assert 에 의존하지 않는다(NDEBUG 빌드에서도 검증되도록). 실패 시 메시지를 찍고 1 을 반환한다.
//
//   cmake -DBUILD_TESTING=ON .. && ctest                       (ARC 컨테이너)
//   python -m ziglang c++ -std=c++17 -target x86_64-linux-musl -static \
//       hub/atlas/src/uart_rx.cpp hub/atlas/src/uart_rx_host_test.cpp -o t   (PC 크로스 빌드 → WSL 에서 실행)
#include "uart_rx.h"

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace {

int failures = 0;

void check(bool ok, const char* what)
{
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}

std::vector<std::uint8_t> fromHex(const std::string& hex)
{
    std::vector<std::uint8_t> out;
    for (std::size_t i = 0; i + 1 < hex.size(); i += 2) {
        out.push_back(static_cast<std::uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
    }
    return out;
}

}  // namespace

int main()
{
    // CRC-16/CCITT-FALSE check value
    const std::uint8_t crc_input[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    check(deskmate::crc16CcittFalse(crc_input, sizeof(crc_input)) == 0x29B1, "crc16 check value 0x29B1");

    // COBS 표준 벡터
    {
        const std::uint8_t cobs_input[] = {0x03, 0x11, 0x22, 0x02, 0x33};
        std::vector<std::uint8_t> decoded;
        check(deskmate::cobsDecode(cobs_input, sizeof(cobs_input), decoded), "cobs decode ok");
        check(decoded == std::vector<std::uint8_t>{0x11, 0x22, 0x00, 0x33}, "cobs 11220033");
    }
    {
        const std::uint8_t cobs_input[] = {0x01, 0x01};
        std::vector<std::uint8_t> decoded;
        check(deskmate::cobsDecode(cobs_input, sizeof(cobs_input), decoded) &&
                  decoded == std::vector<std::uint8_t>{0x00},
              "cobs single zero");
    }

    // 헤더+payload+CRC 직접 구성 → 파싱
    {
        std::vector<std::uint8_t> frame = {0xA5, 0x01, 0x20, 0x34, 0x12, 0x02, 0x00,
                                           0x04, 0x03, 0x02, 0x01, 0xAB, 0xCD};
        const std::uint16_t crc = deskmate::crc16CcittFalse(frame.data(), frame.size());
        frame.push_back(static_cast<std::uint8_t>(crc & 0xFF));
        frame.push_back(static_cast<std::uint8_t>(crc >> 8));
        deskmate::ParsedUartFrame parsed;
        bool crc_error = false;
        check(deskmate::parseUartFrame(frame, parsed, crc_error) && !crc_error, "parse constructed frame");
        check(deskmate::makeBridgeUartLine(parsed) ==
                  "UART\t{\"type\":32,\"seq\":4660,\"ts_ms\":16909060,\"payload_hex\":\"abcd\"}\n",
              "bridge line format");

        // payload 1비트 손상 → CRC 불일치로 거부
        std::vector<std::uint8_t> bad_payload = frame;
        bad_payload[11] ^= 0x01;
        deskmate::ParsedUartFrame bad;
        bool bad_crc = false;
        check(!deskmate::parseUartFrame(bad_payload, bad, bad_crc) && bad_crc, "corrupted payload rejected as CRC error");
        // LEN 손상 → 길이 불일치로 거부 (CRC 오류로 세지 않음)
        std::vector<std::uint8_t> bad_len = frame;
        bad_len[5] ^= 0x01;
        bool len_crc = false;
        check(!deskmate::parseUartFrame(bad_len, bad, len_crc) && !len_crc, "bad LEN rejected as format error");
    }

    // Python 코덱(hub/deskmate_hub/ingest/uart_frame.py)과 공유하는 wire 벡터: COBS + 0x00 종료자 제거 후
    {
        const std::vector<std::uint8_t> wire = fromHex("07a501203412020904030201abcddab8");
        std::vector<std::uint8_t> decoded;
        deskmate::ParsedUartFrame parsed;
        bool crc_error = false;
        check(deskmate::cobsDecode(wire.data(), wire.size(), decoded), "python vector cobs decode");
        check(deskmate::parseUartFrame(decoded, parsed, crc_error) && !crc_error, "python vector parse");
        check(parsed.type == 0x20 && parsed.sequence == 0x1234 && parsed.timestamp_ms == 0x01020304,
              "python vector header");
        check(deskmate::makeBridgeUartLine(parsed).find("\"payload_hex\":\"abcd\"") != std::string::npos,
              "python vector payload");
    }
    // mmWave 26 B 예시 프레임 (data-spec §13.1 test vector)
    {
        const std::vector<std::uint8_t> wire = fromHex("05a5012007020b03e803010501010c37040f01ff050301dea1");
        std::vector<std::uint8_t> decoded;
        deskmate::ParsedUartFrame parsed;
        bool crc_error = false;
        check(deskmate::cobsDecode(wire.data(), wire.size(), decoded) &&
                  deskmate::parseUartFrame(decoded, parsed, crc_error) && !crc_error,
              "mmwave example frame");
        check(deskmate::makeBridgeUartLine(parsed) ==
                  "UART\t{\"type\":32,\"seq\":7,\"ts_ms\":1000,\"payload_hex\":\"01010c37000f01ff000301\"}\n",
              "mmwave example bridge line");
    }

    if (failures == 0) {
        std::printf("uart_rx host test: all checks passed\n");
        return 0;
    }
    std::fprintf(stderr, "uart_rx host test: %d failure(s)\n", failures);
    return 1;
}
