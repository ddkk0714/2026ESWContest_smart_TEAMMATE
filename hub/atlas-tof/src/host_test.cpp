// 호스트(x86) 테스트: 보드·센서 없이 돌아가는 부분을 검사한다.
//
//   host_test <posture_net.bin> <golden.txt> [replay_sensor0.jsonl replay_sensor1.jsonl ...]
//
// 1) 줄 파서: tof / tof_hello / tof_err / 쓰레기
// 2) PostureNet: export.py 의 golden.txt (PyTorch logits) 와 비교, 프레임당 시간
// 3) 리플레이(있으면): 시뮬 세션을 두 센서 파일로 넣어 DevicePosture 전체 경로를 가상 시계로 돌리고
//    파일의 "label" 과 비교 (dataset/tof_sim_v5/tools/sim_to_toflines.py 가 만든 파일)
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "device_posture.h"
#include "posture_net.h"
#include "tof_line.h"

namespace {

int failures = 0;
std::string bin_for_tests;

void check(bool ok, const char* what) {
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) ++failures;
}

void testParser() {
    tof::TofLine l;
    std::string d, st;
    for (int i = 0; i < 64; ++i) { d += (i ? "," : "") + std::to_string(100 + i); st += (i ? "," : "") + std::to_string(i % 2 ? 5 : 255); }
    const std::string frame = "{\"t\":\"tof\",\"seq\":12,\"ms\":3456,\"w\":8,\"h\":8,\"d\":[" + d + "],\"st\":[" + st +
                              "],\"rule\":{\"present\":true,\"posture\":\"upright\",\"conf\":0.80,\"nods\":0}}\n";
    check(tof::parseTofLine(frame, l) && l.kind == tof::LineKind::Frame, "parse tof frame");
    check(l.frame.seq == 12 && l.frame.ms == 3456 && l.frame.w == 8 && l.frame.h == 8, "frame header fields");
    check(l.frame.d[0] == 100 && l.frame.d[63] == 163 && l.frame.st[0] == 255 && l.frame.st[1] == 5, "frame arrays");
    check(l.frame.label.empty(), "frame without label");
    const std::string labelled = "{\"t\":\"tof\",\"seq\":1,\"ms\":0,\"w\":8,\"h\":8,\"d\":[" + d + "],\"st\":[" + st +
                                 "],\"rule\":{},\"label\":\"drowsy\"}";
    check(tof::parseTofLine(labelled, l) && l.frame.label == "drowsy", "frame with label");
    check(tof::parseTofLine("{\"t\":\"tof_hello\",\"ms\":5,\"sensor\":\"VL53L8CX\",\"w\":8,\"h\":8,\"hz\":15,"
                            "\"flip_rows\":false,\"flip_cols\":true,\"target_order\":\"closest\",\"fw\":1}", l) &&
              l.kind == tof::LineKind::Hello && l.hello.hz == 15 && l.hello.flip_cols && !l.hello.flip_rows &&
              l.hello.sensor == "VL53L8CX", "parse tof_hello");
    check(tof::parseTofLine("{\"t\":\"tof_err\",\"ms\":9,\"where\":\"init\",\"code\":-3}", l) &&
              l.kind == tof::LineKind::Error && l.err_where == "init" && l.err_code == -3, "parse tof_err");
    check(!tof::parseTofLine("ets Jul 29 2019 12:21:46", l), "reject boot log");
    check(!tof::parseTofLine("{\"t\":\"tof\",\"seq\":1,\"ms\":0,\"w\":8,\"h\":8,\"d\":[1,2,3],\"st\":[5,5,5]}", l),
          "reject short arrays");
    check(!tof::parseTofLine("{\"t\":\"tof\",\"seq\":1,\"ms\":0,\"w\":8,\"h\":8,\"d\":[" + d, l), "reject truncated line");
    uint32_t ms = 0;
    check(tof::peekLineMs(frame, ms) && ms == 3456, "peek ms");

    // bring-up CSV 형식
    std::string f64 = "F,12345";
    for (int i = 0; i < 64; ++i) f64 += "," + std::to_string(i == 3 ? 0 : 500 + i);
    check(tof::parseTofLine(f64 + "\r\n", l) && l.kind == tof::LineKind::Frame && l.frame.ms == 12345 &&
              l.frame.seq == 12345 && l.frame.w == 8 && !l.frame.has_status && l.frame.d[5] == 505 &&
              l.frame.st[5] == 5 && l.frame.d[3] == 0 && l.frame.st[3] == 255, "parse bring-up F line (64, no status)");
    std::string f128 = f64;
    for (int i = 0; i < 64; ++i) f128 += "," + std::to_string(i % 4 ? 5 : 255);
    check(tof::parseTofLine(f128, l) && l.kind == tof::LineKind::Frame && l.frame.has_status && l.frame.st[0] == 255 &&
              l.frame.st[1] == 5 && l.frame.d[63] == 563, "parse bring-up F line (64 dist + 64 status)");
    check(!tof::parseTofLine("F,12345,1,2,3", l), "reject short F line");
    check(tof::parseTofLine("P,12346,1,chin_rest,0.83,0.10,x,y,3,0.5,2,a,b,c,100,1.5,0.2,1,2,d,e,0.3,4,5,6", l) &&
              l.kind == tof::LineKind::Rule && l.rule_posture == "chin_rest", "parse bring-up P line");
    check(tof::parseTofLine("Ranging started (8x8 @ 15 Hz)", l) && l.kind == tof::LineKind::Hello && l.hello.hz == 15,
          "bring-up 'Ranging started' as hello");
    check(tof::parseTofLine("# rows flipped, estimator restarted", l) && l.kind == tof::LineKind::Info, "bring-up # info");
    check(tof::parseTofLine("[ERR] start_ranging failed (status=3)", l) && l.kind == tof::LineKind::Error, "bring-up [ERR]");
    check(tof::peekLineMs(f64, ms) && ms == 12345, "peek ms from F line");
    check(tof::looksLikeTofLine(f64) && tof::looksLikeTofLine(frame) && !tof::looksLikeTofLine("ets Jul 29 2019"),
          "looksLikeTofLine");

    // tof_live.py --record 가 감싼 줄 (2026-10-15 형식)
    const std::string wrapped_csv = "{\"host_t\": 1792050000.1234, \"port\": \"COM5\", \"raw\": \"" + f64 + "\"}";
    std::string esc;                                           // JSON 문자열로 이스케이프한 tof_logger 줄
    for (char c : frame.substr(0, frame.size() - 1)) { if (c == '"' || c == '\\') esc += '\\'; esc += c; }
    const std::string wrapped_json = "{\"host_t\": 1792050000.2, \"port\": \"COM6\", \"raw\": \"" + esc + "\"}";
    std::string inner, port;
    check(tof::unwrapRecordLine(wrapped_csv, inner, port) && port == "COM5" && inner == f64, "unwrap record line (CSV)");
    check(tof::parseTofLine(wrapped_csv, l) && l.kind == tof::LineKind::Frame && l.frame.ms == 12345 && l.frame.d[5] == 505,
          "parse wrapped CSV frame");
    check(tof::parseTofLine(wrapped_json, l) && l.kind == tof::LineKind::Frame && l.frame.seq == 12 && l.frame.d[63] == 163,
          "parse wrapped JSON frame");
    check(tof::peekLineMs(wrapped_json, ms) && ms == 3456, "peek ms from wrapped line");
    check(!tof::unwrapRecordLine(f64, inner, port), "plain line is not wrapped");

    // UART2 TYPE 0x05 (hub/atlas 가 넘기는 줄): payload v1 200 B
    auto uartLine = [](int sensor_id, int flags, int frame_seq, unsigned ts) {
        std::vector<uint8_t> p = {1, (uint8_t)sensor_id, (uint8_t)flags, 8, 8, 0, (uint8_t)(frame_seq & 255), (uint8_t)(frame_seq >> 8)};
        for (int i = 0; i < 64; ++i) {
            const unsigned dmm = i == 7 ? 0xFFFF : i == 9 ? 0 : 600 + i;
            p.push_back(dmm & 255); p.push_back(dmm >> 8);
        }
        for (int i = 0; i < 64; ++i) p.push_back(i % 8 == 1 ? 4 : 5);
        std::string hex;
        char b[3];
        for (uint8_t v : p) { std::snprintf(b, sizeof b, "%02x", v); hex += b; }
        return "UART\t{\"type\":5,\"seq\":77,\"ts_ms\":" + std::to_string(ts) + ",\"payload_hex\":\"" + hex + "\"}\n";
    };
    check(tof::parseTofLine(uartLine(1, 3, 513, 98765), l) && l.kind == tof::LineKind::Frame && l.sensor_id == 1 &&
              l.frame.seq == 513 && l.frame.ms == 98765 && l.frame.has_status && l.frame.d[0] == 600 && l.frame.d[63] == 663 &&
              l.frame.d[7] == -1 && l.frame.d[9] == -1 && l.frame.st[1] == 4 && l.frame.st[2] == 5 && l.frame.seq16 &&
              l.frame.flip_rows_hint == 0 && l.frame.flip_cols_hint == 0, "parse UART 0x05 frame (dist 0 and 0xFFFF -> none)");
    check(tof::parseTofLine(uartLine(1, 3 | 8, 5, 1), l) && l.frame.flip_cols_hint == 1 && l.frame.flip_rows_hint == 0,
          "flags b3 = flip_cols_needed");
    check(tof::parseTofLine(uartLine(0, 1, 1, 5), l) && l.sensor_id == 0 && !l.frame.has_status && l.frame.st[1] == 5 &&
              l.frame.st[7] == 255, "UART 0x05 without status (has_status=0)");
    check(tof::parseTofLine(uartLine(0, 2, 41, 5), l) && l.kind == tof::LineKind::Idle && l.sensor_id == 0 &&
              l.frame.seq == 41 && l.frame.seq16, "UART 0x05 valid=0 -> Idle (keeps frame_seq)");

    // 교차 벡터 (펌웨어 골든·팀 uart_frame.py·제안서 인코더가 바이트 일치: payload sha256 f0ed0f4c…, wire 215 B b0eae6ec…)
    // dist[z] = -1(전송 0) if z 홀수 else 200+13z, status[z] = 255 if 홀수 else (5 if z%4==0 else 9),
    // sensor_id 1, valid·has_status, flip_rows 0, flip_cols 1, frame_seq 0x1234. envelope SEQ 7, TS_MS 1000.
    {
        const std::string xv = "UART\t{\"type\":5,\"seq\":7,\"ts_ms\":1000,\"payload_hex\":\"01010b0808003412c8000000e2000000fc00000016010000300100004a010000640100007e01000098010000b2010000cc010000e6010000000200001a020000340200004e02000068020000820200009c020000b6020000d0020000ea020000040300001e03000038030000520300006c03000086030000a0030000ba030000d4030000ee03000005ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff05ff09ff\"}";
        tof::TofLine x;
        bool fields = tof::parseTofLine(xv, x) && x.kind == tof::LineKind::Frame && x.sensor_id == 1 &&
                      x.frame.seq == 0x1234 && x.frame.ms == 1000 && x.frame.has_status &&
                      x.frame.flip_rows_hint == 0 && x.frame.flip_cols_hint == 1;
        bool zones = fields;
        for (int z = 0; z < 64 && zones; ++z) {
            const bool odd = z % 2;
            zones = odd ? (x.frame.d[z] == -1 && x.frame.st[z] == 255 && !tof::zoneValid(x.frame.d[z], x.frame.st[z]))
                        : (x.frame.d[z] == 200 + 13 * z && x.frame.st[z] == (z % 4 == 0 ? 5 : 9) &&
                           tof::zoneValid(x.frame.d[z], x.frame.st[z]));
        }
        check(fields, "cross vector: header fields (sensor 1, seq 0x1234, ts 1000, flip_cols)");
        check(zones, "cross vector: even zones valid 200+13z, odd zones none");
        tof::DevicePosture dx;
        std::string e2;
        dx.loadModel(bin_for_tests, &e2);
        const int sx = dx.addSensor("xv");
        dx.feed(sx, x, 0);
        const auto sxs = dx.sensors(0)[0];
        check(sxs.flip_cols && !sxs.flip_rows && sxs.frames == 1, "cross vector: flip_cols applied by default");
    }
    {   // frame_seq u16 순환은 리셋이 아니고, 크게 되감기면(재부팅) 리셋
        tof::DevicePosture dp;
        std::string err;
        const bool ok = dp.loadModel(bin_for_tests, &err);
        const int sidx = dp.addSensor("seqtest");
        tof::TofLine a, b, c, dup, jump, fl;
        tof::parseTofLine(uartLine(0, 3, 65530, 1000), a);
        tof::parseTofLine(uartLine(0, 3, 3, 1100), b);        // delta 9: 짧은 유실 + 0xFFFF→0 순환
        tof::parseTofLine(uartLine(0, 3, 3, 1150), dup);      // delta 0: 중복
        tof::parseTofLine(uartLine(0, 3, 1, 1200), c);        // delta 65534: 되감김
        tof::parseTofLine(uartLine(0, 3, 40, 1300), jump);    // delta 39: 큰 유실
        tof::parseTofLine(uartLine(0, 3 | 4 | 8, 41, 1400), fl);
        dp.feed(sidx, a, 0);
        dp.feed(sidx, b, 100);
        const auto st1 = dp.sensors(100)[0];
        dp.feed(sidx, dup, 110);
        const auto st2 = dp.sensors(110)[0];
        dp.feed(sidx, c, 200);
        const auto r3 = dp.sensors(200)[0].resets;
        dp.feed(sidx, jump, 300);
        const auto r4 = dp.sensors(300)[0].resets;
        dp.feed(sidx, fl, 400);
        const auto st5 = dp.sensors(400)[0];
        check(ok && st1.resets == 0 && st1.seq_gaps == 1, "frame_seq delta 9 across 0xFFFF->0: no reset, counted as gap");
        check(st2.duplicates == 1 && st2.frames == 2, "frame_seq delta 0: duplicate dropped");
        check(r3 == 1 && r4 == 2, "frame_seq rewind and jump > 10 reset the GRU");
        check(st5.flip_rows && st5.flip_cols, "auto flip follows flags b2/b3");
        tof::DeviceConfig forced;
        forced.flip_rows = 0;
        forced.flip_cols = 0;
        tof::DevicePosture dp2(forced);
        dp2.loadModel(bin_for_tests, &err);
        const int s2 = dp2.addSensor("forced");
        dp2.feed(s2, fl, 0);
        const auto st6 = dp2.sensors(0)[0];
        check(!st6.flip_rows && !st6.flip_cols, "TOFPOSTURE_FLIP_*=0 overrides flags");

        // valid=0 이 1 s 넘게: 판정에서 빠지고 GRU 리셋(stale), frame_seq 는 이어져서 돌아올 때 delta 1
        tof::DevicePosture dv;
        dv.loadModel(bin_for_tests, &err);
        const int sv = dv.addSensor("idle");
        tof::TofLine v0;
        tof::parseTofLine(uartLine(0, 3, 100, 0), v0);
        dv.feed(sv, v0, 0);
        dv.tick(0);
        const bool on0 = dv.sensors(0)[0].online;
        for (int k = 1; k <= 15; ++k) {                      // 1.5 s 동안 valid=0
            tof::TofLine idle;
            tof::parseTofLine(uartLine(0, 2, 100 + k, 0), idle);
            dv.feed(sv, idle, 100 * k);
            dv.tick(100 * k);
        }
        const auto mid = dv.sensors(1500)[0];
        tof::TofLine back;
        tof::parseTofLine(uartLine(0, 3, 116, 0), back);
        dv.feed(sv, back, 1600);
        dv.tick(1600);
        const auto end = dv.sensors(1600)[0];
        check(on0 && mid.stale && mid.alive && !mid.online && mid.idle_frames == 15 && mid.resets == 1,
              "valid=0 for 1.5 s: sensor stale, dropped from fusion, GRU reset once");
        check(end.online && !end.stale && end.resets == 1 && end.seq_gaps == 0,
              "valid=1 again: frame_seq continues (delta 1), fresh start without extra reset");
    }
    check(!tof::parseTofLine("UART\t{\"type\":32,\"seq\":1,\"ts_ms\":2,\"payload_hex\":\"0101\"}", l), "other UART types ignored");
}

