// 실측 녹화 리플레이 (호스트 전용 도구) — misplaced 게이트 전/후의 라벨 분포를 단계별로 비교한다.
//
//   tofposture_real_replay <posture_net.bin> <rec_toflines.jsonl> <labels.csv> <t0 | protocol_start.txt>
//                          [--capture <start_s> <seconds>] [--trim 2] [--bg-mm 1200] [--min-mm 80] [--sigma 4] [--frac 0.5] [--hold 10]
//                          [--flip-rows 0|1] [--flip-cols 0|1] [--csv out.csv] [--board rec_raw.jsonl]
// --board 는 같은 녹화의 rec_raw.jsonl 에 남은 보드의 /api/state(kind:"state") 를 읽어 단계별 "보드가 실제로 낸" 라벨 분포를 한 열 더 낸다
// (리플레이 off 열이 이것과 같아야 리플레이가 보드를 재현한 것이다).
//
// 입력은 dataset/real_20261026/*.zip 안의 record_board.py 산출물: rec_toflines.jsonl 은 tof_live --record 형식
// {"host_t":<epoch s>,"port":"S0"|"S1","raw":"F,<ts_ms>,<64 dist>,<64 status>"} (S0 = 센서 A, S1 = B, 격자는 보드가 받은 그대로),
// labels.csv 는 start_s,end_s,label,what (t0 기준 실제 수행 시각), t0 는 숫자 또는 첫 줄이 t0 인 protocol_start 파일.
// 가상 시계는 host_t. 10 Hz 로 tick 하고(5 Hz 입력은 보드처럼 재사용) 라벨 구간 안(앞뒤 trim s 제외)의 표시 라벨을 센다.
// 같은 입력을 세 설정으로 동시에 돌린다: off(모델 그대로 = 보드의 10-26 동작) · and(모델 ∧ 베이스라인 규칙) · never(misplaced 항상 억제).
// --capture 는 녹화 안의 "빈 자리" 단계(보통 첫 30 s)에서 설치 때처럼 베이스라인을 잡는다 (기본 0 s 부터 10 s).
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "device_posture.h"
#include "tof_line.h"

namespace {

struct Step { double start_s, end_s; std::string label, what; };

bool readLabels(const std::string& path, std::vector<Step>& out) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#' || line.rfind("start_s", 0) == 0) continue;
        Step s;
        size_t a = line.find(','), b = a == std::string::npos ? a : line.find(',', a + 1), c = b == std::string::npos ? b : line.find(',', b + 1);
        if (b == std::string::npos) continue;
        s.start_s = std::atof(line.substr(0, a).c_str());
        s.end_s = std::atof(line.substr(a + 1, b - a - 1).c_str());
        s.label = c == std::string::npos ? line.substr(b + 1) : line.substr(b + 1, c - b - 1);
        s.what = c == std::string::npos ? "" : line.substr(c + 1);
        if (!s.label.empty() && s.label.back() == '\r') s.label.pop_back();
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

bool hostT(const std::string& line, double& t) {
    const size_t k = line.find("\"host_t\"");
    if (k == std::string::npos) return false;
    const size_t c = line.find(':', k);
    if (c == std::string::npos) return false;
    t = std::atof(line.c_str() + c + 1);
    return t > 0;
}

struct Counter {
    std::map<std::string, long> mix;
    long n = 0;
    void add(const char* label) { ++mix[label]; ++n; }
    std::string top(int k) const {
        std::vector<std::pair<std::string, long>> v(mix.begin(), mix.end());
        std::sort(v.begin(), v.end(), [](const auto& a, const auto& b) { return a.second > b.second; });
        std::string o;
        for (int i = 0; i < k && i < static_cast<int>(v.size()); ++i) {
            char buf[64];
            std::snprintf(buf, sizeof buf, "%s%s %.0f%%", i ? ", " : "", v[static_cast<size_t>(i)].first.c_str(),
                          100.0 * v[static_cast<size_t>(i)].second / std::max(1L, n));
            o += buf;
        }
        return o;
    }
    double share(const std::string& label) const { auto it = mix.find(label); return n ? 100.0 * (it == mix.end() ? 0 : it->second) / n : 0.0; }
};

}  // namespace

