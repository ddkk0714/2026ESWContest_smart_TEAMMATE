#include "uart_rx.h"

#include <cassert>
#include <cstdint>
#include <vector>

int main()
{
    const std::uint8_t crc_input[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    assert(deskmate::crc16CcittFalse(crc_input, sizeof(crc_input)) == 0x29B1);

    const std::uint8_t cobs_input[] = {0x03, 0x11, 0x22, 0x02, 0x33};
    std::vector<std::uint8_t> decoded;
    assert(deskmate::cobsDecode(cobs_input, sizeof(cobs_input), decoded));
    assert((decoded == std::vector<std::uint8_t>{0x11, 0x22, 0x00, 0x33}));

    std::vector<std::uint8_t> frame = {0xA5, 0x01, 0x20, 0x34, 0x12, 0x02, 0x00,
                                       0x04, 0x03, 0x02, 0x01, 0xAB, 0xCD};
    const std::uint16_t crc = deskmate::crc16CcittFalse(frame.data(), frame.size());
    frame.push_back(static_cast<std::uint8_t>(crc & 0xFF));
    frame.push_back(static_cast<std::uint8_t>(crc >> 8));
    deskmate::ParsedUartFrame parsed;
    bool crc_error = false;
    assert(deskmate::parseUartFrame(frame, parsed, crc_error));
    assert(!crc_error);
    assert(deskmate::makeBridgeUartLine(parsed) ==
           "UART\t{\"type\":32,\"seq\":4660,\"ts_ms\":16909060,"
           "\"payload_hex\":\"abcd\"}\n");
    return 0;
}
