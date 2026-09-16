#include "uart_rx.h"

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <poll.h>
#include <sstream>
#include <termios.h>
#include <unistd.h>
#include <utility>

namespace deskmate {
namespace {

constexpr std::uint8_t kSof = 0xA5;
constexpr std::uint8_t kVersion = 0x01;
constexpr std::size_t kHeaderLength = 11;
constexpr std::size_t kCrcLength = 2;
constexpr std::size_t kMaxPayloadLength = 256;
constexpr std::size_t kMaxEncodedLength = 512;

std::uint16_t readLe16(const std::uint8_t* value)
{
    return static_cast<std::uint16_t>(value[0]) |
           (static_cast<std::uint16_t>(value[1]) << 8U);
}

std::uint32_t readLe32(const std::uint8_t* value)
{
    return static_cast<std::uint32_t>(value[0]) |
           (static_cast<std::uint32_t>(value[1]) << 8U) |
           (static_cast<std::uint32_t>(value[2]) << 16U) |
           (static_cast<std::uint32_t>(value[3]) << 24U);
}

speed_t baudToSpeed(int baud)
{
    switch (baud) {
    case 9600: return B9600;
    case 19200: return B19200;
    case 38400: return B38400;
    case 57600: return B57600;
    case 115200: return B115200;
#ifdef B230400
    case 230400: return B230400;
#endif
#ifdef B460800
    case 460800: return B460800;
#endif
    default: return B115200;
    }
}

int openSerial(const UartRxConfig& config)
{
    const int fd = open(config.device.c_str(), O_RDONLY | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        return -1;
    }

    termios settings{};
    if (tcgetattr(fd, &settings) != 0) {
        close(fd);
        return -1;
    }
    cfmakeraw(&settings);
    const speed_t speed = baudToSpeed(config.baud);
    cfsetispeed(&settings, speed);
    cfsetospeed(&settings, speed);
    settings.c_cflag |= (CLOCAL | CREAD);
#ifdef CRTSCTS
    settings.c_cflag &= ~CRTSCTS;
#endif
    settings.c_cc[VMIN] = 0;
    settings.c_cc[VTIME] = 0;
    if (tcsetattr(fd, TCSANOW, &settings) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

}  // namespace

std::uint16_t crc16CcittFalse(const std::uint8_t* data, std::size_t length)
{
    std::uint16_t crc = 0xFFFF;
    for (std::size_t index = 0; index < length; ++index) {
        crc ^= static_cast<std::uint16_t>(data[index]) << 8U;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000U) ? static_cast<std::uint16_t>((crc << 1U) ^ 0x1021U)
                                  : static_cast<std::uint16_t>(crc << 1U);
        }
    }
    return crc;
}

bool cobsDecode(const std::uint8_t* encoded, std::size_t length,
                std::vector<std::uint8_t>& decoded)
{
    decoded.clear();
    if (length == 0) {
        return false;
    }
    std::size_t input = 0;
    while (input < length) {
        const std::uint8_t code = encoded[input++];
        if (code == 0 || input + code - 1 > length) {
            decoded.clear();
            return false;
        }
        for (std::uint8_t copied = 1; copied < code; ++copied) {
            decoded.push_back(encoded[input++]);
        }
        if (code != 0xFF && input < length) {
            decoded.push_back(0);
        }
    }
    return true;
}

bool parseUartFrame(const std::vector<std::uint8_t>& decoded, ParsedUartFrame& frame,
                    bool& crc_error)
{
    crc_error = false;
    if (decoded.size() < kHeaderLength + kCrcLength || decoded[0] != kSof ||
        decoded[1] != kVersion) {
        return false;
    }
    const std::uint16_t payload_length = readLe16(&decoded[5]);
    if (payload_length > kMaxPayloadLength ||
        decoded.size() != kHeaderLength + payload_length + kCrcLength) {
        return false;
    }
    const std::uint16_t expected_crc = readLe16(&decoded[kHeaderLength + payload_length]);
    if (crc16CcittFalse(decoded.data(), kHeaderLength + payload_length) != expected_crc) {
        crc_error = true;
        return false;
    }
    frame.type = decoded[2];
    frame.sequence = readLe16(&decoded[3]);
    frame.timestamp_ms = readLe32(&decoded[7]);
    frame.payload.assign(decoded.begin() + static_cast<std::ptrdiff_t>(kHeaderLength),
                         decoded.begin() + static_cast<std::ptrdiff_t>(kHeaderLength + payload_length));
    return true;
}

std::string makeBridgeUartLine(const ParsedUartFrame& frame)
{
    std::ostringstream line;
    line << "UART\t{\"type\":" << static_cast<unsigned>(frame.type)
         << ",\"seq\":" << frame.sequence << ",\"ts_ms\":" << frame.timestamp_ms
         << ",\"payload_hex\":\"";
    line << std::hex << std::setfill('0');
    for (const std::uint8_t byte : frame.payload) {
        line << std::setw(2) << static_cast<unsigned>(byte);
    }
    return line.str() + "\"}\n";
}

UartRxConfig uartRxConfigFromEnvironment()
{
    UartRxConfig config;
    if (const char* device = std::getenv("DESKMATE_UART_DEV"); device != nullptr && *device != '\0') {
        config.device = device;
    }
    if (const char* baud_value = std::getenv("DESKMATE_UART_BAUD"); baud_value != nullptr) {
        char* end = nullptr;
        const long parsed = std::strtol(baud_value, &end, 10);
        if (end != baud_value && *end == '\0' && parsed > 0 && parsed <= 460800) {
            config.baud = static_cast<int>(parsed);
        }
    }
    return config;
}

UartReceiver::UartReceiver(UartRxConfig config, LineSink sink)
    : config_(std::move(config)), sink_(std::move(sink))
{
}

UartReceiver::~UartReceiver()
{
    stop();
}

void UartReceiver::start()
{
    if (!running_.exchange(true)) {
        worker_ = std::thread(&UartReceiver::run, this);
    }
}

void UartReceiver::stop()
{
    running_ = false;
    if (worker_.joinable()) {
        worker_.join();
    }
}

const UartRxCounters& UartReceiver::counters() const
{
    return counters_;
}

void UartReceiver::processEncodedFrame(const std::vector<std::uint8_t>& encoded)
{
    ++counters_.received;
    std::vector<std::uint8_t> decoded;
    ParsedUartFrame frame;
    bool crc_error = false;
    if (!cobsDecode(encoded.data(), encoded.size(), decoded) ||
        !parseUartFrame(decoded, frame, crc_error)) {
        ++counters_.discarded;
        if (crc_error) {
            ++counters_.crc_errors;
        }
        return;
    }
    if (!sink_(makeBridgeUartLine(frame))) {
        ++counters_.discarded;
    }
}

void UartReceiver::run()
{
    using Clock = std::chrono::steady_clock;
    auto next_open = Clock::now();
    auto next_report = Clock::now() + std::chrono::seconds(30);
    int fd = -1;
    std::vector<std::uint8_t> encoded;
    bool oversize = false;

    while (running_) {
        const auto now = Clock::now();
        if (now >= next_report) {
            std::cerr << "DESKMATE UART rx=" << counters_.received.load()
                      << " discarded=" << counters_.discarded.load()
                      << " crc_errors=" << counters_.crc_errors.load() << "\n";
            next_report = now + std::chrono::seconds(30);
        }
        if (fd < 0) {
            if (now >= next_open) {
                fd = openSerial(config_);
                if (fd < 0) {
                    std::cerr << "DESKMATE UART unavailable: " << config_.device << " (retrying in 10s)\n";
                    next_open = now + std::chrono::seconds(10);
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            continue;
        }

        pollfd watch{fd, POLLIN, 0};
        const int poll_result = poll(&watch, 1, 250);
        if (poll_result <= 0) {
            continue;
        }
        std::uint8_t bytes[128];
        const ssize_t count = read(fd, bytes, sizeof(bytes));
        if (count <= 0) {
            if (count < 0 && errno != EAGAIN && errno != EINTR) {
                close(fd);
                fd = -1;
                next_open = Clock::now() + std::chrono::seconds(10);
            }
            continue;
        }
        for (ssize_t index = 0; index < count; ++index) {
            if (bytes[index] == 0) {
                if (oversize) {
                    ++counters_.discarded;
                } else if (!encoded.empty()) {
                    processEncodedFrame(encoded);
                }
                encoded.clear();
                oversize = false;
            } else if (!oversize) {
                if (encoded.size() >= kMaxEncodedLength) {
                    encoded.clear();
                    oversize = true;
                } else {
                    encoded.push_back(bytes[index]);
                }
            }
        }
    }
    if (fd >= 0) {
        close(fd);
    }
}

}  // namespace deskmate
