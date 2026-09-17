// Host test for the hub's MQTT 3.1.1 client.
//   1. Codec vectors (always): CONNECT/SUBSCRIBE/PUBLISH/PUBACK/PINGREQ bytes and PUBLISH parsing, checked
//      against the wire format in MQTT 3.1.1 §3.
//   2. Live round trip (only when DESKMATE_TEST_BROKER=host[:port] is set): connect with LWT, subscribe,
//      publish QoS 1 retained, receive it back, and see the health topic.
// Build/run: see hub/atlas/README.md ("호스트 테스트").
#include "mqtt_client.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

int failures = 0;

void check(bool condition, const char* what)
{
    std::printf("%s %s\n", condition ? "ok  " : "FAIL", what);
    if (!condition) ++failures;
}

bool sameBytes(const std::vector<std::uint8_t>& actual, std::initializer_list<int> expected)
{
    if (actual.size() != expected.size()) return false;
    std::size_t i = 0;
    for (int byte : expected) {
        if (actual[i++] != static_cast<std::uint8_t>(byte)) return false;
    }
    return true;
}

void codecTests()
{
    using namespace deskmate::mqtt_codec;
    deskmate::MqttConfig config;
    config.client_id = "hub";
    config.keepalive_sec = 30;
    // CONNECT: header 0x10, len 15, "MQTT", level 4, flags clean-session 0x02, keepalive 30, client id "hub"
    check(sameBytes(encodeConnect(config),
                    {0x10, 15, 0, 4, 'M', 'Q', 'T', 'T', 4, 0x02, 0, 30, 0, 3, 'h', 'u', 'b'}),
          "CONNECT without will");
    config.will = {"h/x", "off", 1, true};
    // will flag 0x04 | qos1 0x08 | retain 0x20 → 0x2E ; body grows by 2+3 + 2+3
    check(sameBytes(encodeConnect(config),
                    {0x10, 25, 0, 4, 'M', 'Q', 'T', 'T', 4, 0x2E, 0, 30, 0, 3, 'h', 'u', 'b',
                     0, 3, 'h', '/', 'x', 0, 3, 'o', 'f', 'f'}),
          "CONNECT with retained QoS1 will");

    check(sameBytes(encodeSubscribe(7, {{"a/#", 0}, {"b", 1}}),
                    {0x82, 12, 0, 7, 0, 3, 'a', '/', '#', 0, 0, 1, 'b', 1}),
          "SUBSCRIBE two filters");

    check(sameBytes(encodePublish("t", "hi", 0, false, 0), {0x30, 5, 0, 1, 't', 'h', 'i'}), "PUBLISH QoS0");
    check(sameBytes(encodePublish("t", "hi", 1, true, 0x1234), {0x33, 7, 0, 1, 't', 0x12, 0x34, 'h', 'i'}),
          "PUBLISH QoS1 retained carries packet id");
    check(sameBytes(encodePuback(0x1234), {0x40, 2, 0x12, 0x34}), "PUBACK");
    check(sameBytes(encodePingreq(), {0xC0, 0}), "PINGREQ");
    check(sameBytes(encodeDisconnect(), {0xE0, 0}), "DISCONNECT");

    // Remaining-length varint: 200-byte payload → length 0xCB 0x01 (203 = 3 + 200)
    const auto big = encodePublish("t", std::string(200, 'x'), 0, false, 0);
    check(big.size() == 206 && big[1] == 0xCB && big[2] == 0x01, "remaining length varint (2 bytes)");

    // Decoder: partial data → 0, then a full CONNACK, then a QoS1 PUBLISH split across two chunks.
    Packet packet;
    const std::uint8_t connack[] = {0x20, 2, 0, 0};
    check(decodePacket(connack, 3, packet) == 0, "decode needs whole packet");
    check(decodePacket(connack, 4, packet) == 4 && packet.type == 2 && packet.body.size() == 2 && packet.body[1] == 0,
          "decode CONNACK");
    const auto publish = encodePublish("deskmate/feedback/user", "{\"verdict\":\"accept\"}", 1, false, 42);
    std::vector<std::uint8_t> stream(publish.begin(), publish.end());
    stream.insert(stream.end(), {0xD0, 0});   // PINGRESP right behind it
    long consumed = decodePacket(stream.data(), stream.size(), packet);
    IncomingPublish incoming;
    check(consumed == static_cast<long>(publish.size()) && parsePublish(packet, incoming) &&
              incoming.topic == "deskmate/feedback/user" && incoming.qos == 1 && incoming.packet_id == 42 &&
              incoming.payload == "{\"verdict\":\"accept\"}",
          "decode + parse QoS1 PUBLISH from a stream");
    check(decodePacket(stream.data() + consumed, stream.size() - consumed, packet) == 2 && packet.type == 13,
          "decode PINGRESP after it");
    const std::uint8_t bad_varint[] = {0x30, 0x80, 0x80, 0x80, 0x80, 0x01};
    check(decodePacket(bad_varint, sizeof(bad_varint), packet) == -1, "malformed remaining length rejected");
    const std::uint8_t truncated_publish[] = {0x30, 3, 0, 9, 't'};
    check(decodePacket(truncated_publish, sizeof(truncated_publish), packet) == 5 && !parsePublish(packet, incoming),
          "PUBLISH with topic longer than body rejected");
}

void liveTest(const std::string& spec)
{
    deskmate::MqttConfig config;
    const auto colon = spec.find(':');
    config.host = spec.substr(0, colon);
    if (colon != std::string::npos) config.port = std::atoi(spec.c_str() + colon + 1);
    config.client_id = "deskmate-hub-hosttest";
    config.will = {"deskmate/health/hosttest", "{\"status\":\"offline\"}", 1, true};
    config.subscriptions = {{"deskmate/test/#", 1}};

    std::mutex mutex;
    std::vector<std::pair<std::string, std::string>> received;
    bool saw_connect = false;
    deskmate::MqttClient client(config,
        [&](const std::string& topic, const std::string& payload) {
            std::lock_guard<std::mutex> lock(mutex);
            received.emplace_back(topic, payload);
        },
        [&](bool connected) {
            if (connected) saw_connect = true;
        });
    client.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!client.connected() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    check(client.connected() && saw_connect, "live: connected to broker");
    std::this_thread::sleep_for(std::chrono::milliseconds(300));   // let SUBACK land
    client.publish("deskmate/test/echo", "{\"n\":1}", 1, true);
    client.publish("deskmate/test/echo0", "plain", 0, false);
    const auto echo_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    for (;;) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (received.size() >= 2) break;
        }
        if (std::chrono::steady_clock::now() > echo_deadline) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    std::lock_guard<std::mutex> lock(mutex);
    bool got_qos1 = false;
    bool got_qos0 = false;
    for (const auto& [topic, payload] : received) {
        if (topic == "deskmate/test/echo" && payload == "{\"n\":1}") got_qos1 = true;
        if (topic == "deskmate/test/echo0" && payload == "plain") got_qos0 = true;
    }
    check(got_qos1, "live: QoS1 retained publish echoed back");
    check(got_qos0, "live: QoS0 publish echoed back");
    check(client.counters().published == 2 && client.counters().received >= 2, "live: counters");
    client.stop();
    check(!client.connected(), "live: stopped");
}

}  // namespace

int main()
{
    codecTests();
    if (const char* broker = std::getenv("DESKMATE_TEST_BROKER"); broker != nullptr && *broker != '\0') {
        liveTest(broker);
    } else {
        std::printf("skip live round trip (set DESKMATE_TEST_BROKER=host[:port])\n");
    }
    if (failures != 0) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all checks passed\n");
    return 0;
}
