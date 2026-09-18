#include <sdbus-c++/sdbus-c++.h>

#include "mqtt_client.h"
#include "uart_rx.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <ctime>
#include <cctype>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>

namespace {

constexpr const char* kServiceName = "com.deskmate.hub1";
constexpr const char* kPython = "/restricted/python3/usr/bin/python3";
constexpr int kHttpPort = 8765;
constexpr std::size_t kMaxBodySize = 16 * 1024;
volatile sig_atomic_t g_stop_requested = 0;

struct HubProcess {
    pid_t pid = -1;
    int input_fd = -1;
    int output_fd = -1;
};

struct BridgeState {
    std::mutex mutex;
    std::condition_variable ack_ready;
    std::string latest_state;
    std::unordered_map<std::string, int> acknowledgements;
    deskmate::MqttClient* mqtt = nullptr;
};

struct NativeSensorState {
    std::mutex mutex;
    std::optional<int> co2;
    std::optional<int> lux;
    std::optional<int> motion_level;
    std::optional<int> distance_cm;
    std::optional<int> heart_bpm;
    std::optional<double> temp_c;
    std::optional<double> humidity_pct;
    std::optional<bool> present;
    std::optional<bool> heart_valid;
    std::optional<std::string> motion_state;
    unsigned long long sequence = 0;

    static int readLe16(const std::vector<unsigned char>& bytes, std::size_t offset)
    {
        return bytes[offset] | (bytes[offset + 1] << 8);
    }

    static std::vector<unsigned char> payloadBytes(const std::string& line)
    {
        constexpr const char* key = "\"payload_hex\":\"";
        const auto key_start = line.find(key);
        if (key_start == std::string::npos) return {};

        const auto payload_start = key_start + std::strlen(key);
        const auto payload_end = line.find('"', payload_start);
        if (payload_end == std::string::npos || (payload_end - payload_start) % 2 != 0) return {};

        std::vector<unsigned char> result;
        for (std::size_t offset = payload_start; offset < payload_end; offset += 2) {
            const std::string hex_byte = line.substr(offset, 2);
            char* parsed_end = nullptr;
            const long value = std::strtol(hex_byte.c_str(), &parsed_end, 16);
            if (parsed_end == hex_byte.c_str() || *parsed_end != '\0' || value < 0 || value > 255) {
                return {};
            }
            result.push_back(static_cast<unsigned char>(value));
        }
        return result;
    }

    bool consume(const std::string& line)
    {
        const bool is_environment = line.rfind("UART\t{\"type\":16,", 0) == 0;
        const bool is_mmwave = line.rfind("UART\t{\"type\":32,", 0) == 0;
        if (!is_environment && !is_mmwave) return false;

        const auto bytes = payloadBytes(line);
        if (bytes.empty()) return false;

        std::lock_guard<std::mutex> lock(mutex);
        if (is_environment) {
            if (bytes.size() < 9) return false;
            const int valid = bytes[8];
            if (valid & 1) co2 = readLe16(bytes, 0);
            if (valid & 2) {
                temp_c = static_cast<double>(static_cast<std::int16_t>(readLe16(bytes, 2))) / 10.0;
            }
            if (valid & 4) humidity_pct = static_cast<double>(readLe16(bytes, 4)) / 10.0;
            if (valid & 8) lux = readLe16(bytes, 6);
        } else {
            if (bytes.size() < 11) return false;
            present = bytes[0] != 0;
            motion_state = bytes[1] == 1 ? "still" : bytes[1] == 2 ? "active" : "none";
            motion_level = bytes[2];

            const int distance = readLe16(bytes, 3);
            distance_cm =
                distance == 0xffff ? std::optional<int>{} : std::optional<int>{distance};
            heart_valid = bytes[8] != 0;
            heart_bpm = (*heart_valid && bytes[7] != 0xff)
                ? std::optional<int>{bytes[7]}
                : std::optional<int>{};
        }
        ++sequence;
        return true;
    }

