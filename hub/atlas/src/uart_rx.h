#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <thread>
#include <vector>

namespace deskmate {

struct UartRxConfig {
    std::string device{"/dev/serial0"};
    int baud{115200};
};

struct ParsedUartFrame {
    std::uint8_t type{};
    std::uint16_t sequence{};
    std::uint32_t timestamp_ms{};
    std::vector<std::uint8_t> payload;
};

struct UartRxCounters {
    std::atomic<std::uint64_t> received{0};
    std::atomic<std::uint64_t> discarded{0};
    std::atomic<std::uint64_t> crc_errors{0};
};

std::uint16_t crc16CcittFalse(const std::uint8_t* data, std::size_t length);
bool cobsDecode(const std::uint8_t* encoded, std::size_t length,
                std::vector<std::uint8_t>& decoded);
bool parseUartFrame(const std::vector<std::uint8_t>& decoded, ParsedUartFrame& frame,
                    bool& crc_error);
std::string makeBridgeUartLine(const ParsedUartFrame& frame);
UartRxConfig uartRxConfigFromEnvironment();

class UartReceiver {
public:
    using LineSink = std::function<bool(const std::string&)>;

    UartReceiver(UartRxConfig config, LineSink sink);
    ~UartReceiver();

    void start();
    void stop();
    const UartRxCounters& counters() const;

private:
    void run();
    void processEncodedFrame(const std::vector<std::uint8_t>& encoded);

    UartRxConfig config_;
    LineSink sink_;
    UartRxCounters counters_;
    std::atomic<bool> running_{false};
    std::thread worker_;
};

}  // namespace deskmate
