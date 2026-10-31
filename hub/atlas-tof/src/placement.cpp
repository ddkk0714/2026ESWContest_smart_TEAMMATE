#include "placement.h"

#include <climits>
#include <cstdio>
#include <cstdlib>
#include <fstream>

#include "device_posture.h"

namespace tof {
namespace {

std::string trim(const std::string& s) {
    const size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}

bool validKey(const std::string& k) { return k == "left" || k == "center" || k == "right"; }

std::string jsonEsc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c == '\n' || c == '\r') o += ' ';
        else o += c;
    }
    return o;
}

// 두 경로가 같은 파일인가: 문자열이 같거나, 둘 다 있을 때 realpath 가 같다
bool samePath(const std::string& a, const std::string& b) {
    if (a == b) return true;
    char ra[PATH_MAX], rb[PATH_MAX];
    if (::realpath(a.c_str(), ra) == nullptr || ::realpath(b.c_str(), rb) == nullptr) return false;
    return std::string(ra) == std::string(rb);
}

}  // namespace

const std::string* PlacementConfig::modelFor(const std::string& key) const {
    for (const auto& kv : models) if (kv.first == key) return &kv.second;
    return nullptr;
}

std::string PlacementConfig::keyForModel(const std::string& path) const {
    for (const auto& kv : models) if (samePath(kv.second, path)) return kv.first;
    return "unknown";
}

bool parsePlacementModels(const std::string& spec, const std::string& base_dir, PlacementConfig& out, std::string* err) {
    out.models.clear();
    if (trim(spec).empty()) return true;
    size_t start = 0;
    while (start <= spec.size()) {
        size_t end = spec.find(',', start);
        if (end == std::string::npos) end = spec.size();
        const std::string item = trim(spec.substr(start, end - start));
        start = end + 1;
        if (item.empty()) continue;
        const size_t eq = item.find('=');
        if (eq == std::string::npos) { if (err) *err = "expected key=path: " + item; out.models.clear(); return false; }
        const std::string key = trim(item.substr(0, eq));
        std::string path = trim(item.substr(eq + 1));
        if (!validKey(key)) { if (err) *err = "unknown placement key: " + key; out.models.clear(); return false; }
        if (path.empty()) { if (err) *err = "empty model path for " + key; out.models.clear(); return false; }
        if (out.modelFor(key) != nullptr) { if (err) *err = "duplicate placement key: " + key; out.models.clear(); return false; }
        if (path[0] != '/' && !base_dir.empty()) path = base_dir + "/" + path;
        out.models.emplace_back(key, path);
    }
    return true;
}

std::string readPlacementFile(const std::string& path) {
    std::ifstream f(path);
    if (!f) return "";
    std::string line;
    std::getline(f, line);
    return trim(line);
}

bool writePlacementFile(const std::string& path, const std::string& key, std::string* err) {
    const std::string tmp = path + ".tmp";
    {
        std::ofstream f(tmp, std::ios::trunc);
        if (!f) { if (err) *err = "cannot write " + tmp; return false; }
        f << key << "\n";
        if (!f) { if (err) *err = "write failed " + tmp; return false; }
    }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) { if (err) *err = "rename failed " + path; std::remove(tmp.c_str()); return false; }
    return true;
}

std::string parsePlacementBody(const std::string& body) {
    const size_t k = body.find("\"placement\"");
    if (k == std::string::npos) return "";
    const size_t colon = body.find(':', k + 11);
    if (colon == std::string::npos) return "";
    const size_t q1 = body.find('"', colon + 1);
    if (q1 == std::string::npos) return "";
    const size_t q2 = body.find('"', q1 + 1);
    if (q2 == std::string::npos) return "";
    return trim(body.substr(q1 + 1, q2 - q1 - 1));
}

Placement::Placement(PlacementConfig cfg, DevicePosture& device) : cfg_(std::move(cfg)), device_(device) {}

std::string Placement::initialModelPath(const std::string& fallback_model) {
    std::lock_guard<std::mutex> lock(mutex_);
    const std::string key = readPlacementFile(cfg_.file);
    if (const std::string* m = cfg_.modelFor(key)) {
        current_ = key;
        model_ = *m;
    } else {
        current_ = cfg_.keyForModel(fallback_model);
        model_ = fallback_model;
    }
    return model_;
}

std::string Placement::current() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return current_;
}

std::string Placement::json(int64_t now_ms) const {
    std::lock_guard<std::mutex> lock(mutex_);
    (void)now_ms;
    std::string j = "{\"placement\":\"" + jsonEsc(current_) + "\",\"model\":\"" + jsonEsc(model_) + "\",\"options\":[";
    for (size_t i = 0; i < cfg_.models.size(); ++i) j += (i ? ",\"" : "\"") + cfg_.models[i].first + "\"";
    j += "],\"models\":{";
    for (size_t i = 0; i < cfg_.models.size(); ++i)
        j += (i ? ",\"" : "\"") + cfg_.models[i].first + "\":\"" + jsonEsc(cfg_.models[i].second) + "\"";
    j += "},\"changed_ms\":" + std::to_string(changed_ms_) + ",\"switches\":" + std::to_string(switches_) +
         ",\"file\":\"" + jsonEsc(cfg_.file) + "\"}";
    return j;
}

int Placement::apply(const std::string& body, int64_t now_ms, std::string& response) {
    const std::string key = parsePlacementBody(body);
    const std::string* model = validKey(key) ? cfg_.modelFor(key) : nullptr;
    if (model == nullptr) {
        response = "{\"error\":\"placement must be one of";
        for (const auto& kv : cfg_.models) response += " " + kv.first;
        response += "\",\"got\":\"" + jsonEsc(key) + "\"}";
        return 400;
    }
    std::string err;
    if (!device_.switchModel(*model, now_ms, &err)) {              // 못 읽거나 모양이 다르면 기존 모델 그대로
        response = "{\"error\":\"" + jsonEsc(err) + "\",\"placement\":\"" + jsonEsc(current()) + "\"}";
        return 500;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        current_ = key;
        model_ = *model;
        changed_ms_ = now_ms;
        ++switches_;
    }
    device_.setPlacement(key);
    std::string werr;
    if (!writePlacementFile(cfg_.file, key, &werr))
        std::fprintf(stderr, "tofposture: placement %s applied but not saved (%s)\n", key.c_str(), werr.c_str());
    else
        std::fprintf(stderr, "tofposture: placement %s -> %s (saved to %s)\n", key.c_str(), model->c_str(), cfg_.file.c_str());
    response = json(now_ms);
    return 200;
}

}  // namespace tof
