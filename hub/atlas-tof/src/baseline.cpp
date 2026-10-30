#include "baseline.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>

#include "posture_net.h"

namespace tof {
namespace {

std::string esc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c == '\n' || c == '\r') o += ' ';
        else o += c;
    }
    return o;
}

// "key":<number> — from 위치부터. 없으면 false
bool findNumber(const std::string& s, const char* key, size_t from, double& v, size_t* at = nullptr) {
    const std::string k = std::string("\"") + key + "\"";
    const size_t p = s.find(k, from);
    if (p == std::string::npos) return false;
    const size_t c = s.find(':', p + k.size());
    if (c == std::string::npos) return false;
    char* end = nullptr;
    v = std::strtod(s.c_str() + c + 1, &end);
    if (end == s.c_str() + c + 1) return false;
    if (at) *at = static_cast<size_t>(end - s.c_str());
    return true;
}

bool findString(const std::string& s, const char* key, size_t from, std::string& v) {
    const std::string k = std::string("\"") + key + "\"";
    const size_t p = s.find(k, from);
    if (p == std::string::npos) return false;
    const size_t q1 = s.find('"', s.find(':', p + k.size()) + 1);
    if (q1 == std::string::npos) return false;
    const size_t q2 = s.find('"', q1 + 1);
    if (q2 == std::string::npos) return false;
    v = s.substr(q1 + 1, q2 - q1 - 1);
    return true;
}

// "key":[n,n,…] → out (cap 개까지). 개수 반환, 없으면 -1
int findArray(const std::string& s, const char* key, size_t from, std::vector<double>& out, size_t limit_end = std::string::npos) {
    const std::string k = std::string("\"") + key + "\"";
    const size_t p = s.find(k, from);
    if (p == std::string::npos || p >= limit_end) return -1;
    const size_t a = s.find('[', p), b = s.find(']', a);
    if (a == std::string::npos || b == std::string::npos) return -1;
    out.clear();
    const char* c = s.c_str() + a + 1;
    const char* stop = s.c_str() + b;
    while (c < stop) {
        char* end = nullptr;
        const double v = std::strtod(c, &end);
        if (end == c) { ++c; continue; }
        out.push_back(v);
        c = end;
    }
    return static_cast<int>(out.size());
}

}  // namespace

void BaselineAccumulator::reset(int w_, int h_) {
    w = w_; h = h_; frames = 0;
    samples.assign(static_cast<size_t>(w * h), {});
}

void BaselineAccumulator::add(const TofFrame& f) {
    if (f.w != w || f.h != h) return;
    const int n = w * h;
    for (int i = 0; i < n; ++i)
        if (zoneValid(f.d[i], f.st[i])) samples[static_cast<size_t>(i)].push_back(f.d[i]);
    ++frames;
}

bool BaselineAccumulator::finish(SensorBaseline& out, int bg_min_mm) const {
    if (frames <= 0 || w <= 0) return false;
    const int n = w * h;
    out.w = w; out.h = h; out.frames = frames; out.has = true; out.bg_zones = 0;
    std::vector<float> stds;
    for (int i = 0; i < n; ++i) {
        std::vector<int16_t> v = samples[static_cast<size_t>(i)];
        const bool valid = static_cast<int>(v.size()) * 2 >= frames && !v.empty();
        out.valid[i] = valid ? 1 : 0;
        if (!valid) { out.median_mm[i] = -1; out.std_mm[i] = 0.f; continue; }
        std::sort(v.begin(), v.end());
        out.median_mm[i] = v[v.size() / 2];
        double mean = 0.0;
        for (int16_t x : v) mean += x;
        mean /= static_cast<double>(v.size());
        double var = 0.0;
        for (int16_t x : v) var += (x - mean) * (x - mean);
        var /= static_cast<double>(v.size());
        out.std_mm[i] = static_cast<float>(std::sqrt(var));
        stds.push_back(out.std_mm[i]);
        if (out.median_mm[i] >= bg_min_mm) ++out.bg_zones;
    }
    if (!stds.empty()) { std::sort(stds.begin(), stds.end()); out.sigma_mm = stds[stds.size() / 2]; }
    return true;
}

float movedFraction(const SensorBaseline& b, const TofFrame& f, const MovedRuleParams& p, int* changed_out, int* lost_out, int* bg_out) {
    if (!b.has || f.w != b.w || f.h != b.h) return -1.f;
    const int n = b.w * b.h;
    int bg = 0, changed = 0, lost = 0;
    for (int i = 0; i < n; ++i) {
        if (!b.valid[i] || b.median_mm[i] < p.bg_min_mm) continue;
        ++bg;
        if (!zoneValid(f.d[i], f.st[i])) { ++lost; continue; }       // 보이던 벽이 안 보임 — 사람이 앉으면 흔해서 세지 않는다
        const float thr = std::max(static_cast<float>(p.moved_min_mm), p.moved_sigma * b.std_mm[i]);
        const int diff = f.d[i] - b.median_mm[i];
        if (std::fabs(static_cast<float>(diff)) <= thr) continue;
        if (diff < 0 && f.d[i] <= p.person_max_mm) continue;          // 사람(또는 손·책)이 가린 것 — 장치 이동이 아니다
        ++changed;
    }
    if (changed_out) *changed_out = changed;
    if (lost_out) *lost_out = lost;
    if (bg_out) *bg_out = bg;
    const int evaluated = bg - lost;
    if (evaluated < std::max(1, p.min_eval_zones)) return -1.f;
    return static_cast<float>(changed) / static_cast<float>(evaluated);
}

