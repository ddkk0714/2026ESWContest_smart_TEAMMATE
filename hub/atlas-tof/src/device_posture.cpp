#include "device_posture.h"

#include <cmath>
#include <cstdio>
#include <sstream>

namespace tof {
namespace {

std::string jsonEscape(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c == '\n' || c == '\r') o += ' ';
        else o += c;
    }
    return o;
}

std::string probsJson(const float* p, int n) {
    std::string o = "{";
    char buf[48];
    for (int i = 0; i < n; ++i) {
        std::snprintf(buf, sizeof buf, "%s\"%s\":%.3f", i ? "," : "", kLabels[i], p[i]);
        o += buf;
    }
    return o + "}";
}

}  // namespace

DevicePosture::DevicePosture(DeviceConfig cfg) : cfg_(cfg) {
    if (cfg_.rate_hz <= 0) cfg_.rate_hz = kModelRateHz;
}

bool DevicePosture::loadModel(const std::string& path, std::string* err) {
    PostureNet probe;
    if (!probe.load(path, err)) return false;
    std::lock_guard<std::mutex> lock(mutex_);
    modelPath_ = path;
    net_classes_ = probe.classes;
    for (Slot& s : slots_) {
        if (!s.net.load(path, err)) return false;
    }
    return true;
}

int DevicePosture::addSensor(const std::string& name) {
    std::lock_guard<std::mutex> lock(mutex_);
    slots_.emplace_back();
    Slot& s = slots_.back();
    s.st.name = name;
    if (!modelPath_.empty()) s.net.load(modelPath_);
    return static_cast<int>(slots_.size()) - 1;
}

void DevicePosture::renameSensor(int sensor, const std::string& name) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (sensor >= 0 && sensor < static_cast<int>(slots_.size())) slots_[static_cast<size_t>(sensor)].st.name = name;
}

