#pragma once

// Minimal MQTT 3.1.1 client for the Pi 4 hub service.
//
// Why not paho: the Atlas SDK ships no MQTT headers/dev libraries and the board's restricted Python has no
// _socket, so the native service owns the broker connection. This client covers exactly what the hub
// contract (docs/mqtt-topics.md) needs: CONNECT with LWT, SUBSCRIBE (QoS 0/1), PUBLISH QoS 0/1 with retain,
// incoming PUBLISH QoS 0/1 (PUBACK sent), keepalive PINGREQ, automatic reconnect with resubscribe.
// QoS 2 is not supported (never used by the contract). No TLS (local network).

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace deskmate {

struct MqttWill {
    std::string topic;
    std::string payload;
    int qos{1};
    bool retain{true};
};

struct MqttConfig {
    std::string host;                 // empty = MQTT disabled
    int port{1883};
    std::string client_id{"deskmate-hub"};
    int keepalive_sec{30};
    MqttWill will;                    // topic empty = no will
    std::vector<std::pair<std::string, int>> subscriptions;   // (topic filter, max qos)
    int reconnect_min_sec{1};
    int reconnect_max_sec{30};
};

struct MqttCounters {
    std::atomic<std::uint64_t> published{0};
    std::atomic<std::uint64_t> received{0};
    std::atomic<std::uint64_t> connects{0};
    std::atomic<std::uint64_t> dropped{0};   // outgoing messages discarded because the queue overflowed
};

// Byte-level codec, exposed for host tests.
namespace mqtt_codec {
std::vector<std::uint8_t> encodeConnect(const MqttConfig& config);
std::vector<std::uint8_t> encodeSubscribe(std::uint16_t packet_id,
                                          const std::vector<std::pair<std::string, int>>& filters);
std::vector<std::uint8_t> encodePublish(const std::string& topic, const std::string& payload, int qos,
                                        bool retain, std::uint16_t packet_id);
std::vector<std::uint8_t> encodePuback(std::uint16_t packet_id);
std::vector<std::uint8_t> encodePingreq();
std::vector<std::uint8_t> encodeDisconnect();

struct Packet {
    std::uint8_t type{};      // high nibble of the fixed header
    std::uint8_t flags{};     // low nibble
    std::vector<std::uint8_t> body;
};
// Returns the number of bytes consumed from `data` (0 = need more data, -1 = malformed stream).
long decodePacket(const std::uint8_t* data, std::size_t length, Packet& packet);

struct IncomingPublish {
    std::string topic;
    std::string payload;
    int qos{};
    std::uint16_t packet_id{};
};
bool parsePublish(const Packet& packet, IncomingPublish& out);
}  // namespace mqtt_codec

class MqttClient {
public:
    using MessageHandler = std::function<void(const std::string& topic, const std::string& payload)>;
    using ConnectionHandler = std::function<void(bool connected)>;

    MqttClient(MqttConfig config, MessageHandler on_message, ConnectionHandler on_connection = nullptr);
    ~MqttClient();

    void start();
    void stop();                                   // publishes nothing; the broker sends the will if configured
    bool connected() const { return connected_.load(); }
    const MqttCounters& counters() const { return counters_; }

    // Thread-safe; queued and sent by the worker. Dropped (counted) when not connected for a long time and the
    // queue is full. Returns false if the client is disabled (no host).
    bool publish(const std::string& topic, const std::string& payload, int qos = 1, bool retain = false);

private:
    struct Outgoing {
        std::string topic;
        std::string payload;
        int qos;
        bool retain;
    };

    void run();
    bool connectOnce();
    void session(int fd);
    bool sendAll(int fd, const std::vector<std::uint8_t>& data);
    void closeSocket(int& fd);

    MqttConfig config_;
    MessageHandler on_message_;
    ConnectionHandler on_connection_;
    MqttCounters counters_;
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};
    std::thread worker_;
    std::mutex queue_mutex_;
    std::condition_variable queue_cv_;
    std::deque<Outgoing> queue_;
    std::uint16_t next_packet_id_{1};
    int wake_pipe_[2]{-1, -1};
};

// Environment: DESKMATE_MQTT_HOST (empty = disabled), DESKMATE_MQTT_PORT (1883), DESKMATE_MQTT_CLIENT_ID.
MqttConfig mqttConfigFromEnvironment();

}  // namespace deskmate