std::string baselineJson(const Baseline& b, bool with_grids) {
    std::ostringstream o;
    o << "{\"schema\":\"deskmate/tof_baseline/1\",\"captured\":" << (b.any() ? "true" : "false")
      << ",\"captured_epoch_s\":";
    { char buf[32]; std::snprintf(buf, sizeof buf, "%.3f", b.captured_epoch_s); o << buf; }
    o << ",\"source\":\"" << esc(b.source) << "\",\"sensors\":[";
    bool first = true;
    for (const SensorBaseline& s : b.sensors) {
        if (!s.has) continue;
        o << (first ? "" : ",") << "{\"id\":" << s.id << ",\"w\":" << s.w << ",\"h\":" << s.h << ",\"frames\":" << s.frames
          << ",\"sigma_mm\":" << s.sigma_mm << ",\"bg_zones\":" << s.bg_zones;
        if (with_grids) {
            const int n = s.w * s.h;
            o << ",\"median_mm\":[";
            for (int i = 0; i < n; ++i) o << (i ? "," : "") << s.median_mm[i];
            o << "],\"std_mm\":[";
            for (int i = 0; i < n; ++i) { char buf[16]; std::snprintf(buf, sizeof buf, "%.1f", s.std_mm[i]); o << (i ? "," : "") << buf; }
            o << "],\"valid\":[";
            for (int i = 0; i < n; ++i) o << (i ? "," : "") << static_cast<int>(s.valid[i]);
            o << "]";
        }
        o << "}";
        first = false;
    }
    o << "]}";
    return o.str();
}

bool saveBaseline(const std::string& path, const Baseline& b, std::string* err) {
    const std::string tmp = path + ".tmp";
    {
        std::ofstream f(tmp, std::ios::trunc);
        if (!f) { if (err) *err = "cannot write " + tmp; return false; }
        f << baselineJson(b, true) << "\n";
        if (!f) { if (err) *err = "write failed " + tmp; return false; }
    }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) { if (err) *err = "rename failed " + path; std::remove(tmp.c_str()); return false; }
    return true;
}

bool loadBaseline(const std::string& path, Baseline& out, std::string* err) {
    std::ifstream f(path);
    if (!f) { if (err) *err = "cannot open " + path; return false; }
    std::stringstream ss;
    ss << f.rdbuf();
    const std::string s = ss.str();
    std::string schema;
    if (!findString(s, "schema", 0, schema) || schema != "deskmate/tof_baseline/1") { if (err) *err = "bad schema"; return false; }
    out = Baseline{};
    double v = 0.0;
    if (findNumber(s, "captured_epoch_s", 0, v)) out.captured_epoch_s = v;
    findString(s, "source", 0, out.source);
    if (out.source.empty()) out.source = "file";
    size_t pos = s.find("\"sensors\"");
    if (pos == std::string::npos) { if (err) *err = "no sensors"; return false; }
    while (true) {
        pos = s.find("{\"id\"", pos);
        if (pos == std::string::npos) break;
        const size_t end = s.find("]}", pos);                       // 마지막 배열(valid) 끝
        SensorBaseline sb;
        double id = 0, w = 0, h = 0, fr = 0, sg = 0, bz = 0;
        if (!findNumber(s, "id", pos, id) || !findNumber(s, "w", pos, w) || !findNumber(s, "h", pos, h)) break;
        findNumber(s, "frames", pos, fr); findNumber(s, "sigma_mm", pos, sg); findNumber(s, "bg_zones", pos, bz);
        sb.id = static_cast<int>(id); sb.w = static_cast<int>(w); sb.h = static_cast<int>(h);
        sb.frames = static_cast<int>(fr); sb.sigma_mm = static_cast<float>(sg); sb.bg_zones = static_cast<int>(bz);
        const int n = sb.w * sb.h;
        std::vector<double> med, sd, va;
        if (n <= 0 || n > kMaxZones || findArray(s, "median_mm", pos, med) != n || findArray(s, "std_mm", pos, sd) != n ||
            findArray(s, "valid", pos, va) != n) {
            if (err) *err = "bad sensor block";
            return false;
        }
        for (int i = 0; i < n; ++i) {
            sb.median_mm[i] = static_cast<int16_t>(med[static_cast<size_t>(i)]);
            sb.std_mm[i] = static_cast<float>(sd[static_cast<size_t>(i)]);
            sb.valid[i] = va[static_cast<size_t>(i)] != 0 ? 1 : 0;
        }
        sb.has = true;
        out.sensors.push_back(sb);
        if (end == std::string::npos) break;
        pos = end + 2;
    }
    if (out.sensors.empty()) { if (err) *err = "no sensor baselines"; return false; }
    return true;
}

}  // namespace tof
