#include "device_posture.h"

#include <chrono>
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
    fd_ = FaceDownRule(cfg_.fd);
}

// 모델 입력 변환. 1) radial: 유효 칸(d ≥ 0, status 5·6·9·10) d × √(1 + x² + y²), x = (col+0.5)·2·tan22.5°/8 − tan22.5°(행도 같게).
//    실기 L8 은 센서 면에 수직인 거리, 시뮬(학습)은 빔 방향 거리라 가장자리가 최대 11 % 짧게 들어온다. 대칭이라 뒤집기와 무관.
// 2) fill: status 255(타깃 없음) 칸을 fill_notarget_mm·status 5 로. 시뮬엔 늘 2.3~3.8 m 뒤 벽·천장이 있어 빈 배경을 misplaced 로 읽는다.
//    3000 은 radial 로 바꾸지 않는다.
void DevicePosture::modelInput(const TofFrame& f, int16_t* d, uint8_t* st) const {
    static const float* kFactor = [] {
        static float t[kMaxZones];
        const double tan_half = std::tan(22.5 * 3.14159265358979323846 / 180.0);
        for (int r = 0; r < 8; ++r)
            for (int c = 0; c < 8; ++c) {
                const double x = (c + 0.5) * 2.0 * tan_half / 8.0 - tan_half;
                const double y = (r + 0.5) * 2.0 * tan_half / 8.0 - tan_half;
                t[r * 8 + c] = static_cast<float>(std::sqrt(1.0 + x * x + y * y));
            }
        return t;
    }();
    const int n = f.w * f.h;
    for (int i = 0; i < n; ++i) {
        d[i] = f.d[i]; st[i] = f.st[i];
        if (cfg_.radial && f.w == 8 && f.h == 8 && zoneValid(d[i], st[i])) {
            const double v = static_cast<double>(d[i]) * kFactor[i];
            d[i] = static_cast<int16_t>(v + 0.5 > 32767.0 ? 32767.0 : v + 0.5);
        }
        if (cfg_.fill_notarget_mm > 0 && st[i] == 255) { d[i] = static_cast<int16_t>(cfg_.fill_notarget_mm); st[i] = 5; }
    }
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
    // 엎드림 규칙: A(센서 0)·B(센서 1) 의 같은 ts_ms 원본 격자 쌍 (/raw 그대로, 뒤집기 전)
    if ((cfg_.facedown_rule || cfg_.seated_upright) && f.w == 8 && f.h == 8) {
        const int id = line.sensor_id >= 0 ? line.sensor_id : sensor;
        if (id == 0 || id == 1) {
            PendingRaw& me = pending_[id];
            me.has = true; me.ms = f.ms;
            for (int i = 0; i < kMaxZones; ++i) { me.d[i] = f.raw_d[i]; me.st[i] = f.raw_st[i]; }
            const PendingRaw& other = pending_[1 - id];
            if (other.has && other.ms == f.ms) {
                const PendingRaw& a = id == 0 ? me : other;
                const PendingRaw& b = id == 0 ? other : me;
                fd_.update(static_cast<double>(f.ms) / 1000.0, a.d, a.st, b.d, b.st);
            }
        }
    }
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
    if (capturing_) {
        if (s.acc.w != f.w || s.acc.h != f.h) s.acc.reset(f.w, f.h);
        s.acc.add(s.last);                                       // 모델 방향(뒤집기 뒤)으로 모은다 — 규칙도 같은 방향에서 본다
    }
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
    if (capturing_ && now_ms >= capture_end_ms_) finishCaptureLocked(now_ms);
    // 1b) 베이스라인 변화 규칙 (센서별 → 최대) — 모델 입력과 같은 방향의 s.last 로
    float moved_max = -1.f;
    for (size_t i = 0; i < slots_.size(); ++i) {
        Slot& s = slots_[i];
        const SensorBaseline* sb = baseline_.forId(slotId(i));
        s.st.has_baseline = sb != nullptr;
        s.st.baseline_bg_zones = sb ? sb->bg_zones : 0;
        s.st.moved_frac = (sb && s.st.online) ? movedFraction(*sb, s.last, cfg_.moved) : -1.f;
        if (s.st.moved_frac > moved_max) moved_max = s.st.moved_frac;
    }
    res_.moved_frac_max = moved_max;
    res_.moved_candidate = moved_max >= 0.f && moved_max >= cfg_.moved.moved_frac_min;
    if (res_.moved_candidate) {
        if (moved_since_ms_ < 0) moved_since_ms_ = now_ms;
        res_.moved_held_ms = now_ms - moved_since_ms_;
    } else {
        moved_since_ms_ = -1;
        res_.moved_held_ms = 0;
    }
    const bool was_moved = res_.device_moved;
    res_.device_moved = res_.moved_candidate && res_.moved_held_ms >= 1000LL * cfg_.moved.hold_s;
    if (res_.device_moved != was_moved)
        std::fprintf(stderr, "tofposture: device_moved %s (moved_frac %.2f, held %.1f s)\n", res_.device_moved ? "true" : "false",
                     moved_max, static_cast<double>(res_.moved_held_ms) / 1000.0);
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
        if (cfg_.radial || cfg_.fill_notarget_mm > 0) {
            int16_t din[kMaxZones]; uint8_t sin[kMaxZones];
            modelInput(s.last, din, sin);
            s.net.step(din, sin, logits);
        } else {
            s.net.step(s.last.d, s.last.st, logits);
        }
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
        res_.display_argmax = -1; res_.display_since_ms = -1; res_.model_misplaced = false; res_.gated = false;
        for (int i = 0; i < C; ++i) { res_.probs[i] = 0.f; res_.smooth[i] = 0.f; res_.display[i] = 0.f; }
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
    // misplaced 게이트: 표시값 = 평활 모델 출력, 단 모델이 misplaced 인데 규칙(And)이 거짓이거나 Never 면 misplaced 를 뺀 재정규화
    res_.model_misplaced = sa == kMisplacedIndex;
    const bool suppress = cfg_.misplaced_rule == MisplacedRule::Never ||
                          (cfg_.misplaced_rule == MisplacedRule::And && !res_.device_moved);
    int da = -1;
    if (suppress && C > kMisplacedIndex) {
        const float rest = 1.f - res_.smooth[kMisplacedIndex];
        for (int i = 0; i < C; ++i) {
            res_.display[i] = i == kMisplacedIndex ? 0.f : (rest > 1e-6f ? res_.smooth[i] / rest : (i == 0 ? 1.f : 0.f));
            if (da < 0 || res_.display[i] > res_.display[da]) da = i;
        }
        res_.gated = res_.model_misplaced;
    } else {
        for (int i = 0; i < C; ++i) res_.display[i] = res_.smooth[i];
        da = sa;
        res_.gated = false;
    }
    // 시연 규칙: 엎드림이 켜져 있으면 face_down(conf 고정), 아니면 모델 misplaced ∧ seated → upright
    res_.fd_on = false; res_.seated_applied = false;
    if (cfg_.facedown_rule && fd_.on() && C > kForwardHeadIndex - 5) {           // C ≥ 4 (face_down 인덱스 3)
        const float conf = std::min(0.999999f, std::max(0.f, cfg_.facedown_conf));
        float rest = 0.f;
        for (int i = 0; i < C; ++i) if (i != 3) rest += res_.display[i];
        for (int i = 0; i < C; ++i) res_.display[i] = i == 3 ? conf : (rest > 1e-6f ? res_.display[i] / rest * (1.f - conf) : 0.f);
        da = 3;
        res_.fd_on = true;
    } else if (cfg_.seated_upright && sa == kMisplacedIndex && C > kMisplacedIndex && fd_.pairs() > 0 && fd_.last().seated) {
        res_.display[0] += res_.display[kMisplacedIndex];
        res_.display[kMisplacedIndex] = 0.f;
        da = 0;
        for (int i = 1; i < C; ++i) if (res_.display[i] > res_.display[da]) da = i;
        res_.seated_applied = true;
    }
    if (da != res_.display_argmax) { res_.display_argmax = da; res_.display_since_ms = now_ms; }
    return true;
}