bool testGolden(const std::string& bin, const std::string& golden_path) {
    tof::PostureNet net;
    std::string err;
    if (!net.load(bin, &err)) { std::printf("FAIL load model: %s\n", err.c_str()); ++failures; return false; }
    std::ifstream g(golden_path);
    if (!g) { std::printf("FAIL open golden %s\n", golden_path.c_str()); ++failures; return false; }
    std::string tok;
    int T, H, W, C, hid;
    g >> tok >> T >> H >> W >> C >> hid;
    if (tok != "golden" || H != net.H || W != net.W || C != net.classes || hid != net.hidden) {
        std::printf("FAIL golden header mismatch\n"); ++failures; return false;
    }
    const int n = H * W;
    std::vector<int16_t> dist((size_t)T * n);
    std::vector<uint8_t> status((size_t)T * n);
    std::vector<int> reset(T);
    std::vector<float> want((size_t)T * C), got(C);
    for (int t = 0; t < T; ++t) {
        int tt, v;
        g >> tok >> tt >> tok >> reset[t] >> tok;
        for (int i = 0; i < n; ++i) { g >> v; dist[(size_t)t * n + i] = (int16_t)v; }
        g >> tok;
        for (int i = 0; i < n; ++i) { g >> v; status[(size_t)t * n + i] = (uint8_t)v; }
        g >> tok;
        for (int c = 0; c < C; ++c) g >> want[(size_t)t * C + c];
    }
    double max_diff = 0.0;
    int mism = 0;
    for (int t = 0; t < T; ++t) {
        if (reset[t]) net.reset();
        net.step(&dist[(size_t)t * n], &status[(size_t)t * n], got.data());
        int a = 0, b = 0;
        for (int c = 0; c < C; ++c) {
            max_diff = std::max(max_diff, std::fabs((double)got[c] - want[(size_t)t * C + c]));
            if (got[c] > got[a]) a = c;
            if (want[(size_t)t * C + c] > want[(size_t)t * C + b]) b = c;
        }
        if (a != b) ++mism;
    }
    using clock = std::chrono::steady_clock;
    auto t0 = clock::now();
    long frames = 0;
    while (std::chrono::duration<double>(clock::now() - t0).count() < 0.5) {
        for (int t = 0; t < T; ++t) net.step(&dist[(size_t)t * n], &status[(size_t)t * n], got.data());
        frames += T;
    }
    const double us = std::chrono::duration<double>(clock::now() - t0).count() / frames * 1e6;
    std::printf("golden: %d frames, max |logit diff| %.3g, argmax mismatches %d, %.1f us/frame\n", T, max_diff, mism, us);
    const bool ok = max_diff < 1e-4 && mism == 0;
    check(ok, "PostureNet matches PyTorch golden");
    return ok;
}

