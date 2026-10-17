#include "http_server.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>

namespace tof {
namespace {

void sendAll(int fd, const std::string& data) {
    size_t off = 0;
    while (off < data.size()) {
        const ssize_t n = ::send(fd, data.data() + off, data.size() - off, MSG_NOSIGNAL);
        if (n <= 0) return;
        off += static_cast<size_t>(n);
    }
}

std::string response(int code, const char* reason, const std::string& body, const char* type = "application/json") {
    return "HTTP/1.0 " + std::to_string(code) + " " + reason + "\r\nContent-Type: " + type +
           "\r\nContent-Length: " + std::to_string(body.size()) +
           "\r\nAccess-Control-Allow-Origin: *\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" + body;
}

}  // namespace

HttpServer::HttpServer(std::string bind_addr, int port, HttpHandlers handlers)
    : bind_addr_(std::move(bind_addr)), port_(port), handlers_(std::move(handlers)) {}

HttpServer::~HttpServer() { stop(); }

bool HttpServer::start(std::string* err) {
    listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd_ < 0) { if (err) *err = std::strerror(errno); return false; }
    int one = 1;
    ::setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(port_));
    if (::inet_pton(AF_INET, bind_addr_.c_str(), &addr.sin_addr) != 1) addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || ::listen(listen_fd_, 8) != 0) {
        if (err) *err = std::strerror(errno);
        ::close(listen_fd_); listen_fd_ = -1;
        return false;
    }
    running_ = true;
    worker_ = std::thread(&HttpServer::run, this);
    return true;
}

void HttpServer::stop() {
    running_ = false;
    if (worker_.joinable()) worker_.join();
    if (listen_fd_ >= 0) { ::close(listen_fd_); listen_fd_ = -1; }
}

void HttpServer::run() {
    while (running_) {
        pollfd pfd{listen_fd_, POLLIN, 0};
        const int ready = ::poll(&pfd, 1, 200);
        if (ready <= 0) continue;
        const int client = ::accept(listen_fd_, nullptr, nullptr);
        if (client < 0) continue;
        timeval tv{2, 0};
        ::setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        ::setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
        handle(client);
        ::close(client);
    }
}

void HttpServer::handle(int client) {
    char buf[2048];
    std::string req;
    while (req.find("\r\n\r\n") == std::string::npos && req.size() < 16384) {
        const ssize_t n = ::recv(client, buf, sizeof buf, 0);
        if (n <= 0) break;
        req.append(buf, static_cast<size_t>(n));
    }
    ++requests_;
    const size_t sp1 = req.find(' ');
    const size_t sp2 = sp1 == std::string::npos ? sp1 : req.find(' ', sp1 + 1);
    if (sp1 == std::string::npos || sp2 == std::string::npos) {
        sendAll(client, response(400, "Bad Request", "{\"error\":\"bad request\"}"));
        return;
    }
    const std::string method = req.substr(0, sp1);
    std::string path = req.substr(sp1 + 1, sp2 - sp1 - 1);
    const size_t q = path.find('?');
    if (q != std::string::npos) path.erase(q);

    if (method == "GET" && path == "/health") {
        sendAll(client, response(200, "OK", handlers_.health ? handlers_.health() : "{}"));
    } else if (method == "GET" && path == "/api/state") {
        sendAll(client, response(200, "OK", handlers_.state ? handlers_.state() : "{}"));
    } else if ((method == "POST" || method == "GET") && path == "/api/reset") {
        if (handlers_.reset) handlers_.reset();
        sendAll(client, response(200, "OK", "{\"ok\":true}"));
    } else if (method == "GET" && path == "/") {
        sendAll(client, response(200, "OK", "deskmate tof posture: GET /health, GET /api/state, POST /api/reset\n", "text/plain"));
    } else {
        sendAll(client, response(404, "Not Found", "{\"error\":\"not found\"}"));
    }
}

}  // namespace tof
