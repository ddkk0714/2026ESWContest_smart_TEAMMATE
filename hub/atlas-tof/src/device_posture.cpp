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

// "resets":{...} — /api/state 와 /health 에 같은 모양으로
std::string resetsJson(const ResetStats& r, int64_t now_ms, const DeviceConfig& cfg) {
    std::ostringstream o;
    o << "\"resets\":{\"periodic\":" << r.periodic << ",\"away\":" << r.away << ",\"manual\":" << r.manual
      << ",\"model_switch\":" << r.model_switch
      << ",\"last_reset_ms\":" << r.last_reset_ms
      << ",\"since_reset_ms\":" << (r.last_reset_ms >= 0 ? now_ms - r.last_reset_ms : -1)
      << ",\"period_s\":" << cfg.reset_period_s << ",\"away_s\":" << cfg.away_reset_s
      << ",\"present_min_mm\":" << cfg.present_min_mm << ",\"present_max_mm\":" << cfg.present_max_mm
      << ",\"present_min_coverage\":" << cfg.present_min_coverage << "}";
    return o.str();
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
    net_H_ = probe.H; net_W_ = probe.W; net_hidden_ = probe.hidden;
    for (Slot& s : slots_) {
        if (!s.net.load(path, err)) return false;
    }
    return true;
}

bool DevicePosture::switchModel(const std::string& path, int64_t now_ms, std::string* err) {
    PostureNet probe;
    if (!probe.load(path, err)) return false;                 // 못 읽음 → 기존 유지
    std::lock_guard<std::mutex> lock(mutex_);
    if (net_classes_ > 0 && (probe.H != net_H_ || probe.W != net_W_ || probe.classes != net_classes_ || probe.hidden != net_hidden_)) {
        if (err) {
            char buf[160];
            std::snprintf(buf, sizeof buf, "model shape mismatch: have %dx%d c%d h%d, file %dx%d c%d h%d",
                          net_H_, net_W_, net_classes_, net_hidden_, probe.H, probe.W, probe.classes, probe.hidden);
            *err = buf;
        }
        return false;
    }
    // 슬롯마다 다시 읽는다(PostureNet 은 가중치 안을 가리키는 포인터를 들고 있어 복사하지 않는다). 하나라도 실패하면 이전
    // 파일로 되돌린다 — probe 가 방금 읽은 파일이라 실패는 I/O 가 사라진 경우뿐.
    for (size_t i = 0; i < slots_.size(); ++i) {
        std::string e;
        if (!slots_[i].net.load(path, &e)) {
            for (size_t k = 0; k <= i && k < slots_.size(); ++k) slots_[k].net.load(modelPath_);
            if (err) *err = "reload failed on sensor " + std::to_string(i) + ": " + e + " (kept " + modelPath_ + ")";
            return false;
        }
    }
    modelPath_ = path;
    net_classes_ = probe.classes;
    net_H_ = probe.H; net_W_ = probe.W; net_hidden_ = probe.hidden;
    ++res_.resets.model_switch;
    resetLocked("model_switch", now_ms < 0 ? res_.last_tick_ms : now_ms);
    return true;
}

void DevicePosture::setPlacement(const std::string& key) {
    std::lock_guard<std::mutex> lock(mutex_);
    placement_ = key;
}

