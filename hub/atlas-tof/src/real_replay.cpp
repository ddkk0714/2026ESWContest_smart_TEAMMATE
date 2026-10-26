// 실측 녹화 리플레이 (호스트 전용 도구) — 보드와 같은 DevicePosture 경로로 녹화를 다시 돌려 단계별 라벨 분포를 낸다.
//
//   tofposture_real_replay <posture_net.bin> <rec_raw.jsonl | rec_toflines.jsonl> <labels.csv | -> <t0 | protocol_start.txt | ->
//       [--variant name:spec ...] [--golden facedown_golden.csv] [--trim 2] [--capture start_s seconds] [--csv out.csv]
//
// 입력: record_board.py 녹화. rec_raw.jsonl 이면 kind:"raw"(보드 /raw 센서 객체 그대로 — 격자는 받은 그대로, 같은 (센서, ts_ms) 는 한 번)와
// kind:"state"(녹화 중 보드가 실제로 낸 /api/state)를 읽고, rec_toflines.jsonl 이면 {"host_t","port","raw":"F,…"} 줄을 읽는다.
// 가상 시계 = 보드 시계: t = ts_ms/1000 + off, off = median(host_t − ts_ms/1000) (PC 기준 구현과 같다). 폴링 시각(host_t)으로 줄 세우면
// 두 센서의 같은 프레임이 떨어져 들어와 짝을 놓친다 — 보드에서는 hub 가 A·B 를 연달아 넘긴다. 10 Hz tick(5 Hz 입력은 최신 프레임 재사용),
// 라벨 구간 안 앞뒤 trim s 제외해 센다. --show a,b,c 로 단계별 표에 낼 변형을 고른다(기본 첫 변형·radial+fill·마지막).
// --clock state: off 를 kind:"state" 줄(host_t − /api/state ts_ms/1000)로 잡는다. 10-26 PC 기준 구현(04527fb)이 이렇게 했는데, state ts_ms 는
// Pi 4 서비스의 단조 시계이고 /raw ts_ms 는 ESP32 millis 라 둘이 약 11 s 어긋난다 — PC 수치를 그대로 재현해 대조할 때만 쓴다.
// --warmup-skip S: 녹화 시작과 센서 공백(> 1 s, 서비스가 GRU 를 리셋하는 곳) 뒤 S 초는 채점에서 뺀다(PC 채점기는 run 시작 45 프레임 = 4.5 s 를 뺀다).
//
// --variant name:spec  spec = 콤마 목록: radial · fill=<mm> · fd(엎드림 규칙) · seated(upright 치환) · and(베이스라인 게이트) · never
//   기본 묶음: off · radial · fill=3000 · radial+fill · combo(radial,fill=3000,fd,seated) — 10-26 PC 표와 같은 조합
// 출력: 변형별 acc / macro-F1 두 가지 — instant(EMA 없는 융합 argmax, PC real_from_record 채점과 같은 기준)와 display(보드가 내는 값:
//   EMA 1 s + misplaced 게이트 + 엎드림 규칙 + upright 치환). 단계별 표는 board(녹화된 보드 출력)·첫 변형·마지막 변형의 display.
// --golden: PC 기준 구현 real_facedown_rule.py 의 프레임 CSV(ts_ms,up,lo,topA,topB,dlo,dup,flag,share,on,seated). 같은 짝짓기
//   (두 센서 ts_ms 교집합, 시간순)로 FaceDownRule 을 따로 돌려 정수·flag·on·seated 는 정확히, 실수는 1e-6 안으로 비교하고, 서비스 경로
//   (fd 를 켠 변형의 feed 짝짓기)가 낸 규칙 출력도 같은 골든과 대조한다.
#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "device_posture.h"
#include "facedown_rule.h"
#include "tof_line.h"