void DevicePosture::feed(int sensor, const TofLine& line, int64_t now_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (sensor < 0 || sensor >= static_cast<int>(slots_.size())) return;
    Slot& s = slots_[static_cast<size_t>(sensor)];
    if (line.kind == LineKind::Hello) {
        char buf[160];
        std::snprintf(buf, sizeof buf, "%s %dx%d %dHz flip_rows=%d flip_cols=%d", line.hello.sensor.c_str(),
                      line.hello.w, line.hello.h, line.hello.hz, line.hello.flip_rows, line.hello.flip_cols);
        s.st.hello = buf;
        // hello = 센서가 (다시) 시작됐다. 상태를 비운다.
        s.net.reset(); ++s.st.resets; s.used_once = false;
        return;
    }
    if (line.kind == LineKind::Error) {
        s.st.error = line.err_where + (line.err_code ? " code " + std::to_string(line.err_code) : "");
        return;
    }
    if (line.kind == LineKind::Rule) { s.st.rule = line.rule_posture; return; }
    if (line.kind == LineKind::Info) { s.st.info = line.info; return; }
    if (line.kind != LineKind::Frame && line.kind != LineKind::Idle) return;
    const TofFrame& f = line.frame;
    const bool idle = line.kind == LineKind::Idle;
    if (!idle) {
        if (!line.rule_posture.empty()) s.st.rule = line.rule_posture;
        s.st.has_status = f.has_status;
        if (f.w * f.h != s.net.H * s.net.W) return;          // 모델 격자와 다른 프레임은 버린다
    }
    // 순서 판단. UART 0x05 는 센서마다 +1 씩 도는 u16 frame_seq: delta=(new-old)&0xFFFF 가 0 이면 중복이라 버리고,
    // 1 정상, 2..10 짧은 유실(진행), 그 밖(되감김 포함)은 그 센서 GRU 리셋. 0xFFFF→0 순환은 delta 1 이라 정상.
    // valid=0 프레임도 frame_seq 를 갱신한다 — 센서가 잠깐 조용했다가 돌아올 때 delta 1 로 이어지게.
    // (valid=0 이 stale_ms 넘게 이어지면 tick 이 그 센서를 판정에서 빼고 GRU 를 리셋한다.)
    // 다른 형식(tof_logger seq, bring-up ms)은 값이 줄면 ESP32 재부팅으로 보고 리셋.
    bool rewind = false;
    if (f.seq16) {
        if (s.seq16_known) {
            const uint32_t delta = (f.seq - s.last_seq16) & f.seq_mask;
            if (delta == 0) { ++s.st.duplicates; return; }
            if (delta > 10) rewind = true;
            else if (delta > 1) ++s.st.seq_gaps;
        }
        s.seq16_known = true;
        s.last_seq16 = f.seq;
    } else if (s.has_frame && f.seq < s.last_seq) {
        rewind = true;
    }
    if (rewind) {
        s.net.reset(); ++s.st.resets; s.used_once = false;
    }
    s.last_alive_ms = now_ms;
    if (idle) { ++s.st.idle_frames; return; }
    s.last = f;
    // 장착 방향 보정: row 0 = 위, col 0 = 센서 기준 왼쪽으로. 설정(TOFPOSTURE_FLIP_*)이 있으면 그것, 없으면 프레임의 b2/b3.
    const bool fr = cfg_.flip_rows >= 0 ? cfg_.flip_rows != 0 : f.flip_rows_hint > 0;
    const bool fc = cfg_.flip_cols >= 0 ? cfg_.flip_cols != 0 : f.flip_cols_hint > 0;
    s.st.flip_rows = fr;
    s.st.flip_cols = fc;
    if (fr || fc) {
        for (int r = 0; r < f.h; ++r)
            for (int c = 0; c < f.w; ++c) {
                const int sr = fr ? f.h - 1 - r : r, sc = fc ? f.w - 1 - c : c;
                s.last.d[r * f.w + c] = f.d[sr * f.w + sc];
                s.last.st[r * f.w + c] = f.st[sr * f.w + sc];
            }
    }
    s.has_frame = true;
    s.last_seq = f.seq;
    s.last_rx_ms = now_ms;
    if (s.first_rx_ms < 0) s.first_rx_ms = now_ms;
    ++s.st.frames;
    s.st.ever = true;
    // 수신율: 2초 창
    if (s.hz_window_start < 0) { s.hz_window_start = now_ms; s.hz_window_frames = 0; }
    ++s.hz_window_frames;
    if (now_ms - s.hz_window_start >= 2000) {
        s.st.hz = 1000.0 * static_cast<double>(s.hz_window_frames) / static_cast<double>(now_ms - s.hz_window_start);
        s.hz_window_start = now_ms;
        s.hz_window_frames = 0;
    }
}