    std::string envelope()
    {
        std::lock_guard<std::mutex> lock(mutex);
        std::ostringstream output;
        output << "{\"schema_version\":\"1.0\",\"seq\":" << sequence
               << ",\"ts\":" << static_cast<long long>(std::time(nullptr))
               << ",\"data\":{\"fsm_state\":\"IDLE\",\"phase\":\"IDLE\","
                  "\"context\":\"focused\",\"c_focus\":0,\"c_fatigue\":0,"
                  "\"confidence\":0,\"gate\":\"none\",\"reasons\":[],"
                  "\"sensor_summary\":{";

        bool has_field = false;
        const auto append_number = [&output, &has_field](const char* name, const auto& value) {
            if (!value) return;
            if (has_field) output << ',';
            output << '"' << name << "\":" << *value;
            has_field = true;
        };
        append_number("co2_ppm", co2);
        append_number("temp_c", temp_c);
        append_number("humidity_pct", humidity_pct);
        append_number("lux", lux);

        if (present) {
            if (has_field) output << ',';
            output << "\"present\":" << (*present ? "true" : "false");
            has_field = true;
        }

        if (motion_state || motion_level || distance_cm || heart_bpm || heart_valid) {
            if (has_field) output << ',';
            output << "\"mmwave\":{";
            bool has_mmwave_field = false;
            if (motion_state) {
                output << "\"motion_state\":\"" << *motion_state << '"';
                has_mmwave_field = true;
            }
            const auto append_mmwave_number =
                [&output, &has_mmwave_field](const char* name, const auto& value) {
                    if (!value) return;
                    if (has_mmwave_field) output << ',';
                    output << '"' << name << "\":" << *value;
                    has_mmwave_field = true;
                };
            append_mmwave_number("motion_level", motion_level);
            append_mmwave_number("distance_cm", distance_cm);
            if (heart_valid) {
                if (has_mmwave_field) output << ',';
                output << "\"heart_valid\":" << (*heart_valid ? "true" : "false");
                has_mmwave_field = true;
            }
            append_mmwave_number("heart_bpm", heart_bpm);
            output << '}';
        }

        output << "}}}";
        return output.str();
    }
};

struct OutgoingTopic {
    const char* prefix;
    const char* topic;
    bool retain;
};

constexpr OutgoingTopic kOutgoingTopics[] = {
    {"STATE\t", "deskmate/state/phase", true},
    {"REQUEST\t", "deskmate/interaction/request", false},
    {"REPORT\t", "deskmate/session/report", true},
    {"CMD\t", "deskmate/control/cmd", false},
};
constexpr const char* kHealthTopic = "deskmate/health/hub";

struct HttpRequest {
    std::string method;
    std::string path;
    std::string body;
};

void handleSignal(int)
{
    g_stop_requested = 1;
}

std::filesystem::path executableDirectory()
{
    return std::filesystem::canonical("/proc/self/exe").parent_path();
}

std::string trim(std::string value)
{
    const auto not_space = [](unsigned char value) { return !std::isspace(value); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

void loadEnvironmentFile(const std::filesystem::path& path)
{
    std::ifstream input(path);
    if (!input) return;

    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        line = trim(line);
        if (line.empty() || line.front() == '#') continue;

        const std::size_t separator = line.find('=');
        if (separator == std::string::npos) {
            std::cerr << "DESKMATE Hub: ignoring invalid environment line\n";
            continue;
        }

        const std::string key = trim(line.substr(0, separator));
        const std::string value = trim(line.substr(separator + 1));
        if (key.empty() || setenv(key.c_str(), value.c_str(), 0) != 0) {
            std::cerr << "DESKMATE Hub: failed to load environment entry\n";
        }
    }
}

HubProcess startHub(const std::filesystem::path& service_dir)
{
    int to_child[2] = {-1, -1};
    int from_child[2] = {-1, -1};
    if (pipe(to_child) != 0 || pipe(from_child) != 0) {
        perror("deskmate-hub pipe");
        return {};
    }

    const pid_t child = fork();
    if (child < 0) {
        perror("deskmate-hub fork");
        close(to_child[0]);
        close(to_child[1]);
        close(from_child[0]);
        close(from_child[1]);
        return {};
    }

    if (child == 0) {
        close(to_child[1]);
        close(from_child[0]);
        if (dup2(to_child[0], STDIN_FILENO) < 0 ||
            dup2(from_child[1], STDOUT_FILENO) < 0) {
            perror("deskmate-hub dup2");
            _exit(126);
        }
        close(to_child[0]);
        close(from_child[1]);

        const std::string python_path =
            (service_dir / "deskmate_hub_service").string();
        setenv("PYTHONHOME", "/restricted/python3/usr", 1);
        setenv("PYTHONPATH", python_path.c_str(), 1);
        setenv("PYTHONUNBUFFERED", "1", 1);
        setenv("DESKMATE_HUB_MODE", "live", 0);
        setenv("DESKMATE_NATIVE_MQTT", "1", 1);
        if (chdir(service_dir.c_str()) != 0) {
            perror("deskmate-hub chdir");
            _exit(126);
        }

        execl(kPython, "python3", "-m", "deskmate_hub", "bridge", nullptr);
        perror("deskmate-hub python exec");
        _exit(126);
    }

    close(to_child[0]);
    close(from_child[1]);
    return HubProcess{child, to_child[1], from_child[0]};
}

bool writeAll(int fd, const std::string& data)
{
    std::size_t offset = 0;
    while (offset < data.size()) {
        const ssize_t count = write(fd, data.data() + offset, data.size() - offset);
        if (count > 0) {
            offset += static_cast<std::size_t>(count);
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        return false;
    }
    return true;
}

bool writeBridgeInput(int fd, std::mutex& input_mutex, const std::string& data)
{
    std::lock_guard<std::mutex> lock(input_mutex);
    return writeAll(fd, data);
}

void processBridgeLine(const std::string& line, BridgeState& state)
{
    if (line.rfind("STATE\t", 0) == 0) {
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.latest_state = line.substr(6);
        }
    }
    for (const OutgoingTopic& outgoing : kOutgoingTopics) {
        const std::size_t prefix_length = std::strlen(outgoing.prefix);
        if (line.rfind(outgoing.prefix, 0) != 0) continue;
        if (state.mqtt != nullptr) {
            state.mqtt->publish(outgoing.topic, line.substr(prefix_length), 1, outgoing.retain);
        }
        return;
    }
    if (line.rfind("ACK\t", 0) == 0) {
        const std::size_t separator = line.find('\t', 4);
        if (separator != std::string::npos) {
            try {
                const std::string request_id = line.substr(4, separator - 4);
                const int status = std::stoi(line.substr(separator + 1));
                {
                    std::lock_guard<std::mutex> lock(state.mutex);
                    state.acknowledgements[request_id] = status;
                }
                state.ack_ready.notify_all();
                return;
            } catch (const std::exception&) {
            }
        }
    }
    if (!line.empty()) std::cerr << "DESKMATE Hub: " << line << '\n';
}

void readBridge(int fd, BridgeState& state)
{
    std::string pending;
    char buffer[4096];
    for (;;) {
        const ssize_t count = read(fd, buffer, sizeof(buffer));
        if (count > 0) {
            pending.append(buffer, static_cast<std::size_t>(count));
            std::size_t newline = 0;
            while ((newline = pending.find('\n')) != std::string::npos) {
                std::string line = pending.substr(0, newline);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                processBridgeLine(line, state);
                pending.erase(0, newline + 1);
            }
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        break;
    }
    if (!pending.empty()) processBridgeLine(pending, state);
    close(fd);
}

bool readRequest(int client, HttpRequest& request)
{
    std::string data;
    char buffer[4096];
    std::size_t header_end = std::string::npos;
    while ((header_end = data.find("\r\n\r\n")) == std::string::npos) {
        const ssize_t count = recv(client, buffer, sizeof(buffer), 0);
        if (count <= 0) return false;
        data.append(buffer, static_cast<std::size_t>(count));
        if (data.size() > 32 * 1024) return false;
    }

    const std::string headers = data.substr(0, header_end);
    std::istringstream stream(headers);
    std::string request_line;
    if (!std::getline(stream, request_line)) return false;
    if (!request_line.empty() && request_line.back() == '\r') request_line.pop_back();
    std::string version;
    std::istringstream first_line(request_line);
    if (!(first_line >> request.method >> request.path >> version)) return false;

    std::size_t content_length = 0;
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        const std::size_t colon = line.find(':');
        if (colon == std::string::npos) continue;
        std::string name = line.substr(0, colon);
        std::transform(name.begin(), name.end(), name.begin(),
                       [](unsigned char value) { return std::tolower(value); });
        if (name == "content-length") {
            try {
                const std::string value = trim(line.substr(colon + 1));
                std::size_t consumed = 0;
                content_length = std::stoul(value, &consumed);
                if (consumed != value.size() || content_length > kMaxBodySize) return false;
            } catch (const std::exception&) {
                return false;
            }
        }
    }

    const std::size_t body_start = header_end + 4;
    while (data.size() - body_start < content_length) {
        const ssize_t count = recv(client, buffer, sizeof(buffer), 0);
        if (count <= 0) return false;
        data.append(buffer, static_cast<std::size_t>(count));
        if (data.size() > 32 * 1024 + kMaxBodySize) return false;
    }
    request.body = data.substr(body_start, content_length);
    return true;
}

const char* reasonPhrase(int status)
{
    switch (status) {
        case 200: return "OK";
        case 202: return "Accepted";
        case 400: return "Bad Request";
        case 404: return "Not Found";
        case 503: return "Service Unavailable";
        default: return "Error";
    }
}

void sendResponse(int client, int status, const std::string& body)
{
    std::ostringstream response;
    response << "HTTP/1.1 " << status << ' ' << reasonPhrase(status) << "\r\n"
             << "Content-Type: application/json; charset=utf-8\r\n"
             << "Content-Length: " << body.size() << "\r\n"
             << "Cache-Control: no-store\r\n"
             << "Connection: close\r\n\r\n"
             << body;
    writeAll(client, response.str());
}

void handleClient(int client, int bridge_input, std::mutex& input_mutex, BridgeState& state,
                  std::atomic<unsigned long long>& next_request_id)
{
    HttpRequest request;
    if (!readRequest(client, request)) {
        sendResponse(client, 400, R"({"error":"bad_request"})");
        return;
    }

    if (request.method == "GET" && request.path == "/health") {
        bool ready = false;
        const bool mqtt_connected = state.mqtt != nullptr && state.mqtt->connected();
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            ready = !state.latest_state.empty();
        }
        sendResponse(client, 200,
                     std::string("{\"status\":\"ok\",\"state_ready\":") +
                         (ready ? "true" : "false") + ",\"mqtt\":" +
                         (mqtt_connected ? "true" : "false") + "}");
        return;
    }

    if (request.method == "GET" && request.path == "/api/state") {
        std::string latest;
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            latest = state.latest_state;
        }
        sendResponse(client, latest.empty() ? 503 : 200,
                     latest.empty() ? R"({"error":"state_not_ready"})" : latest);
        return;
    }

