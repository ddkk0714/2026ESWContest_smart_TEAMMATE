#include "mqtt_bridge.h"

#include <MQTTAsync.h>

#include <dlfcn.h>

#include <atomic>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <utility>

namespace deskmate {
namespace {

constexpr const char* kSensorTopic = "deskmate/sensor/#";
constexpr const char* kFeedbackTopic = "deskmate/feedback/user";
constexpr const char* kHealthTopic = "deskmate/health/hub";

int environmentPort(const char* value, int fallback)
{
    if (value == nullptr) return fallback;
    char* end = nullptr;
    const long parsed = std::strtol(value, &end, 10);
    return end != value && *end == '\0' && parsed > 0 && parsed <= 65535
        ? static_cast<int>(parsed) : fallback;
}

}  // namespace

MqttBridgeConfig mqttBridgeConfigFromEnvironment()
{
    MqttBridgeConfig config;
    if (const char* host = std::getenv("DESKMATE_MQTT_HOST")) config.host = host;
    config.port = environmentPort(std::getenv("DESKMATE_MQTT_PORT"), config.port);
    if (const char* client = std::getenv("DESKMATE_MQTT_CLIENT_ID"); client && *client) {
        config.client_id = client;
    }
    return config;
}

class MqttBridge::Impl {
public:
    Impl(MqttBridgeConfig config, LineSink sink)
        : config_(std::move(config)), sink_(std::move(sink)) {}

    ~Impl() { stop(); }

    bool start()
    {
        if (!config_.enabled()) return false;
        if (!loadLibrary()) return false;

        const std::string uri = "tcp://" + config_.host + ":" + std::to_string(config_.port);
        int rc = api_.create(&client_, uri.c_str(), config_.client_id.c_str(),
                             MQTTCLIENT_PERSISTENCE_NONE, nullptr);
        if (rc != MQTTASYNC_SUCCESS) return fail("create", rc);
        rc = api_.set_callbacks(client_, this, &Impl::connectionLost,
                                &Impl::messageArrived, nullptr);
        if (rc != MQTTASYNC_SUCCESS) return fail("set callbacks", rc);
        if (api_.set_connected) api_.set_connected(client_, this, &Impl::connectedCallback);

        will_payload_ = R"({"node":"hub","status":"offline"})";
        MQTTAsync_willOptions will = MQTTAsync_willOptions_initializer;
        will.topicName = kHealthTopic;
        will.message = will_payload_.c_str();
        will.qos = 1;
        will.retained = 1;

        MQTTAsync_connectOptions options = MQTTAsync_connectOptions_initializer;
        options.keepAliveInterval = 30;
        options.cleansession = 1;
        options.automaticReconnect = 1;
        options.minRetryInterval = 1;
        options.maxRetryInterval = 30;
        options.will = &will;
        options.context = this;
        options.onSuccess = &Impl::connectSuccess;
        options.onFailure = &Impl::connectFailure;
        rc = api_.connect(client_, &options);
        if (rc != MQTTASYNC_SUCCESS) return fail("connect", rc);
        started_ = true;
        std::cerr << "DESKMATE MQTT connecting to " << uri << '\n';
        return true;
    }

    void stop()
    {
        if (!library_) return;
        if (client_) {
            if (connected_) publish(kHealthTopic, R"({"node":"hub","status":"offline"})", 1, true);
            MQTTAsync_disconnectOptions options = MQTTAsync_disconnectOptions_initializer;
            options.timeout = 1000;
            api_.disconnect(client_, &options);
            api_.destroy(&client_);
        }
        connected_ = false;
        started_ = false;
        dlclose(library_);
        library_ = nullptr;
    }

    bool publish(const std::string& topic, const std::string& payload, int qos, bool retained)
    {
        if (!client_ || !connected_) return false;
        MQTTAsync_message message = MQTTAsync_message_initializer;
        message.payload = const_cast<char*>(payload.data());
        message.payloadlen = static_cast<int>(payload.size());
        message.qos = qos;
        message.retained = retained ? 1 : 0;
        const int rc = api_.send_message(client_, topic.c_str(), &message, nullptr);
        if (rc != MQTTASYNC_SUCCESS) {
            std::cerr << "DESKMATE MQTT publish failed: " << rc << '\n';
            return false;
        }
        return true;
    }