bool DevicePosture::tick(int64_t now_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    const int64_t period = 1000 / cfg_.rate_hz;
    if (res_.last_tick_ms >= 0 && now_ms - res_.last_tick_ms < period) return false;
    // 주기가 밀렸어도 한 번만 돌린다(밀린 만큼 몰아 돌리면 GRU 가 시간을 잘못 센다).
    res_.last_tick_ms = (res_.last_tick_ms < 0 || now_ms - res_.last_tick_ms > 2 * period) ? now_ms
                                                                                           : res_.last_tick_ms + period;
    const int C = net_classes_;
    float sum[kMaxClasses] = {};
    int online = 0;
    float logits[kMaxClasses];
    for (Slot& s : slots_) {
        const bool was_online = s.st.online;
        s.st.online = s.has_frame && s.net.loaded() && (now_ms - s.last_rx_ms) <= cfg_.stale_ms;
        if (!s.st.online) {
            if (was_online) { s.net.reset(); ++s.st.resets; s.used_once = false; }   // 끊김 → 돌아오면 새로 시작
            s.st.argmax = -1; s.st.conf = 0.f;
            continue;
        }
        if (s.used_once && s.last.seq == s.last_used_seq) ++s.st.reused;
        s.net.step(s.last.d, s.last.st, logits);
        s.last_used_seq = s.last.seq; s.used_once = true;
        ++s.st.steps;
        softmax(logits, C, s.st.probs);
        int a = 0;
        for (int i = 1; i < C; ++i) if (s.st.probs[i] > s.st.probs[a]) a = i;
        s.st.argmax = a; s.st.conf = s.st.probs[a];
        for (int i = 0; i < C; ++i)
            sum[i] += cfg_.fusion == FusionRule::LogProb ? std::log(s.st.probs[i] + 1e-6f) : s.st.probs[i];
        ++online;
    }
    ++res_.ticks;
    res_.online_sensors = online;
    if (online == 0) {
        res_.valid = false; res_.argmax = -1; res_.conf = 0.f;
        res_.smooth_argmax = -1; res_.label_since_ms = -1;
        for (int i = 0; i < C; ++i) { res_.probs[i] = 0.f; res_.smooth[i] = 0.f; }
        return true;
    }
    res_.valid = true;
    if (cfg_.fusion == FusionRule::LogProb) {
        for (int i = 0; i < C; ++i) sum[i] /= static_cast<float>(online);   // 평균 log-prob → softmax 로 정규화
        softmax(sum, C, res_.probs);
    } else {
        for (int i = 0; i < C; ++i) res_.probs[i] = sum[i] / static_cast<float>(online);
    }
    int a = 0;
    for (int i = 1; i < C; ++i) if (res_.probs[i] > res_.probs[a]) a = i;
    res_.argmax = a; res_.conf = res_.probs[a];
    // EMA
    const float alpha = cfg_.smooth_seconds > 0.f ? 1.f / (cfg_.smooth_seconds * static_cast<float>(cfg_.rate_hz)) : 1.f;
    int sa = 0;
    for (int i = 0; i < C; ++i) {
        res_.smooth[i] = res_.smooth_argmax < 0 ? res_.probs[i] : res_.smooth[i] + alpha * (res_.probs[i] - res_.smooth[i]);
        if (res_.smooth[i] > res_.smooth[sa]) sa = i;
    }
    if (sa != res_.smooth_argmax) { res_.smooth_argmax = sa; res_.label_since_ms = now_ms; }
    return true;
}

void DevicePosture::reset() {
    std::lock_guard<std::mutex> lock(mutex_);
    for (Slot& s : slots_) { s.net.reset(); ++s.st.resets; s.used_once = false; }
    res_.smooth_argmax = -1; res_.label_since_ms = -1;
}

DeviceResult DevicePosture::result() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return res_;
}

std::vector<SensorStatus> DevicePosture::sensors(int64_t now_ms) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<SensorStatus> out;
    for (const Slot& s : slots_) {
        SensorStatus st = s.st;
        st.age_ms = s.last_rx_ms < 0 ? -1 : now_ms - s.last_rx_ms;
        st.alive = s.last_alive_ms >= 0 && now_ms - s.last_alive_ms <= cfg_.stale_ms;
        st.stale = st.alive && (s.last_rx_ms < 0 || now_ms - s.last_rx_ms > cfg_.stale_ms);
        out.push_back(st);
    }
    return out;
}