    if (request.method == "POST" &&
        (request.path == "/api/feedback" || request.path == "/api/test-frame")) {
        if (request.body.empty() || request.body.find_first_of("\r\n\t") != std::string::npos) {
            const char* error = request.path == "/api/feedback"
                ? "invalid_feedback" : "invalid_test_frame";
            sendResponse(client, 400, std::string("{\"error\":\"") + error + "\"}");
            return;
        }
        const std::string request_id = std::to_string(next_request_id.fetch_add(1));
        const std::string command =
            "POST\t" + request_id + "\t" + request.path + "\t" + request.body + "\n";
        if (!writeBridgeInput(bridge_input, input_mutex, command)) {
            sendResponse(client, 503, R"({"error":"bridge_unavailable"})");
            return;
        }

        int status = 503;
        {
            std::unique_lock<std::mutex> lock(state.mutex);
            const bool received = state.ack_ready.wait_for(
                lock, std::chrono::seconds(2), [&] {
                    return state.acknowledgements.count(request_id) != 0;
                });
            if (received) {
                status = state.acknowledgements[request_id];
                state.acknowledgements.erase(request_id);
            }
        }
        if (status == 202) {
            sendResponse(client, 202, R"({"accepted":true})");
        } else if (status == 400) {
            const char* error = request.path == "/api/feedback"
                ? "invalid_feedback" : "invalid_test_frame";
            sendResponse(client, 400, std::string("{\"error\":\"") + error + "\"}");
        } else {
            sendResponse(client, 503, R"({"error":"bridge_unavailable"})");
        }
        return;
    }