std::string DevicePosture::modelPath() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return modelPath_;
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
    if (line.sensor_id >= 0) s.st.sensor_id = line.sensor_id;
    s.raw = f;
    ++frames_total_;
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
    // 재실 coverage (리셋 규칙용. 뒤집기와 무관하게 개수만 센다)
    {
        int n = 0;
        const int total = f.w * f.h;
        for (int i = 0; i < total; ++i)
            if (zoneValid(s.last.d[i], s.last.st[i]) && s.last.d[i] >= cfg_.present_min_mm && s.last.d[i] <= cfg_.present_max_mm) ++n;
        s.st.coverage = total > 0 ? static_cast<float>(n) / static_cast<float>(total) : 0.f;
    }
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
    // 1) 센서 online 판정 + 재실 coverage
    float cov_max = 0.f;
    for (Slot& s : slots_) {
        const bool was_online = s.st.online;
        s.st.online = s.has_frame && s.net.loaded() && (now_ms - s.last_rx_ms) <= cfg_.stale_ms;
        if (!s.st.online) {
            if (was_online) { s.net.reset(); ++s.st.resets; s.used_once = false; }   // 끊김 → 돌아오면 새로 시작
            s.st.argmax = -1; s.st.conf = 0.f;
            continue;
        }
        ++online;
        if (s.st.coverage > cov_max) cov_max = s.st.coverage;
    }
    res_.coverage_max = cov_max;
    res_.present_rule = online > 0 && cov_max >= cfg_.present_min_coverage;
    // 2) 리셋 규칙 (추론 전). 시작 시각은 첫 tick
    if (res_.resets.last_reset_ms < 0) res_.resets.last_reset_ms = now_ms;
    if (cfg_.reset_period_s > 0 && now_ms - res_.resets.last_reset_ms >= 1000LL * cfg_.reset_period_s) {
        ++res_.resets.periodic;
        resetLocked("periodic", now_ms);
    }
    if (cfg_.away_reset_s > 0 && online > 0) {
        if (res_.present_rule) {
            absent_since_ms_ = -1;                                  // 재실 한 프레임이면 타이머를 지운다
        } else if (absent_since_ms_ < 0) {
            absent_since_ms_ = now_ms;
        } else if (now_ms - absent_since_ms_ >= 1000LL * cfg_.away_reset_s) {
            ++res_.resets.away;
            resetLocked("away", now_ms);
            absent_since_ms_ = now_ms;                              // 비어 있는 동안 away_reset_s 마다 반복
        }
    } else {
        absent_since_ms_ = -1;      // 센서가 전부 offline 이면 상태는 이미 비어 있다(아래 online==0 분기) — 세지 않는다
    }
    // 3) 추론
    for (Slot& s : slots_) {
        if (!s.st.online) continue;
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

void DevicePosture::resetLocked(const char* why, int64_t now_ms) {
    for (Slot& s : slots_) { s.net.reset(); ++s.st.resets; s.used_once = false; }
    res_.smooth_argmax = -1; res_.label_since_ms = -1;
    const int64_t since = res_.resets.last_reset_ms >= 0 ? now_ms - res_.resets.last_reset_ms : -1;
    res_.resets.last_reset_ms = now_ms;
    std::fprintf(stderr, "tofposture: reset (%s) after %.1f s, coverage %.3f, sensors %zu\n", why,
                 since >= 0 ? static_cast<double>(since) / 1000.0 : -1.0, res_.coverage_max, slots_.size());
}

void DevicePosture::reset(int64_t now_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (now_ms < 0) now_ms = res_.last_tick_ms;
    ++res_.resets.manual;
    resetLocked("manual", now_ms);
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
    std::string model_file, placement;
    int C = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        model_file = modelPath_; placement = placement_; C = net_classes_;
    }
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
      << ",\"online_sensors\":" << r.online_sensors << ",\"ticks\":" << r.ticks;
    if (!placement.empty()) o << ",\"placement\":\"" << jsonEscape(placement) << "\"";
    o << ",\"present_rule\":" << (r.present_rule ? "true" : "false") << ",\"coverage\":[";
    for (size_t i = 0; i < ss.size(); ++i) o << (i ? "," : "") << (ss[i].online ? ss[i].coverage : 0.f);
    o << "]," << resetsJson(r.resets, now_ms, cfg_)
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
          << ",\"stale\":" << (s.stale ? "true" : "false") << ",\"coverage\":" << s.coverage
          << ",\"rule\":\"" << jsonEscape(s.rule) << "\",\"hello\":\"" << jsonEscape(s.hello)
          << "\",\"info\":\"" << jsonEscape(s.info) << "\",\"error\":\"" << jsonEscape(s.error) << "\"}";
    }
    o << "],\"model\":{\"file\":\"" << jsonEscape(model_file) << "\",\"classes\":" << C
      << ",\"rate_hz\":" << cfg_.rate_hz << ",\"fusion\":\"" << fusionName(cfg_.fusion) << "\""
      << ",\"flip_rows\":" << (cfg_.flip_rows < 0 ? "\"auto\"" : cfg_.flip_rows ? "true" : "false")
      << ",\"flip_cols\":" << (cfg_.flip_cols < 0 ? "\"auto\"" : cfg_.flip_cols ? "true" : "false")
      << ",\"labels\":[";
    for (int i = 0; i < C; ++i) o << (i ? "," : "") << "\"" << kLabels[i] << "\"";
    o << "]}}";
    return o.str();
}

