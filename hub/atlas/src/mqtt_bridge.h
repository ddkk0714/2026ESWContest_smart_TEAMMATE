#pragma once

#include <functional>
#include <memory>
#include <string>

namespace deskmate {

struct MqttBridgeConfig {
    std::string host;
    int port = 1883;
    std::string client_id = "deskmate-hub";

    bool enabled() const { return !host.empty(); }
};

MqttBridgeConfig mqttBridgeConfigFromEnvironment();

class MqttBridge {
public:
    using LineSink = std::function<bool(const std::string&)>;

    MqttBridge(MqttBridgeConfig config, LineSink sink);
    ~MqttBridge();
    MqttBridge(const MqttBridge&) = delete;
    MqttBridge& operator=(const MqttBridge&) = delete;

    bool start();
    void stop();
    bool publish(const std::string& topic, const std::string& payload, int qos, bool retained);
    bool connected() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace deskmate