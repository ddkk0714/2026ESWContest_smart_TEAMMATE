#include "mqtt_client.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <iostream>

namespace deskmate {
namespace mqtt_codec {
namespace {

constexpr std::uint8_t kConnect = 1;
constexpr std::uint8_t kConnack = 2;
constexpr std::uint8_t kPublish = 3;
constexpr std::uint8_t kPuback = 4;
constexpr std::uint8_t kSubscribe = 8;
constexpr std::uint8_t kPingreq = 12;
constexpr std::uint8_t kDisconnect = 14;

void putU16(std::vector<std::uint8_t>& out, std::uint16_t value)
{
    out.push_back(static_cast<std::uint8_t>(value >> 8));
    out.push_back(static_cast<std::uint8_t>(value & 0xFF));
}

void putString(std::vector<std::uint8_t>& out, const std::string& value)
{
    const std::size_t length = value.size() > 0xFFFF ? 0xFFFF : value.size();
    putU16(out, static_cast<std::uint16_t>(length));
    out.insert(out.end(), value.begin(), value.begin() + static_cast<long>(length));
}

std::vector<std::uint8_t> withFixedHeader(std::uint8_t type, std::uint8_t flags,
                                          const std::vector<std::uint8_t>& body)
{
    std::vector<std::uint8_t> out;
    out.reserve(body.size() + 5);
    out.push_back(static_cast<std::uint8_t>((type << 4) | (flags & 0x0F)));
    std::size_t remaining = body.size();
    do {
        std::uint8_t byte = remaining % 128;
        remaining /= 128;
        if (remaining > 0) byte |= 0x80;
        out.push_back(byte);
    } while (remaining > 0);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

}  // namespace

std::vector<std::uint8_t> encodeConnect(const MqttConfig& config)
{
    std::vector<std::uint8_t> body;
    putString(body, "MQTT");
    body.push_back(4);  // protocol level 3.1.1
    std::uint8_t flags = 0x02;  // clean session
    const bool has_will = !config.will.topic.empty();
    if (has_will) {
        flags |= 0x04;
        flags |= static_cast<std::uint8_t>((config.will.qos & 0x03) << 3);
        if (config.will.retain) flags |= 0x20;
    }
    body.push_back(flags);
    putU16(body, static_cast<std::uint16_t>(config.keepalive_sec));
    putString(body, config.client_id);
    if (has_will) {
        putString(body, config.will.topic);
        putString(body, config.will.payload);
    }
    return withFixedHeader(kConnect, 0, body);
}

std::vector<std::uint8_t> encodeSubscribe(std::uint16_t packet_id,
                                          const std::vector<std::pair<std::string, int>>& filters)
{
    std::vector<std::uint8_t> body;
    putU16(body, packet_id);
    for (const auto& [filter, qos] : filters) {
        putString(body, filter);
        body.push_back(static_cast<std::uint8_t>(qos > 1 ? 1 : (qos < 0 ? 0 : qos)));
    }
    return withFixedHeader(kSubscribe, 0x02, body);
}

std::vector<std::uint8_t> encodePublish(const std::string& topic, const std::string& payload, int qos,
                                        bool retain, std::uint16_t packet_id)
{
    std::vector<std::uint8_t> body;
    putString(body, topic);
    if (qos > 0) putU16(body, packet_id);
    body.insert(body.end(), payload.begin(), payload.end());
    std::uint8_t flags = retain ? 0x01 : 0x00;
    flags |= static_cast<std::uint8_t>((qos > 1 ? 1 : qos) << 1);
    return withFixedHeader(kPublish, flags, body);
}

std::vector<std::uint8_t> encodePuback(std::uint16_t packet_id)
{
    std::vector<std::uint8_t> body;
    putU16(body, packet_id);
    return withFixedHeader(kPuback, 0, body);
}

std::vector<std::uint8_t> encodePingreq() { return withFixedHeader(kPingreq, 0, {}); }
std::vector<std::uint8_t> encodeDisconnect() { return withFixedHeader(kDisconnect, 0, {}); }

long decodePacket(const std::uint8_t* data, std::size_t length, Packet& packet)
{
    if (length < 2) return 0;
    std::size_t remaining = 0;
    std::size_t multiplier = 1;
    std::size_t index = 1;
    for (;;) {
        if (index >= length) return 0;
        if (index > 4) return -1;
        const std::uint8_t byte = data[index++];
        remaining += static_cast<std::size_t>(byte & 0x7F) * multiplier;
        if ((byte & 0x80) == 0) break;
        multiplier *= 128;
    }
    if (length < index + remaining) return 0;
    packet.type = static_cast<std::uint8_t>(data[0] >> 4);
    packet.flags = static_cast<std::uint8_t>(data[0] & 0x0F);
    packet.body.assign(data + index, data + index + remaining);
    return static_cast<long>(index + remaining);
}

bool parsePublish(const Packet& packet, IncomingPublish& out)
{
    if (packet.type != kPublish || packet.body.size() < 2) return false;
    const std::size_t topic_length = (static_cast<std::size_t>(packet.body[0]) << 8) | packet.body[1];
    std::size_t offset = 2;
    if (packet.body.size() < offset + topic_length) return false;
    out.topic.assign(reinterpret_cast<const char*>(packet.body.data() + offset), topic_length);
    offset += topic_length;
    out.qos = (packet.flags >> 1) & 0x03;
    if (out.qos > 0) {
        if (packet.body.size() < offset + 2) return false;
        out.packet_id = static_cast<std::uint16_t>((packet.body[offset] << 8) | packet.body[offset + 1]);
        offset += 2;
    }
    out.payload.assign(reinterpret_cast<const char*>(packet.body.data() + offset), packet.body.size() - offset);
    return true;
}

}  // namespace mqtt_codec

// ---------------------------------------------------------------------------------------------------------

namespace {

constexpr std::size_t kMaxQueued = 1000;
constexpr int kConnectTimeoutMs = 10000;

bool connectWithTimeout(int fd, const sockaddr* address, socklen_t length, int timeout_ms)
{
    const int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    int result = ::connect(fd, address, length);
    if (result != 0 && errno != EINPROGRESS) return false;
    if (result != 0) {
        pollfd waiter{fd, POLLOUT, 0};
        if (poll(&waiter, 1, timeout_ms) <= 0) return false;
        int error = 0;
        socklen_t error_length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &error_length) != 0 || error != 0) return false;
    }
    fcntl(fd, F_SETFL, flags);
    return true;
}

}  // namespace