void DevicePosture::setDeviceLabel(const std::string& dev) {
    std::lock_guard<std::mutex> lock(mutex_);
    devLabel_ = dev;
}

// uart_relay_board.cpp rawJson 과 같은 모양. 센서 2개 자리(id 0·1)를 항상 낸다 — 앱 파서(tof_raw_client.dart)는
// have·id·w·h·dist_mm·status·age_ms 만 읽는다. frames/type5/sent 는 받은 유효 프레임 수, bad 는 0(파서가 버린 줄은 세지 않음).
std::string DevicePosture::rawJson(int64_t now_ms) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::string j = "{\"dev\":\"" + jsonEscape(devLabel_) + "\",\"frames\":" + std::to_string(frames_total_) +
                    ",\"type5\":" + std::to_string(frames_total_) + ",\"sent\":" + std::to_string(frames_total_) +
                    ",\"bad\":0,\"sensors\":[";
    int ids = 2;
    for (const Slot& s : slots_) if (s.st.sensor_id + 1 > ids) ids = s.st.sensor_id + 1;
    if (static_cast<int>(slots_.size()) > ids) ids = static_cast<int>(slots_.size());
    for (int id = 0; id < ids; ++id) {
        const Slot* found = nullptr;
        for (const Slot& s : slots_) if (s.st.sensor_id == id && s.has_frame) { found = &s; break; }
        if (!found && id < static_cast<int>(slots_.size())) {         // 센서 번호가 없는 형식(USB 포트·파일)은 자리 번호 = id
            const Slot& s = slots_[static_cast<size_t>(id)];
            if (s.st.sensor_id < 0 && s.has_frame) found = &s;
        }
        if (id) j += ",";
        j += "{\"id\":" + std::to_string(id) + ",\"have\":" + (found ? "true" : "false");
        if (found) {
            const TofFrame& f = found->raw;
            j += ",\"age_ms\":" + std::to_string(now_ms - found->last_rx_ms) + ",\"uart_seq\":" + std::to_string(f.uart_seq) +
                 ",\"ts_ms\":" + std::to_string(f.ms) + ",\"frame_id\":" + std::to_string(f.frame_id) +
                 ",\"frame_seq\":" + std::to_string(f.seq) + ",\"mask\":" + std::to_string(f.sensor_mask) +
                 ",\"flags\":" + std::to_string(f.raw_flags) + ",\"flip_rows\":" + ((f.raw_flags & 1) ? "true" : "false") +
                 ",\"flip_cols\":" + ((f.raw_flags & 2) ? "true" : "false") +
                 ",\"w\":" + std::to_string(f.w) + ",\"h\":" + std::to_string(f.h) + ",\"dist_mm\":[";
            const int z = f.w * f.h;
            for (int i = 0; i < z; ++i) { if (i) j += ","; j += std::to_string(f.raw_d[i]); }
            j += "],\"status\":[";
            for (int i = 0; i < z; ++i) { if (i) j += ","; j += std::to_string(f.raw_st[i]); }
            j += "]";
        }
        j += "}";
    }
    j += "]}";
    return j;
}

std::string DevicePosture::healthJson(int64_t now_ms) const {
    const DeviceResult r = result();
    const std::vector<SensorStatus> ss = sensors(now_ms);
    std::ostringstream o;
    o << "{\"ok\":" << (net_classes_ > 0 ? "true" : "false") << ",\"model_loaded\":" << (net_classes_ > 0 ? "true" : "false")
      << ",\"rate_hz\":" << cfg_.rate_hz << ",\"ticks\":" << r.ticks << ",\"online_sensors\":" << r.online_sensors
      << ",\"present_rule\":" << (r.present_rule ? "true" : "false") << "," << resetsJson(r.resets, now_ms, cfg_)
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