    sendResponse(client, 404, R"({"error":"not_found"})");
}

void runHttpServer(int bridge_input, std::mutex& input_mutex, BridgeState& state,
                   std::atomic<bool>& http_failed)
{
    const int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) {
        perror("deskmate-hub socket");
        http_failed = true;
        return;
    }
    int reuse = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(kHttpPort);
    if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
        listen(server, 8) != 0) {
        perror("deskmate-hub bind/listen");
        close(server);
        http_failed = true;
        return;
    }

    std::cerr << "DESKMATE Hub HTTP adapter listening on " << kHttpPort << '\n';
    std::atomic<unsigned long long> next_request_id{1};
    while (!g_stop_requested) {
        pollfd descriptor = {server, POLLIN, 0};
        const int ready = poll(&descriptor, 1, 250);
        if (ready < 0) {
            if (errno == EINTR) continue;
            perror("deskmate-hub poll");
            http_failed = true;
            break;
        }
        if (ready == 0) continue;

        const int client = accept(server, nullptr, nullptr);
        if (client < 0) {
            if (errno == EINTR) continue;
            perror("deskmate-hub accept");
            continue;
        }
        timeval timeout = {3, 0};
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        handleClient(client, bridge_input, input_mutex, state, next_request_id);
        close(client);
    }
    close(server);
}

}  // namespace