MqttClient::MqttClient(MqttConfig config, MessageHandler on_message, ConnectionHandler on_connection)
    : config_(std::move(config)), on_message_(std::move(on_message)), on_connection_(std::move(on_connection))
{
}

MqttClient::~MqttClient() { stop(); }

void MqttClient::start()
{
    if (config_.host.empty() || running_.exchange(true)) return;
    if (pipe(wake_pipe_) != 0) {
        perror("deskmate-mqtt pipe");
        running_ = false;
        return;
    }
    fcntl(wake_pipe_[0], F_SETFL, fcntl(wake_pipe_[0], F_GETFL, 0) | O_NONBLOCK);   // drained with non-blocking reads
    worker_ = std::thread(&MqttClient::run, this);
}

void MqttClient::stop()
{
    if (!running_.exchange(false)) return;
    if (wake_pipe_[1] >= 0) {
        const char byte = 'q';
        (void)!write(wake_pipe_[1], &byte, 1);
    }
    if (worker_.joinable()) worker_.join();
    for (int& fd : wake_pipe_) closeSocket(fd);
}

bool MqttClient::publish(const std::string& topic, const std::string& payload, int qos, bool retain)
{
    if (config_.host.empty()) return false;
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        if (queue_.size() >= kMaxQueued) {
            queue_.pop_front();
            ++counters_.dropped;
        }
        queue_.push_back(Outgoing{topic, payload, qos, retain});
    }
    if (wake_pipe_[1] >= 0) {
        const char byte = 'p';
        (void)!write(wake_pipe_[1], &byte, 1);
    }
    return true;
}

void MqttClient::closeSocket(int& fd)
{
    if (fd >= 0) {
        close(fd);
        fd = -1;
    }
}

