#include <sdbus-c++/sdbus-c++.h>

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
#include <cctype>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <mutex>
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
};

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

void processBridgeLine(const std::string& line, BridgeState& state)
{
    if (line.rfind("STATE\t", 0) == 0) {
        std::lock_guard<std::mutex> lock(state.mutex);
        state.latest_state = line.substr(6);
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

std::string trim(std::string value)
{
    const auto not_space = [](unsigned char value) { return !std::isspace(value); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
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

void handleClient(int client, int bridge_input, BridgeState& state,
                  std::atomic<unsigned long long>& next_request_id)
{
    HttpRequest request;
    if (!readRequest(client, request)) {
        sendResponse(client, 400, R"({"error":"bad_request"})");
        return;
    }

    if (request.method == "GET" && request.path == "/health") {
        bool ready = false;
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            ready = !state.latest_state.empty();
        }
        sendResponse(client, 200, ready
            ? R"({"status":"ok","state_ready":true})"
            : R"({"status":"ok","state_ready":false})");
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
        if (!writeAll(bridge_input, command)) {
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

void runHttpServer(int bridge_input, BridgeState& state,
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
        handleClient(client, bridge_input, state, next_request_id);
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
        auto connection = sdbus::createSystemBusConnection(
            sdbus::ServiceName(kServiceName));
        connection->enterEventLoopAsync();

        HubProcess hub = startHub(executableDirectory());
        if (hub.pid < 0) return 1;

        BridgeState state;
        std::atomic<bool> http_failed{false};
        std::thread reader(readBridge, hub.output_fd, std::ref(state));
        std::thread http(runHttpServer, hub.input_fd, std::ref(state),
                         std::ref(http_failed));

        std::cerr << "DESKMATE Hub service started (child " << hub.pid << ")\n";
        int child_status = 0;
        bool child_exited = false;
        while (!g_stop_requested && !http_failed) {
            const pid_t result = waitpid(hub.pid, &child_status, WNOHANG);
            if (result == hub.pid) {
                child_exited = true;
                std::cerr << "DESKMATE Hub child exited\n";
                break;
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
        if (!child_exited) {
            kill(hub.pid, SIGTERM);
            waitpid(hub.pid, &child_status, 0);
        }
        close(hub.input_fd);
        http.join();
        reader.join();
        connection->leaveEventLoop();

        if (http_failed) return 1;
        return WIFEXITED(child_status) ? WEXITSTATUS(child_status) : 1;
    } catch (const std::exception& error) {
        std::cerr << "DESKMATE Hub service failed: " << error.what() << '\n';
        return 1;
    }
}