    bool connected() const { return connected_; }

private:
    struct Api {
        decltype(&MQTTAsync_create) create = nullptr;
        decltype(&MQTTAsync_setCallbacks) set_callbacks = nullptr;
        decltype(&MQTTAsync_setConnected) set_connected = nullptr;
        decltype(&MQTTAsync_connect) connect = nullptr;
        decltype(&MQTTAsync_disconnect) disconnect = nullptr;
        decltype(&MQTTAsync_destroy) destroy = nullptr;
        decltype(&MQTTAsync_subscribe) subscribe = nullptr;
        decltype(&MQTTAsync_sendMessage) send_message = nullptr;
        decltype(&MQTTAsync_freeMessage) free_message = nullptr;
        decltype(&MQTTAsync_free) free_memory = nullptr;
    } api_;

    template <typename Function>
    bool load(Function& target, const char* name, bool required = true)
    {
        target = reinterpret_cast<Function>(dlsym(library_, name));
        if (!target && required) std::cerr << "DESKMATE MQTT missing symbol: " << name << '\n';
        return target != nullptr || !required;
    }

    bool loadLibrary()
    {
        for (const char* name : {"libpaho-mqtt3a.so.1", "libpaho-mqtt3a.so.1.3.13"}) {
            library_ = dlopen(name, RTLD_NOW | RTLD_LOCAL);
            if (library_) break;
        }
        if (!library_) {
            std::cerr << "DESKMATE MQTT library unavailable: " << dlerror() << '\n';
            return false;
        }
        return load(api_.create, "MQTTAsync_create") &&
               load(api_.set_callbacks, "MQTTAsync_setCallbacks") &&
               load(api_.set_connected, "MQTTAsync_setConnected", false) &&
               load(api_.connect, "MQTTAsync_connect") &&
               load(api_.disconnect, "MQTTAsync_disconnect") &&
               load(api_.destroy, "MQTTAsync_destroy") &&
               load(api_.subscribe, "MQTTAsync_subscribe") &&
               load(api_.send_message, "MQTTAsync_sendMessage") &&
               load(api_.free_message, "MQTTAsync_freeMessage") &&
               load(api_.free_memory, "MQTTAsync_free");
    }

    bool fail(const char* operation, int rc)
    {
        std::cerr << "DESKMATE MQTT " << operation << " failed: " << rc << '\n';
        stop();
        return false;
    }

    void onConnected()
    {
        connected_ = true;
        api_.subscribe(client_, kSensorTopic, 0, nullptr);
        api_.subscribe(client_, kFeedbackTopic, 1, nullptr);
        publish(kHealthTopic, R"({"node":"hub","status":"online"})", 1, true);
        std::cerr << "DESKMATE MQTT connected\n";
    }

    static void connectSuccess(void* context, MQTTAsync_successData*)
    {
        static_cast<Impl*>(context)->onConnected();
    }

    static void connectFailure(void* context, MQTTAsync_failureData* response)
    {
        auto* self = static_cast<Impl*>(context);
        self->connected_ = false;
        std::cerr << "DESKMATE MQTT connect failed: " << (response ? response->code : -1) << '\n';
    }

    static void connectedCallback(void* context, char*)
    {
        static_cast<Impl*>(context)->onConnected();
    }

    static void connectionLost(void* context, char*)
    {
        static_cast<Impl*>(context)->connected_ = false;
        std::cerr << "DESKMATE MQTT disconnected; automatic reconnect enabled\n";
    }

    static int messageArrived(void* context, char* topic_name, int topic_length,
                              MQTTAsync_message* message)
    {
        auto* self = static_cast<Impl*>(context);
        const std::string topic(topic_name, topic_length > 0
            ? static_cast<std::size_t>(topic_length) : std::strlen(topic_name));
        const std::string payload(static_cast<const char*>(message->payload),
                                  static_cast<std::size_t>(message->payloadlen));
        self->sink_("MQTT\t" + topic + "\t" + payload + "\n");
        self->api_.free_message(&message);
        self->api_.free_memory(topic_name);
        return 1;
    }

    MqttBridgeConfig config_;
    LineSink sink_;
    void* library_ = nullptr;
    MQTTAsync client_ = nullptr;
    std::atomic<bool> connected_{false};
    bool started_ = false;
    std::string will_payload_;
};

MqttBridge::MqttBridge(MqttBridgeConfig config, LineSink sink)
    : impl_(std::make_unique<Impl>(std::move(config), std::move(sink))) {}
MqttBridge::~MqttBridge() = default;
bool MqttBridge::start() { return impl_->start(); }
void MqttBridge::stop() { impl_->stop(); }
bool MqttBridge::publish(const std::string& topic, const std::string& payload, int qos, bool retained)
{ return impl_->publish(topic, payload, qos, retained); }
bool MqttBridge::connected() const { return impl_->connected(); }

}  // namespace deskmate