FaceDownState DevicePosture::faceDownState() const {
    std::lock_guard<std::mutex> lock(mutex_);
    FaceDownState s;
    s.enabled = cfg_.facedown_rule; s.seated_enabled = cfg_.seated_upright;
    s.on = fd_.on(); s.share = fd_.share(); s.pairs = fd_.pairs(); s.last_t_s = fd_.lastT(); s.last = fd_.last();
    s.conf = cfg_.facedown_conf;
    return s;
}

// ---- 베이스라인 ----

void DevicePosture::setBaselineFile(const std::string& path) {
    std::lock_guard<std::mutex> lock(mutex_);
    baselineFile_ = path;
}

void DevicePosture::setBaseline(const Baseline& b, int64_t now_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    baseline_ = b;
    baseline_.captured_at_ms = now_ms;
    if (baseline_.source.empty()) baseline_.source = "file";
    moved_since_ms_ = -1;
}

bool DevicePosture::startBaselineCapture(int64_t now_ms, int seconds, const std::string& source) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (capturing_) return false;
    if (seconds <= 0) seconds = cfg_.baseline_capture_s;
    capturing_ = true;
    capture_start_ms_ = now_ms;
    capture_end_ms_ = now_ms + 1000LL * seconds;
    capture_source_ = source;
    for (Slot& s : slots_) s.acc.reset(0, 0);
    std::fprintf(stderr, "tofposture: baseline capture started (%d s, %s)\n", seconds, source.c_str());
    return true;
}