int main()
{
    struct sigaction action = {};
    action.sa_handler = handleSignal;
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, nullptr);
    sigaction(SIGTERM, &action, nullptr);
    signal(SIGPIPE, SIG_IGN);

    try {
        const std::filesystem::path service_dir = executableDirectory();
        loadEnvironmentFile(service_dir / "hub.env");

        auto connection = sdbus::createSystemBusConnection(
            sdbus::ServiceName(kServiceName));
        connection->enterEventLoopAsync();

        HubProcess hub = startHub(service_dir);
        if (hub.pid < 0) return 1;

        BridgeState state;
        NativeSensorState native_sensors;
        std::atomic<bool> http_failed{false};
        std::mutex bridge_input_mutex;
        deskmate::UartReceiver uart(deskmate::uartRxConfigFromEnvironment(),
            [&hub, &bridge_input_mutex, &native_sensors, &state](const std::string& line) {
                const bool native_consumed = native_sensors.consume(line);
                if (native_consumed) {
                    const std::string envelope = native_sensors.envelope();
                    { std::lock_guard<std::mutex> lock(state.mutex); state.latest_state = envelope; }
                    if (state.mqtt != nullptr) state.mqtt->publish("deskmate/state/phase", envelope, 1, true);
                }
                const bool bridge_consumed = hub.input_fd >= 0 &&
                    writeBridgeInput(hub.input_fd, bridge_input_mutex, line);
                return native_consumed || bridge_consumed;
            });
        uart.start();

        deskmate::MqttConfig mqtt_config = deskmate::mqttConfigFromEnvironment();
        mqtt_config.will = {kHealthTopic, R"({"node":"hub","status":"offline"})", 1, true};
        mqtt_config.subscriptions = {{"deskmate/sensor/#", 0}, {"deskmate/feedback/user", 1},
                                     {"deskmate/control/result", 1}};
        deskmate::MqttClient mqtt(
            mqtt_config,
            [&hub, &bridge_input_mutex](const std::string& topic, const std::string& payload) {
                std::string line = "MQTT\t" + topic + '\t';
                for (const char value : payload) {
                    line += (value == '\n' || value == '\r') ? ' ' : value;
                }
                line += '\n';
                writeBridgeInput(hub.input_fd, bridge_input_mutex, line);
            },
            [&state](bool connected) {
                if (connected && state.mqtt != nullptr) {
                    state.mqtt->publish(
                        kHealthTopic,
                        "{\"ts\":" + std::to_string(static_cast<long long>(std::time(nullptr))) +
                            ",\"node\":\"hub\",\"status\":\"online\"}",
                        1, true);
                }
            });
        state.mqtt = &mqtt;
        std::thread reader(readBridge, hub.output_fd, std::ref(state));
        mqtt.start();
        std::thread http(runHttpServer, hub.input_fd, std::ref(bridge_input_mutex), std::ref(state),
                         std::ref(http_failed));

        std::cerr << "DESKMATE Hub service started (child " << hub.pid << ")\n";
        int child_status = 0;
        bool child_exited = false;
        while (!g_stop_requested && !http_failed) {
            const pid_t result = child_exited ? 0 : waitpid(hub.pid, &child_status, WNOHANG);
            if (result == hub.pid) {
                child_exited = true;
                close(hub.input_fd);
                hub.input_fd = -1;
                std::cerr << "DESKMATE Hub Python bridge unavailable; native sensor forwarding remains active\n";
            }
            if (result < 0) {
                perror("deskmate-hub waitpid");
                child_exited = true;
                child_status = 1;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }

        g_stop_requested = 1;
        uart.stop();
        if (mqtt.connected()) {
            mqtt.publish(kHealthTopic, R"({"node":"hub","status":"offline"})", 1, true);
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        mqtt.stop();
        state.mqtt = nullptr;
        if (!child_exited) {
            kill(hub.pid, SIGTERM);
            waitpid(hub.pid, &child_status, 0);
        }
        http.join();
        close(hub.input_fd);
        reader.join();
        connection->leaveEventLoop();

        if (http_failed) return 1;
        return WIFEXITED(child_status) ? WEXITSTATUS(child_status) : 1;
    } catch (const std::exception& error) {
        std::cerr << "DESKMATE Hub service failed: " << error.what() << '\n';
        return 1;
    }
}