int main(int argc, char** argv) {
    if (argc < 5) {
        std::fprintf(stderr, "usage: %s posture_net.bin rec_toflines.jsonl labels.csv t0|protocol_start.txt [--capture start_s seconds] [--trim s]"
                             " [--bg-mm N] [--min-mm N] [--sigma F] [--frac F] [--hold S] [--flip-rows 0|1] [--flip-cols 0|1] [--csv out.csv]\n", argv[0]);
        return 2;
    }
    const std::string bin = argv[1], rec = argv[2], labels_path = argv[3];
    const double t0 = readT0(argv[4]);
    double cap_start = 0.0; int cap_seconds = 10; double trim = 2.0;
    tof::DeviceConfig base;
    std::string csv_out, board_path;
    for (int i = 5; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&](int k = 1) { return i + k < argc ? argv[i + k] : "0"; };
        if (a == "--capture") { cap_start = std::atof(next()); cap_seconds = std::atoi(next(2)); i += 2; }
        else if (a == "--trim") { trim = std::atof(next()); ++i; }
        else if (a == "--bg-mm") { base.moved.bg_min_mm = std::atoi(next()); ++i; }
        else if (a == "--min-mm") { base.moved.moved_min_mm = std::atoi(next()); ++i; }
        else if (a == "--sigma") { base.moved.moved_sigma = static_cast<float>(std::atof(next())); ++i; }
        else if (a == "--frac") { base.moved.moved_frac_min = static_cast<float>(std::atof(next())); ++i; }
        else if (a == "--hold") { base.moved.hold_s = std::atoi(next()); ++i; }
        else if (a == "--min-zones") { base.moved.min_eval_zones = std::atoi(next()); ++i; }
        else if (a == "--flip-rows") { base.flip_rows = std::atoi(next()); ++i; }
        else if (a == "--flip-cols") { base.flip_cols = std::atoi(next()); ++i; }
        else if (a == "--csv") { csv_out = next(); ++i; }
        else if (a == "--board") { board_path = next(); ++i; }
    }
    std::vector<Step> steps;
    if (!readLabels(labels_path, steps)) { std::fprintf(stderr, "cannot read labels %s\n", labels_path.c_str()); return 1; }
    if (t0 <= 0) { std::fprintf(stderr, "bad t0\n"); return 1; }

    const char* names[3] = {"off", "and", "never"};
    tof::MisplacedRule modes[3] = {tof::MisplacedRule::Off, tof::MisplacedRule::And, tof::MisplacedRule::Never};
    std::vector<tof::DevicePosture*> devs;
    for (int m = 0; m < 3; ++m) {
        tof::DeviceConfig cfg = base;
        cfg.misplaced_rule = modes[m];
        tof::DevicePosture* d = new tof::DevicePosture(cfg);
        std::string err;
        if (!d->loadModel(bin, &err)) { std::fprintf(stderr, "model: %s\n", err.c_str()); return 1; }
        devs.push_back(d);
    }
    std::map<std::string, int> slot_of;                           // port -> slot index (S0 -> 0, S1 -> 1 in order of appearance)

    std::ifstream f(rec);
    if (!f) { std::fprintf(stderr, "cannot open %s\n", rec.c_str()); return 1; }
    std::string line;
    double first_t = -1.0;
    int64_t next_tick = -1;
    const int64_t period = 100;
    std::vector<std::vector<Counter>> per_step(3, std::vector<Counter>(steps.size()));
    std::vector<Counter> overall(3);
    std::vector<long> correct(3, 0), scored(3, 0);
    long moved_ticks = 0, ticks = 0;
    float moved_max_seen = -1.f;
    bool capture_started = false;
    long frames = 0;
    auto rel_ms = [&](double epoch) { return static_cast<int64_t>((epoch - first_t) * 1000.0 + 0.5); };
    auto do_tick = [&](int64_t now) {
        if (!capture_started && cap_seconds > 0 && now >= rel_ms(t0 + cap_start)) {
            capture_started = true;
            for (auto* d : devs) d->startBaselineCapture(now, cap_seconds, "replay");
        }
        bool ticked = false;
        for (auto* d : devs) ticked = d->tick(now) || ticked;
        if (!ticked) return;
        ++ticks;
        const tof::DeviceResult ra = devs[1]->result();
        if (ra.device_moved) ++moved_ticks;
        if (ra.moved_frac_max > moved_max_seen) moved_max_seen = ra.moved_frac_max;
        const double t_rel = (first_t + now / 1000.0) - t0;
        for (size_t si = 0; si < steps.size(); ++si) {
            if (t_rel < steps[si].start_s + trim || t_rel > steps[si].end_s - trim) continue;
            for (int m = 0; m < 3; ++m) {
                const tof::DeviceResult r = devs[static_cast<size_t>(m)]->result();
                const char* lab = r.valid ? tof::DevicePosture::labelName(r.display_argmax) : "invalid";
                per_step[static_cast<size_t>(m)][si].add(lab);
                overall[static_cast<size_t>(m)].add(lab);
                ++scored[static_cast<size_t>(m)];
                if (steps[si].label == lab) ++correct[static_cast<size_t>(m)];
            }
            break;
        }
    };
    while (std::getline(f, line)) {
        double ht = 0.0;
        if (!hostT(line, ht)) continue;
        std::string inner, port;
        if (!tof::unwrapRecordLine(line, inner, port)) continue;
        tof::TofLine l;
        if (!tof::parseTofLine(line, l) || l.kind != tof::LineKind::Frame) continue;
        if (first_t < 0) { first_t = ht; next_tick = 0; }
        const int64_t now = rel_ms(ht);
        while (next_tick <= now) { do_tick(next_tick); next_tick += period; }
        auto it = slot_of.find(port);
        if (it == slot_of.end()) {
            int idx = -1;
            for (auto* d : devs) idx = d->addSensor(port);
            it = slot_of.emplace(port, idx).first;
            std::fprintf(stderr, "sensor %d <- port %s\n", idx, port.c_str());
        }
        for (auto* d : devs) d->feed(it->second, l, now);
        ++frames;
    }
    for (int k = 0; k < 20; ++k) { do_tick(next_tick); next_tick += period; }   // 끝 2 s 더 tick

    // 보드가 녹화 중 실제로 낸 /api/state 의 posture (kind:"state")
    std::vector<Counter> board(steps.size());
    long board_states = 0;
    if (!board_path.empty()) {
        std::ifstream bf(board_path);
        std::string bl;
        while (bf && std::getline(bf, bl)) {
            if (bl.find("\"kind\":\"state\"") == std::string::npos) continue;
            double ht = 0.0;
            if (!hostT(bl, ht)) continue;
            const size_t k = bl.find("\"posture\":\"");
            if (k == std::string::npos) continue;
            const size_t q = bl.find('"', k + 11);
            const std::string lab = bl.substr(k + 11, q - k - 11);
            const double t_rel = ht - t0;
            ++board_states;
            for (size_t si = 0; si < steps.size(); ++si)
                if (t_rel >= steps[si].start_s + trim && t_rel <= steps[si].end_s - trim) { board[si].add(lab.c_str()); break; }
        }
        std::printf("board states: %ld lines from %s\n", board_states, board_path.c_str());
    }
    const tof::Baseline b = devs[1]->baseline();
    std::printf("recording: %ld frames, %ld ticks, %zu sensors, t0 %.1f, first %.1f (labels start at +%.1f s)\n", frames, ticks, slot_of.size(), t0,
                first_t, first_t - t0);
    std::printf("baseline (%s): ", b.any() ? b.source.c_str() : "none");
    for (const auto& s : b.sensors) std::printf("sensor %d %d frames, bg zones %d/64, sigma %.1f mm; ", s.id, s.frames, s.bg_zones, s.sigma_mm);
    std::printf("\nrule: bg >= %d mm, thr max(%d, %.1f sigma), frac >= %.2f held %d s -> device_moved on %ld/%ld ticks (%.1f%%), max moved_frac %.2f\n",
                base.moved.bg_min_mm, base.moved.moved_min_mm, base.moved.moved_sigma, base.moved.moved_frac_min, base.moved.hold_s, moved_ticks, ticks,
                ticks ? 100.0 * moved_ticks / ticks : 0.0, moved_max_seen);
    if (board_states)
        std::printf("\n| step | label | ticks | board (recorded) | off (replay, model as-is) | and (model AND baseline rule) | never | match board / off / and / never |\n|---|---|---|---|---|---|---|---|\n");
    else
        std::printf("\n| step | label | ticks | off (model as-is) | and (model AND baseline rule) | never | match off / and / never |\n|---|---|---|---|---|---|---|\n");
    std::ofstream csv;
    if (!csv_out.empty()) { csv.open(csv_out, std::ios::trunc); csv << "step,label,ticks,mode,top,match_pct\n"; }
    for (size_t si = 0; si < steps.size(); ++si) {
        const Counter& c0 = per_step[0][si];
        if (c0.n == 0) continue;
        if (board_states)
            std::printf("| %zu | %s | %ld | %s | %s | %s | %s | %.0f / %.0f / %.0f / %.0f |\n", si + 1, steps[si].label.c_str(), c0.n,
                        board[si].top(3).c_str(), c0.top(3).c_str(), per_step[1][si].top(3).c_str(), per_step[2][si].top(3).c_str(),
                        board[si].share(steps[si].label), c0.share(steps[si].label), per_step[1][si].share(steps[si].label),
                        per_step[2][si].share(steps[si].label));
        else
            std::printf("| %zu | %s | %ld | %s | %s | %s | %.0f / %.0f / %.0f |\n", si + 1, steps[si].label.c_str(), c0.n, c0.top(3).c_str(),
                        per_step[1][si].top(3).c_str(), per_step[2][si].top(3).c_str(), c0.share(steps[si].label),
                        per_step[1][si].share(steps[si].label), per_step[2][si].share(steps[si].label));
        if (csv)
            for (int m = 0; m < 3; ++m)
                csv << si + 1 << "," << steps[si].label << "," << c0.n << "," << names[m] << ",\"" << per_step[static_cast<size_t>(m)][si].top(3)
                    << "\"," << per_step[static_cast<size_t>(m)][si].share(steps[si].label) << "\n";
    }
    std::printf("\noverall (labelled ticks, +-%.0f s trimmed):\n", trim);
    for (int m = 0; m < 3; ++m)
        std::printf("  %-5s acc %.3f (%ld/%ld), misplaced %.1f%%, mix: %s\n", names[m],
                    scored[static_cast<size_t>(m)] ? static_cast<double>(correct[static_cast<size_t>(m)]) / scored[static_cast<size_t>(m)] : 0.0,
                    correct[static_cast<size_t>(m)], scored[static_cast<size_t>(m)], overall[static_cast<size_t>(m)].share("misplaced"),
                    overall[static_cast<size_t>(m)].top(6).c_str());
    for (auto* d : devs) delete d;
    return 0;
}