bool MqttClient::sendAll(int fd, const std::vector<std::uint8_t>& data)
{
    std::size_t offset = 0;
    while (offset < data.size()) {
        const ssize_t written = send(fd, data.data() + offset, data.size() - offset, MSG_NOSIGNAL);
        if (written < 0) {
            if (errno == EINTR) continue;
            return false;
        }
        offset += static_cast<std::size_t>(written);
    }
    return true;
}

void MqttClient::run()
{
    int backoff = config_.reconnect_min_sec;
    while (running_) {
        const auto started = std::chrono::steady_clock::now();
        if (connectOnce()) {
            const auto lasted = std::chrono::steady_clock::now() - started;
            if (lasted > std::chrono::seconds(10)) backoff = config_.reconnect_min_sec;
        }
        if (!running_) break;
        // Interruptible backoff sleep.
        pollfd waiter{wake_pipe_[0], POLLIN, 0};
        if (poll(&waiter, 1, backoff * 1000) > 0) {
            char drain[64];
            while (read(wake_pipe_[0], drain, sizeof(drain)) > 0) {
            }
        }
        backoff = backoff * 2 > config_.reconnect_max_sec ? config_.reconnect_max_sec : backoff * 2;
    }
}

bool MqttClient::connectOnce()
{
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* results = nullptr;
    const std::string port = std::to_string(config_.port);
    if (getaddrinfo(config_.host.c_str(), port.c_str(), &hints, &results) != 0 || results == nullptr) {
        std::cerr << "DESKMATE MQTT: cannot resolve " << config_.host << '\n';
        return false;
    }
    int fd = -1;
    for (addrinfo* entry = results; entry != nullptr; entry = entry->ai_next) {
        fd = socket(entry->ai_family, entry->ai_socktype, entry->ai_protocol);
        if (fd < 0) continue;
        if (connectWithTimeout(fd, entry->ai_addr, entry->ai_addrlen, kConnectTimeoutMs)) break;
        closeSocket(fd);
    }
    freeaddrinfo(results);
    if (fd < 0) {
        std::cerr << "DESKMATE MQTT: connect to " << config_.host << ':' << config_.port << " failed\n";
        return false;
    }
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    if (!sendAll(fd, mqtt_codec::encodeConnect(config_))) {
        closeSocket(fd);
        return false;
    }
    session(fd);   // waits for CONNACK, then runs until the connection drops or stop() is called
    closeSocket(fd);
    return true;
}