namespace {

struct Step { double start_s, end_s; std::string label; };

bool readLabels(const std::string& path, std::vector<Step>& out) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#' || line.rfind("start_s", 0) == 0) continue;
        const size_t a = line.find(','), b = a == std::string::npos ? a : line.find(',', a + 1);
        if (b == std::string::npos) continue;
        const size_t c = line.find(',', b + 1);
        Step s;
        s.start_s = std::atof(line.substr(0, a).c_str());
        s.end_s = std::atof(line.substr(a + 1, b - a - 1).c_str());
        s.label = c == std::string::npos ? line.substr(b + 1) : line.substr(b + 1, c - b - 1);
        out.push_back(s);
    }
    return !out.empty();
}

double readT0(const std::string& arg) {
    char* end = nullptr;
    const double v = std::strtod(arg.c_str(), &end);
    if (end != arg.c_str() && *end == '\0') return v;
    std::ifstream f(arg);
    std::string line;
    if (f && std::getline(f, line)) return std::atof(line.c_str());
    return 0.0;
}

double numAfter(const std::string& s, const char* key, size_t from = 0, bool* ok = nullptr) {
    const std::string k = std::string("\"") + key + "\"";
    const size_t p = s.find(k, from);
    if (ok) *ok = p != std::string::npos;
    if (p == std::string::npos) return 0.0;
    const size_t c = s.find(':', p + k.size());
    return std::atof(s.c_str() + c + 1);
}

bool intArray(const std::string& s, const char* key, size_t from, std::vector<int>& out) {
    const std::string k = std::string("\"") + key + "\"";
    const size_t p = s.find(k, from);
    if (p == std::string::npos) return false;
    const size_t a = s.find('[', p), b = s.find(']', a);
    if (a == std::string::npos || b == std::string::npos) return false;
    out.clear();
    const char* c = s.c_str() + a + 1;
    const char* stop = s.c_str() + b;
    while (c < stop) {
        while (c < stop && (*c == ' ' || *c == ',')) ++c;
        if (c >= stop) break;
        if (std::strncmp(c, "null", 4) == 0) { out.push_back(INT_MIN); c += 4; continue; }
        char* end = nullptr;
        const long v = std::strtol(c, &end, 10);
        if (end == c) { ++c; continue; }
        out.push_back(static_cast<int>(v));
        c = end;
    }
    return true;
}

struct Frame { double host_t = 0.0; int id = -1; uint32_t ts = 0; tof::TofLine line; };
struct BoardState { double host_t; std::string posture; double ts_ms; };

// rec_raw.jsonl 의 /raw 센서 객체 → 보드 파서가 만드는 것과 같은 TofFrame (d: ≤0 이나 status 255 면 -1, raw_* = 받은 그대로)
bool rawObject(const std::string& line, Frame& out) {
    const size_t sp = line.find("\"sensor\"");
    if (sp == std::string::npos) return false;
    bool ok = false;
    const int id = static_cast<int>(numAfter(line, "id", sp, &ok));
    if (!ok || line.find("\"have\":true", sp) == std::string::npos) return false;
    std::vector<int> d, st;
    if (!intArray(line, "dist_mm", sp, d) || !intArray(line, "status", sp, st) || d.size() != 64 || st.size() != 64) return false;
    out.id = id;
    out.ts = static_cast<uint32_t>(numAfter(line, "ts_ms", sp));
    tof::TofFrame& f = out.line.frame;
    out.line.kind = tof::LineKind::Frame;
    out.line.sensor_id = id;
    f.w = 8; f.h = 8; f.has_status = true;
    f.ms = out.ts;
    f.seq = out.ts;                      // 폴링 녹화는 프레임을 건너뛴다 — frame_id 간격 리셋을 흉내 내지 않고 시계만 단조로
    f.seq16 = false;
    f.flip_rows_hint = 0; f.flip_cols_hint = 0;   // 표준 0x05: 펌웨어가 이미 돌렸다
    for (int i = 0; i < 64; ++i) {
        const int dv = d[static_cast<size_t>(i)] == INT_MIN ? -1 : d[static_cast<size_t>(i)];
        const int sv = st[static_cast<size_t>(i)] == INT_MIN ? 255 : st[static_cast<size_t>(i)];
        f.raw_d[i] = static_cast<int16_t>(dv);
        f.raw_st[i] = static_cast<uint8_t>(sv);
        f.d[i] = (dv <= 0 || sv == 255) ? int16_t(-1) : static_cast<int16_t>(dv);
        f.st[i] = static_cast<uint8_t>(sv);
    }
    return true;
}

