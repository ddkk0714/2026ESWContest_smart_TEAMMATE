// 호스트(x86) 테스트: 보드·센서 없이 돌아가는 부분을 검사한다.
//
//   host_test <posture_net.bin> <golden.txt> [replay_sensor0.jsonl replay_sensor1.jsonl ...]
//
// 1) 줄 파서: tof / tof_hello / tof_err / 쓰레기
// 2) PostureNet: export.py 의 golden.txt (PyTorch logits) 와 비교, 프레임당 시간
// 3) GRU 리셋 규칙 (device_posture.h "리셋"): (a) 주기 리셋이 N s 뒤 h·EMA 를 비우고 시각을 갱신, (b) coverage 미달이
//    N s 이어지면 반복 리셋·재실 한 프레임에 타이머 해제, (c) 둘 다 0 이면 DevicePosture 경로가 golden 과 같다(회귀 없음)
// 4) GET /raw: uart_relay_board 스키마(두 센서 64칸·have·id=물리 번호·받은 그대로), HttpServer 리스너로 실제 GET
// 5) 리플레이(있으면): 시뮬 세션을 두 센서 파일로 넣어 DevicePosture 전체 경로를 가상 시계로 돌리고
//    파일의 "label" 과 비교 (dataset/tof_sim_v5/tools/sim_to_toflines.py 가 만든 파일). 기본 설정과 리셋 끔 두 번. 끝에 /raw 확인
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "device_posture.h"
#include "http_server.h"
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
    {   // 표준 배치(data-spec §13.1) — 펌웨어 test_tof_payload 의 골든과 같은 hex. 실기 ESP32 가 보내는 것.
        const std::string g0 =
            "080800022a030200ffffc501c801cb01ce01d101d401d701da01dd01e001ffffe601e901ec01ef01f201f501f801fb01fe010102"
            "ffff07020a020d0210021302160219021c021f022202ffff28022b022e023102340237023a023d0240024302ffff49024c024f02"
            "5202550258025b025e0261026402ffff6a026d0270027302760279027c027f02ff09060005090600050906ff0509060005090600"
            "0509ff00050906000509060005ff06000509060005090600ff09060005090600050906ff0509060005090600";
        const std::string g1 =
            "080801022a03020082028502ffff8b028e029102940297029a029d02a002a302a602ffffac02af02b202b502b802bb02be02c102"
            "c402c702ffffcd02d002d302d602d902dc02df02e202e502e802ffffee02f102f402f702fa02fd020003030306030903ffff0f03"
            "1203150318031b031e032103240327032a03ffff30033303360339033c033f030509ff00050906000509060005ff060005090600"
            "05090600ff09060005090600050906ff05090600050906000509ff00050906000509060005ff060005090600";
        auto std_line = [](const std::string& hex, unsigned ts) {
            return "UART\t{\"type\":5,\"seq\":9,\"ts_ms\":" + std::to_string(ts) + ",\"payload_hex\":\"" + hex + "\"}";
        };
        static const uint8_t kSt[4] = {5, 9, 6, 0};
        auto zones_ok = [&](const tof::TofLine& x, int base) {
            for (int z = 0; z < 64; ++z) {
                const int i = base + z;
                if (i % 11 == 0) { if (x.frame.d[z] != -1 || x.frame.st[z] != 255) return false; }
                else if (x.frame.d[z] != 450 + 3 * i || x.frame.st[z] != kSt[i % 4]) return false;
            }
            return true;
        };
        tof::TofLine a, b, sp;
        check(tof::parseTofLine(std_line(g0, 1234), a) && a.kind == tof::LineKind::Frame && a.sensor_id == 0 &&
                  a.frame.w == 8 && a.frame.h == 8 && a.frame.seq == 0x2A && a.frame.seq16 && a.frame.seq_mask == 0xFF &&
                  a.frame.ms == 1234 && a.frame.has_status && a.frame.flip_rows_hint == 0 && a.frame.flip_cols_hint == 0,
              "standard 0x05: header (sensor 0, frame_id 0x2A, flags are already applied -> no flip hint)");
        check(zones_ok(a, 0), "standard 0x05: golden part 0 zones (-1/255 every 11th, else 450+3i)");
        check(tof::parseTofLine(std_line(g1, 1234), b) && b.sensor_id == 1 && zones_ok(b, 64),
              "standard 0x05: golden part 1 (sensor 1)");
        const std::string spaced = "UART\t{\"type\": 5, \"seq\": 9, \"ts_ms\": 77, \"payload_hex\": \"" + g0 + "\"}";
        check(tof::parseTofLine(spaced, sp) && sp.kind == tof::LineKind::Frame && sp.frame.ms == 77 && zones_ok(sp, 0),
              "UART line with spaces after ':' (json.dumps default)");
        std::string bad = g0;
        bad[10] = '0'; bad[11] = '1';                       // sensor_mask 0x01 이지만 sensor 0 은 그대로 → 되고
        tof::TofLine m;
        check(tof::parseTofLine(std_line(bad, 1), m), "standard 0x05: sensor bit set in mask is accepted");
        bad = g1; bad[10] = '0'; bad[11] = '1';             // sensor 1 인데 mask 에 b1 없음 → 거절
        check(!tof::parseTofLine(std_line(bad, 1), m), "standard 0x05: sensor not in sensor_mask is rejected");

        // frame_id u8 순환: 250 → 3 (delta 9) 은 유실, 같은 값은 중복, 3 → 100 은 리셋. 기본 설정에서 안 뒤집는다.
        auto with_fid = [&](int fid) { std::string h = g0; char c[3]; std::snprintf(c, sizeof c, "%02x", fid & 255);
                                       h[8] = c[0]; h[9] = c[1]; return std_line(h, 1); };
        tof::DevicePosture dq;
        std::string e3;
        dq.loadModel(bin_for_tests, &e3);
        const int sq = dq.addSensor("std");
        tof::TofLine f1, f2, f3, f4;
        tof::parseTofLine(with_fid(250), f1);
        tof::parseTofLine(with_fid(3), f2);
        tof::parseTofLine(with_fid(3), f3);
        tof::parseTofLine(with_fid(100), f4);
        dq.feed(sq, f1, 0);
        dq.feed(sq, f2, 100);
        const auto q1 = dq.sensors(100)[0];
        dq.feed(sq, f3, 110);
        const auto q2 = dq.sensors(110)[0];
        dq.feed(sq, f4, 200);
        const auto q3 = dq.sensors(200)[0];
        check(q1.resets == 0 && q1.seq_gaps == 1 && !q1.flip_cols && !q1.flip_rows,
              "standard 0x05: frame_id 250->3 wraps as a gap, no reset, no flip by default");
        check(q2.duplicates == 1 && q3.resets == 1, "standard 0x05: same frame_id is a duplicate, jump > 10 resets");
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

struct Golden {
    int T = 0, H = 0, W = 0, C = 0, hid = 0;
    std::vector<int16_t> dist;
    std::vector<uint8_t> status;
    std::vector<int> reset;
    std::vector<float> want;    // PyTorch logits, T×C
};

bool loadGolden(const std::string& golden_path, const tof::PostureNet& net, Golden& gd) {
    std::ifstream g(golden_path);
    if (!g) { std::printf("FAIL open golden %s\n", golden_path.c_str()); ++failures; return false; }
    std::string tok;
    g >> tok >> gd.T >> gd.H >> gd.W >> gd.C >> gd.hid;
    if (tok != "golden" || gd.H != net.H || gd.W != net.W || gd.C != net.classes || gd.hid != net.hidden) {
        std::printf("FAIL golden header mismatch\n"); ++failures; return false;
    }
    const int n = gd.H * gd.W, T = gd.T, C = gd.C;
    gd.dist.assign((size_t)T * n, 0);
    gd.status.assign((size_t)T * n, 0);
    gd.reset.assign(T, 0);
    gd.want.assign((size_t)T * C, 0.f);
    for (int t = 0; t < T; ++t) {
        int tt, v;
        g >> tok >> tt >> tok >> gd.reset[t] >> tok;
        for (int i = 0; i < n; ++i) { g >> v; gd.dist[(size_t)t * n + i] = (int16_t)v; }
        g >> tok;
        for (int i = 0; i < n; ++i) { g >> v; gd.status[(size_t)t * n + i] = (uint8_t)v; }
        g >> tok;
        for (int c = 0; c < C; ++c) g >> gd.want[(size_t)t * C + c];
    }
    return true;
}

bool testGolden(const std::string& bin, const std::string& golden_path) {
    tof::PostureNet net;
    std::string err;
    if (!net.load(bin, &err)) { std::printf("FAIL load model: %s\n", err.c_str()); ++failures; return false; }
    Golden gd;
    if (!loadGolden(golden_path, net, gd)) return false;
    const int T = gd.T, C = gd.C, n = gd.H * gd.W;
    std::vector<int16_t>& dist = gd.dist;
    std::vector<uint8_t>& status = gd.status;
    std::vector<int>& reset = gd.reset;
    std::vector<float>& want = gd.want;
    std::vector<float> got(C);
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

// 8×8 프레임 하나: 앞 `present_zones` 칸은 600 mm(재실 범위 안), 나머지는 2000 mm(모델에는 유효, 재실 범위 밖). 상태값 5.
tof::TofLine makeFrame(uint32_t seq, uint32_t ms, int present_zones, int present_mm = 600, uint8_t present_status = 5) {
    tof::TofLine l;
    l.kind = tof::LineKind::Frame;
    l.frame.seq = seq; l.frame.ms = ms; l.frame.w = 8; l.frame.h = 8; l.frame.has_status = true;
    for (int i = 0; i < 64; ++i) {
        const bool p = i < present_zones;
        l.frame.d[i] = (int16_t)(p ? present_mm : 2000);
        l.frame.st[i] = p ? present_status : 5;
    }
    return l;
}

bool smoothEqualsInstant(const tof::DeviceResult& r, int C) {
    for (int i = 0; i < C; ++i) if (r.smooth[i] != r.probs[i]) return false;
    return true;
}

// (a) 주기 리셋: reset_period_s=2 — 사람이 계속 앉아 있어도 2 s 마다 h·EMA 를 비우고 마지막 리셋 시각이 갱신된다
void testPeriodicReset(const std::string& bin) {
    tof::DeviceConfig cfg;
    cfg.reset_period_s = 2;
    cfg.away_reset_s = 0;
    tof::DevicePosture d(cfg);
    std::string err;
    if (!d.loadModel(bin, &err)) { check(false, "periodic: model load"); return; }
    const int C = d.classes();
    const int s0 = d.addSensor("p0"), s1 = d.addSensor("p1");
    tof::DeviceResult at19, at20, at21, at40;
    for (int k = 0; k <= 40; ++k) {
        const int64_t now = 100 * k;
        d.feed(s0, makeFrame(k + 1, (uint32_t)now, 20), now);
        d.feed(s1, makeFrame(k + 1, (uint32_t)now, 12), now);
        d.tick(now);
        if (k == 19) at19 = d.result();
        if (k == 20) at20 = d.result();
        if (k == 21) at21 = d.result();
        if (k == 40) at40 = d.result();
    }
    const auto ss = d.sensors(4000);
    check(at19.resets.periodic == 0 && at19.resets.last_reset_ms == 0 && at19.present_rule,
          "periodic: no reset before 2 s (last_reset = start), person present by coverage");
    check(at20.resets.periodic == 1 && at20.resets.last_reset_ms == 2000 && at20.resets.away == 0 && at20.resets.manual == 0,
          "periodic: reset at 2 s, last_reset_ms updated");
    check(smoothEqualsInstant(at20, C) && at20.label_since_ms == 2000 && at20.valid && at20.smooth_argmax >= 0,
          "periodic: EMA cleared (smooth == instant on the reset step, label restarted), no warm-up");
    check(!smoothEqualsInstant(at21, C) || at21.smooth_argmax == at20.smooth_argmax, "periodic: EMA runs again after reset");
    check(at40.resets.periodic == 2 && at40.resets.last_reset_ms == 4000, "periodic: repeats every 2 s from the last reset");
    check(ss.size() == 2 && ss[0].resets == 2 && ss[1].resets == 2, "periodic: every sensor GRU reset twice");
    // 수동 리셋도 마지막 리셋 시각을 갱신해 주기 타이머를 다시 시작한다
    d.reset(4100);
    for (int k = 41; k <= 61; ++k) {
        const int64_t now = 100 * k;
        d.feed(s0, makeFrame(k + 1, (uint32_t)now, 20), now);
        d.feed(s1, makeFrame(k + 1, (uint32_t)now, 12), now);
        d.tick(now);
    }
    const auto r61 = d.result();
    check(r61.resets.manual == 1 && r61.resets.periodic == 3 && r61.resets.last_reset_ms == 6100,
          "periodic: manual reset restarts the period (next periodic at manual + 2 s)");
}

// (b) 자리 비움 리셋: away_reset_s=1 — coverage 미달이 1 s 이어지면 리셋, 비어 있는 동안 1 s 마다 반복, 재실 한 프레임에 타이머 해제
void testAwayReset(const std::string& bin) {
    tof::DeviceConfig cfg;
    cfg.reset_period_s = 0;
    cfg.away_reset_s = 1;
    tof::DevicePosture d(cfg);
    std::string err;
    if (!d.loadModel(bin, &err)) { check(false, "away: model load"); return; }
    const int s0 = d.addSensor("a0"), s1 = d.addSensor("a1");
    // coverage 셈: 4칸/64 = 0.0625 ≥ 0.06 재실, 3칸 미달, 범위 밖 거리·상태 4 는 안 센다
    d.feed(s0, makeFrame(1, 0, 4), 0);
    d.feed(s1, makeFrame(1, 0, 3), 0);
    auto cs = d.sensors(0);
    check(std::fabs(cs[0].coverage - 0.0625f) < 1e-6f && std::fabs(cs[1].coverage - 3.f / 64.f) < 1e-6f, "away: coverage = zones in range / 64");
    d.feed(s0, makeFrame(2, 100, 4, 140), 100);
    d.feed(s1, makeFrame(2, 100, 4, 1400), 100);
    cs = d.sensors(100);
    check(cs[0].coverage == 0.f && cs[1].coverage == 0.f, "away: 140 mm and 1400 mm zones do not count");
    d.feed(s0, makeFrame(3, 200, 4, 600, 4), 200);
    check(d.sensors(200)[0].coverage == 0.f, "away: status 4 zones do not count");

    tof::DevicePosture e(cfg);
    e.loadModel(bin, &err);
    const int e0 = e.addSensor("e0"), e1 = e.addSensor("e1");
    std::map<int, tof::DeviceResult> at;
    auto run = [&](int k_from, int k_to, int zones0, int zones1) {
        for (int k = k_from; k <= k_to; ++k) {
            const int64_t now = 100 * k;
            e.feed(e0, makeFrame(k + 1, (uint32_t)now, zones0), now);
            e.feed(e1, makeFrame(k + 1, (uint32_t)now, zones1), now);
            e.tick(now);
            at[k] = e.result();
        }
    };
    run(0, 25, 0, 3);                 // 두 센서 모두 미달 (최대 3/64) → 1 s, 2 s 에 리셋
    check(!at[0].present_rule && at[9].resets.away == 0, "away: no reset before 1 s of absence");
    check(at[10].resets.away == 1 && at[10].resets.last_reset_ms == 1000 && at[10].resets.periodic == 0,
          "away: reset after 1 s without presence (coverage, not model output)");
    check(smoothEqualsInstant(at[10], e.classes()) && at[10].label_since_ms == 1000, "away: EMA cleared on the reset step");
    check(at[19].resets.away == 1 && at[20].resets.away == 2 && at[20].resets.last_reset_ms == 2000,
          "away: repeats every 1 s while the seat stays empty");
    run(26, 26, 0, 4);                // 한 프레임 재실(4/64 한쪽만) → 타이머 해제
    check(at[26].present_rule && at[26].resets.away == 2, "away: one present frame (max of the two sensors) clears the timer");
    run(27, 35, 0, 0);                // 다시 비움: 2.7 s 부터 → 3.6 s 에는 아직, 3.7 s 에 리셋
    check(at[35].resets.away == 2, "away: timer restarts from the first absent frame (0.9 s: no reset yet)");
    run(36, 37, 0, 0);
    check(at[37].resets.away == 3 && at[37].resets.last_reset_ms == 3700, "away: reset 1 s after absence resumed");
    // 프레임이 끊기면: stale_ms(1 s) 안에서는 마지막 프레임을 재사용하므로 4.7 s 에 한 번 더 리셋되고, 그 뒤 센서가
    // 전부 offline 이면 세지 않는다 — 상태는 이미 비어 있다
    for (int k = 38; k <= 80; ++k) e.tick(100 * k);
    const auto off = e.result();
    check(!off.valid && off.resets.away == 4 && off.resets.last_reset_ms == 4700 && !off.present_rule,
          "away: one more reset while the last frame is reused (<= stale_ms), none once all sensors are offline");
}

// (c) 둘 다 0: DevicePosture(센서 1개) 경로의 센서별 확률이 golden 의 softmax(logits) 와 같다. 기본값(주기 0 / 자리 비움 60)도
//     240 프레임(24 s, 사람 있음)에서는 같다. 기본값 자체도 검사한다(주기 0 = 끔, 자리 비움 60 s)
void testDevicePathMatchesGolden(const std::string& bin, const std::string& golden_path) {
    tof::PostureNet probe;
    std::string err;
    if (!probe.load(bin, &err)) { check(false, "device-golden: model load"); return; }
    Golden gd;
    if (!loadGolden(golden_path, probe, gd)) return;
    const int n = gd.H * gd.W, C = gd.C;
    for (int pass = 0; pass < 2; ++pass) {
        tof::DeviceConfig cfg;
        if (pass == 0) { cfg.reset_period_s = 0; cfg.away_reset_s = 0; }
        tof::DevicePosture d(cfg);
        d.loadModel(bin, &err);
        const int s = d.addSensor("g");
        double max_diff = 0.0;
        int mism = 0;
        for (int t = 0; t < gd.T; ++t) {
            const int64_t now = 100 * t;
            if (gd.reset[t] && t > 0) {                      // golden 의 리셋 지점 = 센서 재시작(hello)
                tof::TofLine hello;
                hello.kind = tof::LineKind::Hello;
                hello.hello.ms = (uint32_t)now; hello.hello.w = 8; hello.hello.h = 8; hello.hello.hz = 10;
                d.feed(s, hello, now);
            }
            tof::TofLine l;
            l.kind = tof::LineKind::Frame;
            l.frame.seq = t + 1; l.frame.ms = (uint32_t)now; l.frame.w = gd.W; l.frame.h = gd.H; l.frame.has_status = true;
            for (int i = 0; i < n; ++i) { l.frame.d[i] = gd.dist[(size_t)t * n + i]; l.frame.st[i] = gd.status[(size_t)t * n + i]; }
            d.feed(s, l, now);
            d.tick(now);
            const auto st = d.sensors(now)[0];
            std::vector<float> wp(C);
            tof::softmax(&gd.want[(size_t)t * C], C, wp.data());
            int a = 0, b = 0;
            for (int c = 0; c < C; ++c) {
                max_diff = std::max(max_diff, std::fabs((double)st.probs[c] - wp[c]));
                if (st.probs[c] > st.probs[a]) a = c;
                if (wp[c] > wp[b]) b = c;
            }
            if (a != b) ++mism;
        }
        const auto r = d.result();
        std::printf("device-golden (%s): max |prob diff| %.3g, argmax mismatches %d, resets periodic %llu away %llu\n",
                    pass == 0 ? "resets off" : "defaults 0/60", max_diff, mism,
                    (unsigned long long)r.resets.periodic, (unsigned long long)r.resets.away);
        check(max_diff < 1e-4 && mism == 0 && r.resets.periodic == 0 && r.resets.away == 0 && r.resets.manual == 0,
              pass == 0 ? "resets off: DevicePosture path matches golden (no regression)"
                        : "defaults: DevicePosture path matches golden over 24 s (no reset fired)");
    }
    const tof::DeviceConfig def;
    check(def.reset_period_s == 0 && def.away_reset_s == 60 && def.present_min_mm == 150 && def.present_max_mm == 1300 &&
              std::fabs(def.present_min_coverage - 0.06f) < 1e-6f,
          "defaults: periodic reset off (0), away reset 60 s, presence 150-1300 mm, coverage 0.06");
    // 기본값으로 15 분 이상 앉아 있어도 주기 리셋은 걸리지 않는다 (자리 비움 리셋만 켜져 있음)
    tof::DevicePosture d(def);
    d.loadModel(bin, &err);
    const int s = d.addSensor("long");
    for (int k = 0; k <= 9100; ++k) {                      // 910 s
        const int64_t now = 100 * k;
        d.feed(s, makeFrame(k + 1, (uint32_t)now, 20), now);
        d.tick(now);
    }
    const auto r = d.result();
    check(r.resets.periodic == 0 && r.resets.away == 0 && r.resets.last_reset_ms == 0,
          "defaults: 910 s seated -> no periodic reset (off), no away reset (present)");
}

// UART 0x05 v1 줄: 거리 base+i (i==7 은 0xFFFF, i==9 는 0 → 없음), 상태 i%8==1 이면 4 아니면 5
std::string uartV1Line(int sensor_id, int flags, int frame_seq, unsigned ts, unsigned envelope_seq, int base = 600) {
    std::vector<uint8_t> p = {1, (uint8_t)sensor_id, (uint8_t)flags, 8, 8, 0, (uint8_t)(frame_seq & 255), (uint8_t)(frame_seq >> 8)};
    for (int i = 0; i < 64; ++i) {
        const unsigned dmm = i == 7 ? 0xFFFF : i == 9 ? 0 : (unsigned)(base + i);
        p.push_back(dmm & 255); p.push_back(dmm >> 8);
    }
    for (int i = 0; i < 64; ++i) p.push_back(i % 8 == 1 ? 4 : 5);
    std::string hex;
    char b[3];
    for (uint8_t v : p) { std::snprintf(b, sizeof b, "%02x", v); hex += b; }
    return "UART\t{\"type\":5,\"seq\":" + std::to_string(envelope_seq) + ",\"ts_ms\":" + std::to_string(ts) +
           ",\"payload_hex\":\"" + hex + "\"}";
}

// JSON 문자열에서 "key":[...] 의 원소 개수와 첫 원소 (라이브러리 없이)
int jsonArrayCount(const std::string& j, const std::string& key, size_t from, long* first = nullptr) {
    const size_t k = j.find("\"" + key + "\":[", from);
    if (k == std::string::npos) return -1;
    const size_t a = j.find('[', k), b = j.find(']', a);
    if (a == std::string::npos || b == std::string::npos) return -1;
    const std::string body = j.substr(a + 1, b - a - 1);
    if (body.empty()) return 0;
    if (first) *first = std::atol(body.c_str());
    return (int)std::count(body.begin(), body.end(), ',') + 1;
}

// 4) /raw JSON: 중계기 스키마, 센서 id = 물리 번호, 격자는 받은 그대로(뒤집기 전), 없는 센서 have=false
void testRawJson(const std::string& bin) {
    tof::DeviceConfig cfg;
    cfg.flip_cols = 1;                                       // 모델 입력은 좌우 뒤집되 /raw 는 받은 그대로여야 한다
    tof::DevicePosture d(cfg);
    std::string err;
    d.loadModel(bin, &err);
    d.setDeviceLabel("tcp:9100");
    const int slot = d.addSensor("tcp:9100");                // main.cpp 처럼 한 입력에 두 센서: 두 번째는 자리를 새로 만든다
    const std::string j0 = d.rawJson(0);
    check(j0.find("\"dev\":\"tcp:9100\",\"frames\":0") != std::string::npos &&
              j0.find("{\"id\":0,\"have\":false}") != std::string::npos && j0.find("{\"id\":1,\"have\":false}") != std::string::npos,
          "raw: before any frame both sensors have=false, relay-style header");
    tof::TofLine l1, l0;
    tof::parseTofLine(uartV1Line(1, 3 | 8, 300, 5000, 77, 700), l1);   // 물리 센서 1 (B) 이 먼저 온다 → 자리 0
    d.feed(slot, l1, 1000);
    const int slot2 = d.addSensor("tcp:9100#s0");
    tof::parseTofLine(uartV1Line(0, 3, 41, 5100, 78, 600), l0);        // 물리 센서 0 (A) → 자리 1
    d.feed(slot2, l0, 1100);
    const std::string j = d.rawJson(1300);
    const size_t i0 = j.find("{\"id\":0,"), i1 = j.find("{\"id\":1,");
    check(i0 != std::string::npos && i1 != std::string::npos && i0 < i1, "raw: sensors listed as id 0 then id 1");
    long first_d = 0, first_s = 0;
    const int n_d0 = jsonArrayCount(j, "dist_mm", i0, &first_d), n_s0 = jsonArrayCount(j, "status", i0, &first_s);
    check(n_d0 == 64 && n_s0 == 64 && first_d == 600 && first_s == 5, "raw: id 0 = physical sensor 0 (A), 64 dist + 64 status, as received");
    long first_d1 = 0;
    const int n_d1 = jsonArrayCount(j, "dist_mm", i1, &first_d1);
    check(n_d1 == 64 && first_d1 == 700, "raw: id 1 = physical sensor 1 (B) even though it arrived first (slot 0)");
    check(j.find("\"id\":0,\"have\":true,\"age_ms\":200,\"uart_seq\":78,\"ts_ms\":5100,\"frame_id\":0,\"frame_seq\":41,\"mask\":0,\"flags\":3,") != std::string::npos,
          "raw: id 0 header fields (age from last frame, envelope seq, ts_ms, frame_seq, flags byte)");
    check(j.find("\"frames\":2,\"type5\":2,\"sent\":2,\"bad\":0") != std::string::npos, "raw: frames/type5/sent count received frames");
    // 받은 그대로: 0xFFFF·0 은 -1 (중계기와 같게), 상태는 그대로 4/5. 모델 입력(sensors[].flip_cols) 은 뒤집혀 있어도 raw 는 안 뒤집힘
    const size_t d0 = j.find("\"dist_mm\":[", i0);
    const std::string arr = j.substr(d0, j.find(']', d0) - d0);
    check(arr.find("606,-1,608,-1,610") != std::string::npos, "raw: 0xFFFF and 0 distances are -1 like the relay, order as received (not flipped)");
    check(d.sensors(1300)[0].flip_cols, "raw: model input is still flipped by TOFPOSTURE_FLIP_COLS");
    // 표준 배치(펌웨어 TofPayload) 도: i16 -1 그대로, frame_id·mask·flags 채워짐
    const std::string g0 =
        "080800022a030200ffffc501c801cb01ce01d101d401d701da01dd01e001ffffe601e901ec01ef01f201f501f801fb01fe010102"
        "ffff07020a020d0210021302160219021c021f022202ffff28022b022e023102340237023a023d0240024302ffff49024c024f02"
        "5202550258025b025e0261026402ffff6a026d0270027302760279027c027f02ff09060005090600050906ff0509060005090600"
        "0509ff00050906000509060005ff06000509060005090600ff09060005090600050906ff0509060005090600";
    tof::TofLine sl;
    tof::parseTofLine("UART\t{\"type\":5,\"seq\":9,\"ts_ms\":1234,\"payload_hex\":\"" + g0 + "\"}", sl);
    tof::DevicePosture e;
    e.loadModel(bin, &err);
    e.feed(e.addSensor("std"), sl, 10);
    const std::string js = e.rawJson(20);
    check(js.find("\"id\":0,\"have\":true,\"age_ms\":10,\"uart_seq\":9,\"ts_ms\":1234,\"frame_id\":42,\"frame_seq\":42,\"mask\":3,\"flags\":2,\"flip_rows\":false,\"flip_cols\":true,\"w\":8,\"h\":8,\"dist_mm\":[-1,453,456") != std::string::npos,
          "raw: standard layout keeps i16 -1, frame_id 0x2A, mask 3, flags 2 -> flip_cols like the relay");
    check(js.find("{\"id\":1,\"have\":false}") != std::string::npos, "raw: missing physical sensor 1 -> have=false");
}

// 4') HttpServer 로 실제 GET /raw (raw 전용 리스너: / 도 raw, /api/state 는 404). 포트가 막혀 있으면 건너뛴다
void testRawHttp(const std::string& bin) {
    tof::DevicePosture d;
    std::string err;
    d.loadModel(bin, &err);
    tof::TofLine l0;
    tof::parseTofLine(uartV1Line(0, 3, 1, 10, 1), l0);
    d.feed(d.addSensor("x"), l0, 0);
    const int port = 38772;
    tof::HttpServer srv("127.0.0.1", port, tof::HttpHandlers{nullptr, nullptr, nullptr, [&d] { return d.rawJson(100); }});
    if (!srv.start(&err)) { std::printf("skip raw http test (port %d: %s)\n", port, err.c_str()); return; }
    auto get = [&](const char* path) {
        const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in a{};
        a.sin_family = AF_INET; a.sin_port = htons((uint16_t)port); ::inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
        std::string out;
        if (::connect(fd, (sockaddr*)&a, sizeof a) == 0) {
            const std::string req = std::string("GET ") + path + " HTTP/1.0\r\nHost: x\r\n\r\n";
            ::send(fd, req.data(), req.size(), 0);
            char buf[4096];
            ssize_t n;
            while ((n = ::recv(fd, buf, sizeof buf, 0)) > 0) out.append(buf, (size_t)n);
        }
        ::close(fd);
        return out;
    };
    const std::string r1 = get("/raw"), r2 = get("/"), r3 = get("/api/state");
    srv.stop();
    check(r1.rfind("HTTP/1.0 200", 0) == 0 && r1.find("Access-Control-Allow-Origin: *") != std::string::npos &&
              r1.find("\"sensors\":[{\"id\":0,\"have\":true") != std::string::npos && jsonArrayCount(r1, "dist_mm", 0) == 64,
          "raw http: GET /raw -> 200, CORS *, relay-shaped JSON with 64 zones");
    check(r2.rfind("HTTP/1.0 200", 0) == 0 && r2.find("\"dev\":") != std::string::npos, "raw http: GET / on the raw-only listener is /raw too");
    check(r3.rfind("HTTP/1.0 404", 0) == 0, "raw http: raw-only listener has no /api/state");
}

struct ReplayLine { uint32_t ms; int sensor; tof::TofLine line; };

void testReplay(const std::string& bin, const std::vector<std::string>& files, tof::DeviceConfig cfg, const char* tag) {
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
    std::printf("replay[%s]: %zu lines, %ld ticks, %ld scored; fused acc %.3f", tag, lines.size(), ticks, scored,
                scored ? (double)correct / scored : 0.0);
    for (size_t s = 0; s < files.size(); ++s)
        std::printf(", sensor%zu acc %.3f", s, per_scored[s] ? (double)per_correct[s] / per_scored[s] : 0.0);
    std::printf("\n  per label:");
    for (const auto& kv : per_label)
        std::printf(" %s %.2f(%ld)", kv.first.c_str(), (double)kv.second.first / kv.second.second, kv.second.second);
    std::printf("\n");
    const tof::DeviceResult fin = device.result();
    std::printf("  resets: periodic %llu away %llu manual %llu\n", (unsigned long long)fin.resets.periodic,
                (unsigned long long)fin.resets.away, (unsigned long long)fin.resets.manual);
    check(scored > 0, "replay produced scored ticks");
    std::printf("  state: %s\n", device.stateJson(end_ms).substr(0, 400).c_str());
    const std::string raw = device.rawJson(end_ms);
    const size_t i0 = raw.find("{\"id\":0,\"have\":true"), i1 = raw.find("{\"id\":1,\"have\":true");
    long d0 = 0, d1 = 0;
    check(files.size() >= 2 && i0 != std::string::npos && i1 != std::string::npos &&
              jsonArrayCount(raw, "dist_mm", i0, &d0) == 64 && jsonArrayCount(raw, "status", i0) == 64 &&
              jsonArrayCount(raw, "dist_mm", i1, &d1) == 64 && jsonArrayCount(raw, "status", i1) == 64,
          "replay: /raw has both sensors have=true with 64 dist_mm + 64 status");
    const std::vector<tof::SensorStatus> fin_ss = device.sensors(end_ms);
    (void)fin_ss;
    check(d0 != 0 && d1 != 0 && raw.find("\"dist_mm\":[0,0,0,0") == std::string::npos,
          "replay: /raw grids carry the tof_logger JSON values (not zeros)");
    std::printf("  raw: %s\n", raw.substr(0, 200).c_str());
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
    testPeriodicReset(argv[1]);
    testAwayReset(argv[1]);
    testDevicePathMatchesGolden(argv[1], argv[2]);
    testRawJson(argv[1]);
    testRawHttp(argv[1]);
    if (argc > 3) {
        const std::vector<std::string> files(argv + 3, argv + argc);
        testReplay(argv[1], files, tof::DeviceConfig{}, "defaults 0/60");
        tof::DeviceConfig off;
        off.reset_period_s = 0; off.away_reset_s = 0;
        testReplay(argv[1], files, off, "resets off");
    }
    std::printf("%s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