void MqttClient::session(int fd)
{
    using clock = std::chrono::steady_clock;
    std::vector<std::uint8_t> inbox;
    std::uint8_t buffer[4096];
    bool acknowledged = false;
    bool ping_outstanding = false;
    const auto keepalive = std::chrono::seconds(config_.keepalive_sec > 0 ? config_.keepalive_sec : 30);
    auto last_sent = clock::now();
    auto last_received = clock::now();
    const auto connack_deadline = clock::now() + std::chrono::milliseconds(kConnectTimeoutMs);

    auto nextPacketId = [this]() {
        if (next_packet_id_ == 0) next_packet_id_ = 1;   // 0 is not a valid packet identifier
        return next_packet_id_++;
    };
    auto sendPacket = [&](const std::vector<std::uint8_t>& packet) {
        if (!sendAll(fd, packet)) return false;
        last_sent = clock::now();
        return true;
    };

    while (running_) {
        pollfd fds[2] = {{fd, POLLIN, 0}, {wake_pipe_[0], POLLIN, 0}};
        const int ready = poll(fds, 2, 500);
        if (ready < 0) {
            if (errno == EINTR) continue;
            break;
        }
        const auto now = clock::now();

        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            const ssize_t count = recv(fd, buffer, sizeof(buffer), 0);
            if (count <= 0) {
                if (count < 0 && errno == EINTR) continue;
                std::cerr << "DESKMATE MQTT: connection closed by broker\n";
                break;
            }
            last_received = now;
            inbox.insert(inbox.end(), buffer, buffer + count);
            bool fatal = false;
            for (;;) {
                mqtt_codec::Packet packet;
                const long consumed = mqtt_codec::decodePacket(inbox.data(), inbox.size(), packet);
                if (consumed < 0) {
                    std::cerr << "DESKMATE MQTT: malformed packet, reconnecting\n";
                    fatal = true;
                    break;
                }
                if (consumed == 0) break;
                inbox.erase(inbox.begin(), inbox.begin() + consumed);
                switch (packet.type) {
                case 2: {  // CONNACK
                    const std::uint8_t code = packet.body.size() >= 2 ? packet.body[1] : 0xFF;
                    if (code != 0) {
                        std::cerr << "DESKMATE MQTT: broker refused connection (code " << int(code) << ")\n";
                        fatal = true;
                        break;
                    }
                    acknowledged = true;
                    ++counters_.connects;
                    if (!config_.subscriptions.empty() &&
                        !sendPacket(mqtt_codec::encodeSubscribe(nextPacketId(), config_.subscriptions))) {
                        fatal = true;
                        break;
                    }
                    connected_ = true;
                    std::cerr << "DESKMATE MQTT: connected " << config_.host << ':' << config_.port << '\n';
                    if (on_connection_) on_connection_(true);
                    break;
                }
                case 3: {  // PUBLISH
                    mqtt_codec::IncomingPublish message;
                    if (mqtt_codec::parsePublish(packet, message)) {
                        ++counters_.received;
                        if (message.qos == 1 && !sendPacket(mqtt_codec::encodePuback(message.packet_id))) {
                            fatal = true;
                            break;
                        }
                        if (on_message_) on_message_(message.topic, message.payload);
                    }
                    break;
                }
                case 13:  // PINGRESP
                    ping_outstanding = false;
                    break;
                default:  // PUBACK / SUBACK / others: nothing to track (QoS 1 without redelivery)
                    break;
                }
                if (fatal) break;
            }
            if (fatal) break;
        }

        if (fds[1].revents & POLLIN) {
            char drain[64];
            while (read(wake_pipe_[0], drain, sizeof(drain)) > 0) {
            }
        }

        if (!acknowledged) {
            if (clock::now() > connack_deadline) {
                std::cerr << "DESKMATE MQTT: no CONNACK, reconnecting\n";
                break;
            }
            continue;
        }

        // Flush the outgoing queue.
        std::deque<Outgoing> batch;
        {
            std::lock_guard<std::mutex> lock(queue_mutex_);
            batch.swap(queue_);
        }
        bool send_failed = false;
        while (!batch.empty()) {
            const Outgoing& item = batch.front();
            const std::uint16_t packet_id = item.qos > 0 ? nextPacketId() : 0;
            if (!sendPacket(mqtt_codec::encodePublish(item.topic, item.payload, item.qos, item.retain, packet_id))) {
                send_failed = true;
                break;
            }
            ++counters_.published;
            batch.pop_front();
        }
        if (send_failed) {
            std::lock_guard<std::mutex> lock(queue_mutex_);
            queue_.insert(queue_.begin(), batch.begin(), batch.end());   // keep unsent messages for the next session
            std::cerr << "DESKMATE MQTT: send failed, reconnecting\n";
            break;
        }

        // Keepalive.
        if (ping_outstanding && now - last_received > keepalive) {
            std::cerr << "DESKMATE MQTT: keepalive timeout, reconnecting\n";
            break;
        }
        if (!ping_outstanding && now - last_sent > keepalive / 2) {
            if (!sendPacket(mqtt_codec::encodePingreq())) break;
            ping_outstanding = true;
        }
    }

    if (acknowledged) {
        if (!running_) sendAll(fd, mqtt_codec::encodeDisconnect());
        connected_ = false;
        if (on_connection_) on_connection_(false);
    }
}

MqttConfig mqttConfigFromEnvironment()
{
    MqttConfig config;
    if (const char* host = std::getenv("DESKMATE_MQTT_HOST"); host != nullptr) config.host = host;
    if (const char* port = std::getenv("DESKMATE_MQTT_PORT"); port != nullptr && *port != '\0') {
        const int value = std::atoi(port);
        if (value > 0 && value < 65536) config.port = value;
    }
    if (const char* id = std::getenv("DESKMATE_MQTT_CLIENT_ID"); id != nullptr && *id != '\0') config.client_id = id;
    return config;
}

}  // namespace deskmate