struct Counter {
    std::map<std::string, long> mix;
    long n = 0;
    void add(const std::string& label) { ++mix[label]; ++n; }
    double share(const std::string& label) const { auto it = mix.find(label); return n ? 100.0 * (it == mix.end() ? 0 : it->second) / n : 0.0; }
    std::string top(int k) const {
        std::vector<std::pair<std::string, long>> v(mix.begin(), mix.end());
        std::sort(v.begin(), v.end(), [](const auto& a, const auto& b) { return a.second != b.second ? a.second > b.second : a.first < b.first; });
        std::string o;
        for (int i = 0; i < k && i < static_cast<int>(v.size()); ++i) {
            char buf[64];
            std::snprintf(buf, sizeof buf, "%s%s %.0f%%", i ? ", " : "", v[static_cast<size_t>(i)].first.c_str(),
                          100.0 * v[static_cast<size_t>(i)].second / std::max(1L, n));
            o += buf;
        }
        return o;
    }
};

struct Score {
    std::vector<std::pair<std::string, std::string>> yp;   // (truth, pred)
    void add(const std::string& y, const std::string& p) { yp.emplace_back(y, p); }
    double acc() const { long c = 0; for (const auto& t : yp) c += t.first == t.second; return yp.empty() ? 0.0 : static_cast<double>(c) / yp.size(); }
    double macroF1() const {   // PC real_from_record 와 같게: 정답에 나온 클래스마다 2tp / (n_pred + n_true), 평균
        std::set<std::string> cls;
        for (const auto& t : yp) cls.insert(t.first);
        double sum = 0.0;
        for (const auto& c : cls) {
            double tp = 0, np = 0, nt = 0;
            for (const auto& t : yp) { tp += (t.first == c && t.second == c); np += t.second == c; nt += t.first == c; }
            sum += 2.0 * tp / std::max(1.0, np + nt);
        }
        return cls.empty() ? 0.0 : sum / cls.size();
    }
};

struct Variant {
    std::string name;
    tof::DeviceConfig cfg;
    std::unique_ptr<tof::DevicePosture> dev;
    Score instant, display;
    std::vector<Counter> per_step;
    std::map<uint32_t, std::pair<bool, bool>> fd_out;     // 서비스 경로의 규칙 출력: ts → (on, seated)
    uint64_t fd_pairs_seen = 0;
    long nondrowsy_ticks = 0, nondrowsy_drowsy = 0, nondrowsy_drowsy80 = 0;
    double model_drowsy_max = 0.0;
};

bool parseVariant(const std::string& arg, Variant& v) {
    const size_t c = arg.find(':');
    v.name = c == std::string::npos ? arg : arg.substr(0, c);
    const std::string spec = c == std::string::npos ? arg : arg.substr(c + 1);
    std::stringstream ss(spec);
    std::string tok;
    while (std::getline(ss, tok, ',')) {
        if (tok.empty() || tok == "off") continue;
        if (tok == "radial") v.cfg.radial = true;
        else if (tok.rfind("fill", 0) == 0) v.cfg.fill_notarget_mm = tok.size() > 5 ? std::atoi(tok.c_str() + 5) : 3000;
        else if (tok == "fd") v.cfg.facedown_rule = true;
        else if (tok == "seated") v.cfg.seated_upright = true;
        else if (tok == "and") v.cfg.misplaced_rule = tof::MisplacedRule::And;
        else if (tok == "never") v.cfg.misplaced_rule = tof::MisplacedRule::Never;
        else { std::fprintf(stderr, "unknown variant token %s\n", tok.c_str()); return false; }
    }
    return true;
}