struct ReplayLine { uint32_t ms; int sensor; tof::TofLine line; };

void testReplay(const std::string& bin, const std::vector<std::string>& files) {
    tof::DeviceConfig cfg;
    tof::DevicePosture device(cfg);
    std::string err;
    if (!device.loadModel(bin, &err)) { check(false, "replay: model load"); return; }
    std::vector<ReplayLine> lines;
    for (size_t s = 0; s < files.size(); ++s) {
        device.addSensor(files[s]);
        std::ifstream f(files[s]);
        if (!f) { check(false, ("replay: open " + files[s]).c_str()); return; }
        std::string raw;
        while (std::getline(f, raw)) {
            tof::TofLine l;
            if (!tof::parseTofLine(raw, l)) continue;
            uint32_t ms = l.kind == tof::LineKind::Frame ? l.frame.ms : l.hello.ms;
            lines.push_back({ms, (int)s, l});
        }
    }
    std::stable_sort(lines.begin(), lines.end(), [](const ReplayLine& a, const ReplayLine& b) { return a.ms < b.ms; });
    if (lines.empty()) { check(false, "replay: no lines"); return; }
    // 가상 시계: 줄의 ms 를 그대로 시간으로 쓰고, 10 Hz 로 tick
    const int64_t period = 1000 / cfg.rate_hz;
    int64_t next_tick = (int64_t)lines.front().ms;
    size_t i = 0;
    std::string label;
    long scored = 0, correct = 0, ticks = 0;
    std::vector<long> per_correct(files.size(), 0), per_scored(files.size(), 0);
    std::map<std::string, std::pair<long, long>> per_label;
    const int64_t end_ms = (int64_t)lines.back().ms + period;
    for (int64_t now = next_tick; now <= end_ms; now += period) {
        while (i < lines.size() && (int64_t)lines[i].ms <= now) {
            device.feed(lines[i].sensor, lines[i].line, (int64_t)lines[i].ms);
            if (lines[i].sensor == 0 && lines[i].line.kind == tof::LineKind::Frame) label = lines[i].line.frame.label;
            ++i;
        }
        if (!device.tick(now)) continue;
        ++ticks;
        if (ticks <= 45 || label.empty() || label == "transition") continue;   // 학습 채점과 같은 제외 규칙
        const tof::DeviceResult r = device.result();
        const std::string got = tof::DevicePosture::labelName(r.argmax);
        ++scored;
        correct += got == label;
        auto& pl = per_label[label];
        ++pl.second; pl.first += got == label;
        const std::vector<tof::SensorStatus> ss = device.sensors(now);
        for (size_t s = 0; s < ss.size(); ++s) {
            if (!ss[s].online) continue;
            ++per_scored[s];
            per_correct[s] += std::string(tof::DevicePosture::labelName(ss[s].argmax)) == label;
        }
    }
    std::printf("replay: %zu lines, %ld ticks, %ld scored; fused acc %.3f", lines.size(), ticks, scored,
                scored ? (double)correct / scored : 0.0);
    for (size_t s = 0; s < files.size(); ++s)
        std::printf(", sensor%zu acc %.3f", s, per_scored[s] ? (double)per_correct[s] / per_scored[s] : 0.0);
    std::printf("\n  per label:");
    for (const auto& kv : per_label)
        std::printf(" %s %.2f(%ld)", kv.first.c_str(), (double)kv.second.first / kv.second.second, kv.second.second);
    std::printf("\n");
    check(scored > 0, "replay produced scored ticks");
    std::printf("  state: %s\n", device.stateJson(end_ms).substr(0, 400).c_str());
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: %s posture_net.bin golden.txt [replay0.jsonl replay1.jsonl ...]\n", argv[0]);
        return 2;
    }
    bin_for_tests = argv[1];
    testParser();
    testGolden(argv[1], argv[2]);
    if (argc > 3) testReplay(argv[1], std::vector<std::string>(argv + 3, argv + argc));
    std::printf("%s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