bool DevicePosture::capturing() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return capturing_;
}

void DevicePosture::finishCaptureLocked(int64_t now_ms) {
    capturing_ = false;
    Baseline b;
    for (size_t i = 0; i < slots_.size(); ++i) {
        SensorBaseline sb;
        if (!slots_[i].acc.finish(sb, cfg_.moved.bg_min_mm)) continue;
        sb.id = slotId(i);
        b.sensors.push_back(sb);
    }
    if (b.sensors.empty()) {
        std::fprintf(stderr, "tofposture: baseline capture ended with no frames — baseline unchanged\n");
        return;
    }
    b.captured_at_ms = now_ms;
    b.captured_epoch_s = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    b.source = capture_source_;
    baseline_ = b;
    moved_since_ms_ = -1;
    res_.device_moved = false;
    for (const SensorBaseline& sb : b.sensors)
        std::fprintf(stderr, "tofposture: baseline sensor %d: %d frames, %d background zones (>= %d mm), sigma %.1f mm\n", sb.id, sb.frames,
                     sb.bg_zones, cfg_.moved.bg_min_mm, sb.sigma_mm);
    if (!baselineFile_.empty()) {
        std::string err;
        if (!saveBaseline(baselineFile_, baseline_, &err)) std::fprintf(stderr, "tofposture: baseline not saved: %s\n", err.c_str());
        else std::fprintf(stderr, "tofposture: baseline saved to %s\n", baselineFile_.c_str());
    }
}

void DevicePosture::clearBaseline() {
    std::lock_guard<std::mutex> lock(mutex_);
    baseline_ = Baseline{};
    capturing_ = false;
    moved_since_ms_ = -1;
    res_.device_moved = false; res_.moved_candidate = false; res_.moved_held_ms = 0; res_.moved_frac_max = -1.f;
    for (Slot& s : slots_) { s.st.has_baseline = false; s.st.baseline_bg_zones = 0; s.st.moved_frac = -1.f; }
    if (!baselineFile_.empty()) std::remove(baselineFile_.c_str());
}

Baseline DevicePosture::baseline() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return baseline_;
}

std::string DevicePosture::baselineJson(int64_t now_ms) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::string j = tof::baselineJson(baseline_, true);
    j.pop_back();                                               // '}' 를 떼고 상태를 덧붙인다
    std::ostringstream o;
    o << j << ",\"capturing\":" << (capturing_ ? "true" : "false")
      << ",\"capture_remaining_ms\":" << (capturing_ ? std::max<int64_t>(0, capture_end_ms_ - now_ms) : -1)
      << ",\"file\":\"" << jsonEscape(baselineFile_) << "\""
      << ",\"age_ms\":" << (baseline_.captured_at_ms >= 0 ? now_ms - baseline_.captured_at_ms : -1)
      << ",\"rule\":{\"mode\":\"" << misplacedRuleName(cfg_.misplaced_rule) << "\",\"bg_min_mm\":" << cfg_.moved.bg_min_mm
      << ",\"moved_min_mm\":" << cfg_.moved.moved_min_mm << ",\"moved_sigma\":" << cfg_.moved.moved_sigma
      << ",\"person_max_mm\":" << cfg_.moved.person_max_mm << ",\"moved_frac_min\":" << cfg_.moved.moved_frac_min
      << ",\"hold_s\":" << cfg_.moved.hold_s << ",\"min_eval_zones\":" << cfg_.moved.min_eval_zones << "}"
      << ",\"moved_frac\":[";
    for (size_t i = 0; i < slots_.size(); ++i) o << (i ? "," : "") << slots_[i].st.moved_frac;
    o << "],\"moved_candidate\":" << (res_.moved_candidate ? "true" : "false") << ",\"moved_held_ms\":" << res_.moved_held_ms
      << ",\"device_moved\":" << (res_.device_moved ? "true" : "false") << "}";
    return o.str();
}