std::string DevicePosture::stateJson(int64_t now_ms) const {
    const DeviceResult r = result();
    const std::vector<SensorStatus> ss = sensors(now_ms);
    const int C = net_classes_;
    std::ostringstream o;
    o << "{\"schema\":\"deskmate/tof_posture/1\",\"ts_ms\":" << now_ms
      << ",\"valid\":" << (r.valid ? "true" : "false")
      << ",\"posture\":\"" << labelName(r.smooth_argmax) << "\""
      << ",\"posture_ko\":\"" << labelKo(r.smooth_argmax) << "\""
      << ",\"index\":" << r.smooth_argmax
      << ",\"conf\":" << (r.smooth_argmax >= 0 ? r.smooth[r.smooth_argmax] : 0.f)
      << ",\"since_ms\":" << (r.label_since_ms >= 0 ? now_ms - r.label_since_ms : -1)
      << ",\"instant\":{\"posture\":\"" << labelName(r.argmax) << "\",\"conf\":" << r.conf
      << ",\"probs\":" << probsJson(r.probs, C) << "}"
      << ",\"probs\":" << probsJson(r.smooth, C)
      << ",\"present\":" << ((r.valid && r.smooth_argmax >= 0 && r.smooth_argmax != kAwayIndex) ? "true" : "false")
      << ",\"misplaced\":" << ((r.valid && r.smooth_argmax == kMisplacedIndex) ? "true" : "false")
      << ",\"online_sensors\":" << r.online_sensors << ",\"ticks\":" << r.ticks
      << ",\"sensors\":[";
    for (size_t i = 0; i < ss.size(); ++i) {
        const SensorStatus& s = ss[i];
        o << (i ? "," : "") << "{\"name\":\"" << jsonEscape(s.name) << "\",\"online\":" << (s.online ? "true" : "false")
          << ",\"posture\":\"" << labelName(s.argmax) << "\",\"conf\":" << s.conf
          << ",\"probs\":" << probsJson(s.probs, C)
          << ",\"frames\":" << s.frames << ",\"steps\":" << s.steps << ",\"reused\":" << s.reused
          << ",\"resets\":" << s.resets << ",\"age_ms\":" << s.age_ms << ",\"hz\":" << s.hz
          << ",\"has_status\":" << (s.has_status ? "true" : "false")
          << ",\"flip_rows\":" << (s.flip_rows ? "true" : "false") << ",\"flip_cols\":" << (s.flip_cols ? "true" : "false")
          << ",\"seq_gaps\":" << s.seq_gaps << ",\"duplicates\":" << s.duplicates
          << ",\"idle_frames\":" << s.idle_frames << ",\"alive\":" << (s.alive ? "true" : "false")
          << ",\"stale\":" << (s.stale ? "true" : "false")
          << ",\"rule\":\"" << jsonEscape(s.rule) << "\",\"hello\":\"" << jsonEscape(s.hello)
          << "\",\"info\":\"" << jsonEscape(s.info) << "\",\"error\":\"" << jsonEscape(s.error) << "\"}";
    }
    o << "],\"model\":{\"file\":\"" << jsonEscape(modelPath_) << "\",\"classes\":" << C
      << ",\"rate_hz\":" << cfg_.rate_hz << ",\"fusion\":\"" << fusionName(cfg_.fusion) << "\""
      << ",\"flip_rows\":" << (cfg_.flip_rows < 0 ? "\"auto\"" : cfg_.flip_rows ? "true" : "false")
      << ",\"flip_cols\":" << (cfg_.flip_cols < 0 ? "\"auto\"" : cfg_.flip_cols ? "true" : "false")
      << ",\"labels\":[";
    for (int i = 0; i < C; ++i) o << (i ? "," : "") << "\"" << kLabels[i] << "\"";
    o << "]}}";
    return o.str();
}

std::string DevicePosture::healthJson(int64_t now_ms) const {
    const DeviceResult r = result();
    const std::vector<SensorStatus> ss = sensors(now_ms);
    std::ostringstream o;
    o << "{\"ok\":" << (net_classes_ > 0 ? "true" : "false") << ",\"model_loaded\":" << (net_classes_ > 0 ? "true" : "false")
      << ",\"rate_hz\":" << cfg_.rate_hz << ",\"ticks\":" << r.ticks << ",\"online_sensors\":" << r.online_sensors
      << ",\"sensors\":[";
    for (size_t i = 0; i < ss.size(); ++i) {
        const SensorStatus& s = ss[i];
        o << (i ? "," : "") << "{\"name\":\"" << jsonEscape(s.name) << "\",\"online\":" << (s.online ? "true" : "false")
          << ",\"frames\":" << s.frames << ",\"hz\":" << s.hz << ",\"age_ms\":" << s.age_ms
          << ",\"stale\":" << (s.stale ? "true" : "false")
          << ",\"error\":\"" << jsonEscape(s.error) << "\"}";
    }
    o << "]}";
    return o.str();
}

}  // namespace tof