struct GoldenRow { uint32_t ts; int up, lo, topA, topB; bool has_dlo, has_dup; double dlo, dup; bool flag; double share; bool on, seated; };

bool readGolden(const std::string& path, std::vector<GoldenRow>& out) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    if (!std::getline(f, line)) return false;
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::vector<std::string> head;
    { std::stringstream hs(line); std::string h; while (std::getline(hs, h, ',')) head.push_back(h); }
    auto col = [&](const char* name) { for (size_t i = 0; i < head.size(); ++i) if (head[i] == name) return static_cast<int>(i); return -1; };
    const int cts = col("ts_ms"), cup = col("up"), clo = col("lo"), cta = col("topA"), ctb = col("topB"), cdlo = col("dlo"), cdup = col("dup"),
              cfl = col("flag"), csh = col("share"), con = col("on"), cse = col("seated");
    if (cts < 0 || cup < 0 || clo < 0 || cta < 0 || ctb < 0 || cdlo < 0 || cdup < 0 || cfl < 0 || csh < 0 || con < 0 || cse < 0) return false;
    auto b = [](const std::string& s) { return s == "1" || s == "True" || s == "true"; };
    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        std::vector<std::string> v;
        std::stringstream ls(line);
        std::string x;
        while (std::getline(ls, x, ',')) v.push_back(x);
        while (v.size() < head.size()) v.push_back("");
        auto at = [&](int c) -> const std::string& { return v[static_cast<size_t>(c)]; };
        GoldenRow r{};
        r.ts = static_cast<uint32_t>(std::atoll(at(cts).c_str()));
        r.up = std::atoi(at(cup).c_str()); r.lo = std::atoi(at(clo).c_str());
        r.topA = std::atoi(at(cta).c_str()); r.topB = std::atoi(at(ctb).c_str());
        r.has_dlo = !at(cdlo).empty(); r.dlo = std::atof(at(cdlo).c_str());
        r.has_dup = !at(cdup).empty(); r.dup = std::atof(at(cdup).c_str());
        r.flag = b(at(cfl)); r.share = std::atof(at(csh).c_str());
        r.on = b(at(con)); r.seated = b(at(cse));
        out.push_back(r);
    }
    return !out.empty();
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 5) {
        std::fprintf(stderr, "usage: %s posture_net.bin rec_raw.jsonl|rec_toflines.jsonl labels.csv|- t0|protocol_start.txt|- "
                             "[--variant name:spec] [--golden csv] [--trim s] [--capture start_s seconds] [--csv out.csv]\n", argv[0]);
        return 2;
    }
    const std::string bin = argv[1], rec = argv[2], labels_path = argv[3], t0_arg = argv[4];
    double trim = 2.0, cap_start = 0.0;
    int cap_seconds = 0;
    std::string golden_path, csv_out, show_arg, clock = "raw";
    double warmup_skip = 0.0;
    std::vector<Variant> variants;
    for (int i = 5; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&](int k = 1) { return i + k < argc ? std::string(argv[i + k]) : std::string("0"); };
        if (a == "--variant") { Variant v; if (!parseVariant(next(), v)) return 2; variants.push_back(std::move(v)); ++i; }
        else if (a == "--golden") { golden_path = next(); ++i; }
        else if (a == "--trim") { trim = std::atof(next().c_str()); ++i; }
        else if (a == "--capture") { cap_start = std::atof(next().c_str()); cap_seconds = std::atoi(next(2).c_str()); i += 2; }
        else if (a == "--csv") { csv_out = next(); ++i; }
        else if (a == "--show") { show_arg = next(); ++i; }
        else if (a == "--clock") { clock = next(); ++i; }
        else if (a == "--warmup-skip") { warmup_skip = std::atof(next().c_str()); ++i; }
    }
    if (variants.empty())
        for (const char* v : {"off:off", "radial:radial", "fill:fill=3000", "radial+fill:radial,fill=3000", "combo:radial,fill=3000,fd,seated"}) {
            Variant x; parseVariant(v, x); variants.push_back(std::move(x));
        }

    // ---- 입력 ----
    std::vector<Frame> frames;
    std::vector<BoardState> board;
    {
        std::ifstream f(rec);
        if (!f) { std::fprintf(stderr, "cannot open %s\n", rec.c_str()); return 1; }
        std::set<std::pair<int, uint32_t>> seen;
        std::map<std::string, int> port_id;
        std::string line;
        while (std::getline(f, line)) {
            bool ok = false;
            const double ht = numAfter(line, "host_t", 0, &ok);
            if (!ok) continue;
            if (line.find("\"kind\":\"raw\"") != std::string::npos) {
                Frame fr;
                if (!rawObject(line, fr)) continue;
                if (!seen.insert({fr.id, fr.ts}).second) continue;
                fr.host_t = ht;
                frames.push_back(fr);
            } else if (line.find("\"kind\":\"state\"") != std::string::npos) {
                const size_t k = line.find("\"posture\":\"");
                if (k == std::string::npos) continue;
                const size_t q = line.find('"', k + 11);
                const size_t sp = line.find("\"state\"");
                board.push_back({ht, line.substr(k + 11, q - k - 11), numAfter(line, "ts_ms", sp == std::string::npos ? 0 : sp)});
            } else if (line.find("\"raw\":\"F,") != std::string::npos) {
                std::string inner, port;
                Frame fr;
                if (!tof::unwrapRecordLine(line, inner, port) || !tof::parseTofLine(line, fr.line) || fr.line.kind != tof::LineKind::Frame) continue;
                auto it = port_id.find(port);
                if (it == port_id.end()) it = port_id.emplace(port, static_cast<int>(port_id.size())).first;
                fr.id = it->second;
                fr.ts = fr.line.frame.ms;
                fr.line.sensor_id = fr.id;
                if (!seen.insert({fr.id, fr.ts}).second) continue;
                fr.host_t = ht;
                frames.push_back(fr);
            }
        }
    }
    if (frames.empty()) { std::fprintf(stderr, "no frames in %s\n", rec.c_str()); return 1; }
    {   // 보드 시계로: off = median(host_t − ts/1000) (보드 ms 는 재부팅 전까지 단조)
        std::vector<double> offs;
        if (clock == "state")
            for (const BoardState& b : board) offs.push_back(b.host_t - b.ts_ms / 1000.0);
        if (offs.empty())
            for (const Frame& fr : frames) offs.push_back(fr.host_t - fr.ts / 1000.0);
        std::sort(offs.begin(), offs.end());
        const size_t m = offs.size();
        const double off = m % 2 ? offs[m / 2] : (offs[m / 2 - 1] + offs[m / 2]) / 2.0;
        for (Frame& fr : frames) fr.host_t = fr.ts / 1000.0 + off;
        std::printf("board clock offset %.3f s (host_t - ts_ms/1000, median of %s)\n", off, clock == "state" ? "/api/state lines" : "/raw frames");
    }
    std::stable_sort(frames.begin(), frames.end(), [](const Frame& a, const Frame& b) { return a.ts != b.ts ? a.ts < b.ts : a.id < b.id; });
    std::vector<Step> steps;
    if (labels_path != "-" && !readLabels(labels_path, steps)) { std::fprintf(stderr, "cannot read labels %s\n", labels_path.c_str()); return 1; }
    const double first_t = frames.front().host_t;
    const double t0 = t0_arg == "-" ? first_t : readT0(t0_arg);

    // ---- 규칙 골든: PC 와 같은 짝짓기(두 센서 ts 교집합, ts 순)로 FaceDownRule 단독 실행 ----
    std::vector<GoldenRow> golden;
    if (!golden_path.empty()) {
        if (!readGolden(golden_path, golden)) { std::fprintf(stderr, "cannot read golden %s\n", golden_path.c_str()); return 1; }
        std::map<uint32_t, const Frame*> a, b;
        for (const Frame& fr : frames) (fr.id == 0 ? a : b)[fr.ts] = &fr;
        tof::FaceDownRule rule;
        size_t gi = 0, n = 0, int_mis = 0, bool_mis = 0, extra = 0;
        double max_f = 0.0;
        for (const auto& kv : a) {
            auto jt = b.find(kv.first);
            if (jt == b.end()) continue;
            const tof::TofFrame& fa = kv.second->line.frame;
            const tof::TofFrame& fb = jt->second->line.frame;
            const tof::FaceDownFeatures r = rule.update(kv.first / 1000.0, fa.raw_d, fa.raw_st, fb.raw_d, fb.raw_st);
            ++n;
            if (gi >= golden.size() || golden[gi].ts != kv.first) { ++extra; continue; }
            const GoldenRow& g = golden[gi++];
            int_mis += (r.up != g.up) + (r.lo != g.lo) + (r.topA != g.topA) + (r.topB != g.topB) + (r.has_dlo != g.has_dlo) + (r.has_dup != g.has_dup);
            bool_mis += (r.flag != g.flag) + (rule.on() != g.on) + (r.seated != g.seated);
            if (r.has_dlo && g.has_dlo) max_f = std::max(max_f, std::fabs(r.dlo - g.dlo));
            if (r.has_dup && g.has_dup) max_f = std::max(max_f, std::fabs(r.dup - g.dup));
            max_f = std::max(max_f, std::fabs(rule.share() - g.share));
        }
        const bool pass = n == golden.size() && extra == 0 && int_mis == 0 && bool_mis == 0 && max_f < 1e-6;
        std::printf("facedown golden (standalone, PC pairing): %zu pairs vs %zu golden rows, int mismatches %zu, flag/on/seated mismatches %zu, "
                    "max |float diff| %.2g -> %s\n", n, golden.size(), int_mis, bool_mis, max_f, pass ? "MATCH" : "MISMATCH");
    }

    // ---- 서비스 경로 ----
    for (Variant& v : variants) {
        v.dev = std::make_unique<tof::DevicePosture>(v.cfg);
        std::string err;
        if (!v.dev->loadModel(bin, &err)) { std::fprintf(stderr, "model: %s\n", err.c_str()); return 1; }
        v.dev->addSensor("s0");
        v.dev->addSensor("s1");
        v.per_step.assign(steps.size(), Counter());
    }
    std::vector<Counter> board_step(steps.size());
    for (const BoardState& b : board) {
        const double tr = b.host_t - t0;
        for (size_t si = 0; si < steps.size(); ++si)
            if (tr >= steps[si].start_s + trim && tr <= steps[si].end_s - trim) { board_step[si].add(b.posture); break; }
    }
    auto rel_ms = [&](double epoch) { return static_cast<int64_t>(std::llround((epoch - first_t) * 1000.0)); };
    // GRU 가 새로 시작하는 시각: 녹화 시작과, 한 센서의 프레임 간격이 stale(1 s) 를 넘은 곳
    std::vector<std::pair<int64_t, int64_t>> warm;      // [시작, 끝) ms
    if (warmup_skip > 0) {
        std::map<int, double> last;
        for (const Frame& fr : frames) {
            auto it = last.find(fr.id);
            if (it == last.end() || fr.host_t - it->second > 1.0) {
                const int64_t a = rel_ms(fr.host_t);
                warm.emplace_back(a, a + static_cast<int64_t>(warmup_skip * 1000.0));
            }
            last[fr.id] = fr.host_t;
        }
    }
    auto in_warmup = [&](int64_t now) { for (const auto& w : warm) if (now >= w.first && now < w.second) return true; return false; };
    const int64_t period = 100;
    int64_t next_tick = 0;
    bool capture_started = false;
    long ticks = 0;
    auto do_tick = [&](int64_t now) {
        if (!capture_started && cap_seconds > 0 && now >= rel_ms(t0 + cap_start)) {
            capture_started = true;
            for (Variant& v : variants) v.dev->startBaselineCapture(now, cap_seconds, "replay");
        }
        bool any = false;
        for (Variant& v : variants) any = v.dev->tick(now) || any;
        if (!any) return;
        ++ticks;
        const double tr = (first_t + now / 1000.0) - t0;
        if (in_warmup(now)) return;
        for (size_t si = 0; si < steps.size(); ++si) {
            if (tr < steps[si].start_s + trim || tr > steps[si].end_s - trim) continue;
            for (Variant& v : variants) {
                const tof::DeviceResult r = v.dev->result();
                const std::string disp = r.valid ? tof::DevicePosture::labelName(r.display_argmax) : "invalid";
                const std::string inst = r.valid ? tof::DevicePosture::labelName(r.argmax) : "invalid";
                v.per_step[si].add(disp);
                v.display.add(steps[si].label, disp);
                v.instant.add(steps[si].label, inst);
                if (steps[si].label != "drowsy") {
                    ++v.nondrowsy_ticks;
                    if (disp == "drowsy") { ++v.nondrowsy_drowsy; if (r.display[5] >= 0.80f) ++v.nondrowsy_drowsy80; }
                    if (r.valid) v.model_drowsy_max = std::max(v.model_drowsy_max, static_cast<double>(r.smooth[5]));
                }
            }
            break;
        }
    };
    for (const Frame& fr : frames) {
        const int64_t now = rel_ms(fr.host_t);
        while (next_tick <= now) { do_tick(next_tick); next_tick += period; }
        if (fr.id < 0 || fr.id > 1) continue;
        for (Variant& v : variants) {
            v.dev->feed(fr.id, fr.line, now);
            if (v.cfg.facedown_rule || v.cfg.seated_upright) {
                const tof::FaceDownState s = v.dev->faceDownState();
                if (s.pairs != v.fd_pairs_seen) {
                    v.fd_pairs_seen = s.pairs;
                    v.fd_out[static_cast<uint32_t>(std::llround(s.last_t_s * 1000.0))] = {s.on, s.last.seated};
                }
            }
        }
    }
    for (int k = 0; k < 20; ++k) { do_tick(next_tick); next_tick += period; }

    std::printf("recording %s: %zu frames, %zu board states, %ld ticks, t0 %.3f (first frame at %+.1f s)\n", rec.c_str(), frames.size(),
                board.size(), ticks, t0, first_t - t0);
    if (!golden.empty())
        for (Variant& v : variants) {
            if (!v.cfg.facedown_rule) continue;
            size_t miss = 0, onmis = 0, semis = 0;
            for (const GoldenRow& g : golden) {
                auto it = v.fd_out.find(g.ts);
                if (it == v.fd_out.end()) { ++miss; continue; }
                onmis += it->second.first != g.on;
                semis += it->second.second != g.seated;
            }
            std::printf("facedown golden (service path, variant %s): %zu pairs evaluated, %zu golden pairs not seen, on mismatches %zu, "
                        "seated mismatches %zu -> %s\n", v.name.c_str(), v.fd_out.size(), miss, onmis, semis,
                        (miss == 0 && onmis == 0 && semis == 0 && v.fd_out.size() == golden.size()) ? "MATCH" : "MISMATCH");
        }
    if (steps.empty()) {   // 라벨 없는 녹화: 규칙이 켜진 구간만
        for (Variant& v : variants) {
            if (!v.cfg.facedown_rule) continue;
            std::printf("face_down rule on-runs (variant %s, board s from first pair):", v.name.c_str());
            bool on = false;
            uint32_t start = 0, prev = 0;
            const uint32_t first = v.fd_out.empty() ? 0 : v.fd_out.begin()->first;
            long on_n = 0;
            for (const auto& kv : v.fd_out) {
                if (kv.second.first) ++on_n;
                if (kv.second.first && !on) { on = true; start = kv.first; }
                if (!kv.second.first && on) { on = false; std::printf(" [%.1f-%.1f]", (start - first) / 1000.0, (prev - first) / 1000.0); }
                prev = kv.first;
            }
            if (on) std::printf(" [%.1f-%.1f]", (start - first) / 1000.0, (prev - first) / 1000.0);
            std::printf("  on %.1f%% of %zu pairs\n", v.fd_out.empty() ? 0.0 : 100.0 * on_n / v.fd_out.size(), v.fd_out.size());
        }
        return 0;
    }

    std::printf("\n| variant | instant acc / F1 (no EMA, PC scorer basis) | display acc / F1 (board output) | non-drowsy ticks shown drowsy (conf >= 0.80) | max smoothed P(drowsy) off drowsy steps |\n|---|---|---|---|---|\n");
    for (Variant& v : variants)
        std::printf("| %s | %.3f / %.3f | %.3f / %.3f | %.1f%% (%.1f%%) | %.3f |\n", v.name.c_str(), v.instant.acc(), v.instant.macroF1(),
                    v.display.acc(), v.display.macroF1(), v.nondrowsy_ticks ? 100.0 * v.nondrowsy_drowsy / v.nondrowsy_ticks : 0.0,
                    v.nondrowsy_ticks ? 100.0 * v.nondrowsy_drowsy80 / v.nondrowsy_ticks : 0.0, v.model_drowsy_max);
    std::vector<const Variant*> show;
    {
        std::vector<std::string> names;
        if (show_arg.empty()) { names.push_back(variants.front().name); names.push_back("radial+fill"); names.push_back(variants.back().name); }
        else { std::stringstream ss(show_arg); std::string x; while (std::getline(ss, x, ',')) names.push_back(x); }
        for (const auto& n : names)
            for (const Variant& v : variants)
                if (v.name == n && std::find(show.begin(), show.end(), &v) == show.end()) show.push_back(&v);
    }
    const Variant* first_v = &variants.front();
    std::printf("\n| step | label | ticks | board (recorded) |");
    for (const Variant* v : show) std::printf(" %s |", v->name.c_str());
    std::printf(" match board");
    for (const Variant* v : show) std::printf(" / %s", v->name.c_str());
    std::printf(" |\n|---|---|---|---|");
    for (size_t k = 0; k < show.size(); ++k) std::printf("---|");
    std::printf("---|\n");
    std::ofstream csv;
    if (!csv_out.empty()) { csv.open(csv_out, std::ios::trunc); csv << "step,label,ticks,variant,top,match_pct,face_down_pct\n"; }
    for (size_t si = 0; si < steps.size(); ++si) {
        const Counter& co = first_v->per_step[si];
        if (co.n == 0) continue;
        std::printf("| %zu | %s | %ld | %s |", si + 1, steps[si].label.c_str(), co.n, board_step[si].n ? board_step[si].top(3).c_str() : "-");
        for (const Variant* v : show) std::printf(" %s |", v->per_step[si].top(3).c_str());
        std::printf(" %.0f", board_step[si].share(steps[si].label));
        for (const Variant* v : show) std::printf(" / %.0f", v->per_step[si].share(steps[si].label));
        std::printf(" |\n");
        if (csv)
            for (const Variant& v : variants)
                csv << si + 1 << "," << steps[si].label << "," << co.n << "," << v.name << ",\"" << v.per_step[si].top(3) << "\","
                    << v.per_step[si].share(steps[si].label) << "," << v.per_step[si].share("face_down") << "\n";
    }
    return 0;
}