void DevicePosture::resetLocked(const char* why, int64_t now_ms) {
    for (Slot& s : slots_) { s.net.reset(); ++s.st.resets; s.used_once = false; }
    res_.smooth_argmax = -1; res_.label_since_ms = -1;
    res_.display_argmax = -1; res_.display_since_ms = -1;
    fd_.reset();                                                 // 규칙 창도 비운다 (PC 기준 구현의 reset() 과 같게)
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
    const FaceDownState fds = faceDownState();
    std::string model_file, placement, baseline_source;
    int C = 0;
    bool has_baseline = false, capturing = false;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        model_file = modelPath_; placement = placement_; C = net_classes_;
        has_baseline = baseline_.any(); baseline_source = baseline_.source; capturing = capturing_;
    }
    std::ostringstream o;
    // posture·index·conf·since_ms·probs·present·misplaced 는 misplaced 게이트를 거친 표시값(Off 면 평활 모델 출력과 같다).
    // 모델 원 출력은 instant(이번 스텝)·model_output(평활)에 그대로 남긴다.
    o << "{\"schema\":\"deskmate/tof_posture/1\",\"ts_ms\":" << now_ms
      << ",\"valid\":" << (r.valid ? "true" : "false")
      << ",\"posture\":\"" << labelName(r.display_argmax) << "\""
      << ",\"posture_ko\":\"" << labelKo(r.display_argmax) << "\""
      << ",\"index\":" << r.display_argmax
      << ",\"conf\":" << (r.display_argmax >= 0 ? r.display[r.display_argmax] : 0.f)
      << ",\"since_ms\":" << (r.display_since_ms >= 0 ? now_ms - r.display_since_ms : -1)
      << ",\"instant\":{\"posture\":\"" << labelName(r.argmax) << "\",\"conf\":" << r.conf
      << ",\"probs\":" << probsJson(r.probs, C) << "}"
      << ",\"probs\":" << probsJson(r.display, C)
      << ",\"model_output\":{\"posture\":\"" << labelName(r.smooth_argmax) << "\",\"conf\":"
      << (r.smooth_argmax >= 0 ? r.smooth[r.smooth_argmax] : 0.f)
      << ",\"since_ms\":" << (r.label_since_ms >= 0 ? now_ms - r.label_since_ms : -1)
      << ",\"probs\":" << probsJson(r.smooth, C) << "}"
      << ",\"present\":" << ((r.valid && r.display_argmax >= 0 && r.display_argmax != kAwayIndex) ? "true" : "false")
      << ",\"misplaced\":" << ((r.valid && r.display_argmax == kMisplacedIndex) ? "true" : "false")
      << ",\"misplaced_rule\":{\"mode\":\"" << misplacedRuleName(cfg_.misplaced_rule) << "\""
      << ",\"baseline\":" << (has_baseline ? "true" : "false") << ",\"baseline_source\":\"" << jsonEscape(baseline_source) << "\""
      << ",\"capturing\":" << (capturing ? "true" : "false")
      << ",\"moved_frac\":[";
    for (size_t i = 0; i < ss.size(); ++i) o << (i ? "," : "") << ss[i].moved_frac;
    o << "],\"moved_candidate\":" << (r.moved_candidate ? "true" : "false") << ",\"moved_held_ms\":" << r.moved_held_ms
      << ",\"device_moved\":" << (r.device_moved ? "true" : "false")
      << ",\"model_misplaced\":" << (r.model_misplaced ? "true" : "false") << ",\"gated\":" << (r.gated ? "true" : "false") << "}"
      << ",\"radial\":" << (cfg_.radial ? "true" : "false") << ",\"fill_notarget_mm\":" << cfg_.fill_notarget_mm
      << ",\"rule\":{\"enabled\":" << (fds.enabled ? "true" : "false") << ",\"face_down\":" << (fds.on ? "true" : "false")
      << ",\"applied\":" << (r.fd_on ? "true" : "false") << ",\"conf\":" << fds.conf << ",\"share\":" << fds.share
      << ",\"flag\":" << (fds.last.flag ? "true" : "false") << ",\"up\":" << fds.last.up << ",\"lo\":" << fds.last.lo
      << ",\"topA\":" << fds.last.topA << ",\"topB\":" << fds.last.topB
      << ",\"dlo\":" << (fds.last.has_dlo ? fds.last.dlo : -1.0) << ",\"dup\":" << (fds.last.has_dup ? fds.last.dup : -1.0)
      << ",\"seated\":" << (fds.last.seated ? "true" : "false") << ",\"seated_upright\":" << (fds.seated_enabled ? "true" : "false")
      << ",\"seated_applied\":" << (r.seated_applied ? "true" : "false") << ",\"pairs\":" << fds.pairs
      << ",\"last_pair_ms\":" << (fds.last_t_s >= 0 ? static_cast<long long>(fds.last_t_s * 1000.0 + 0.5) : -1LL) << "}"
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
          << ",\"has_baseline\":" << (s.has_baseline ? "true" : "false") << ",\"moved_frac\":" << s.moved_frac
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
      << ",\"misplaced_rule\":\"" << misplacedRuleName(cfg_.misplaced_rule) << "\",\"device_moved\":" << (r.device_moved ? "true" : "false")
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
