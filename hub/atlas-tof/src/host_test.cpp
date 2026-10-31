// 호스트(x86) 테스트: 보드·센서 없이 돌아가는 부분을 검사한다.
//
//   host_test <posture_net.bin> <golden.txt> [replay_sensor0.jsonl replay_sensor1.jsonl ...]
//
// 1) 줄 파서: tof / tof_hello / tof_err / 쓰레기
// 2) PostureNet: export.py 의 golden.txt (PyTorch logits) 와 비교, 프레임당 시간
// 3) GRU 리셋 규칙 (device_posture.h "리셋"): (a) 주기 리셋이 N s 뒤 h·EMA 를 비우고 시각을 갱신, (b) coverage 미달이
//    N s 이어지면 반복 리셋·재실 한 프레임에 타이머 해제, (c) 둘 다 0 이면 DevicePosture 경로가 golden 과 같다(회귀 없음)
// 4) GET /raw: uart_relay_board 스키마(두 센서 64칸·have·id=물리 번호·받은 그대로), HttpServer 리스너로 실제 GET
// 4'') 장치 위치별 모델(placement.h): 맵 파싱, 시작 때 파일 우선, POST 성공·잘못된 키 400·모양 다른 모델 500(기존 유지)·
//     같은 키 재요청, 교체 뒤 첫 스텝 EMA = 그 프레임 확률, 기능 꺼짐 = /api/state 에 placement 없음(골든 동일은 3-c)
// 4''') 베이스라인·misplaced 게이트(baseline.h): 누적기 중앙값·σ·유효, movedFraction(가림 제외·멀어짐·사라짐·σ 문턱·배경 아닌 칸 제외),
//     파일 저장/읽기, 캡처→규칙→device_moved(10 s 유지), 모드 off = 표시 == 평활, never = misplaced 0 재정규화, and 는 규칙에 따라, HTTP extra 경로
// 4-d) 시연 엎드림 규칙(facedown_rule.h): near 경계·status·행 집계·numpy 중앙값·두 분기·seated, 4 s 창 0.7/0.4 이력, 서비스에서
//     face_down conf 0.995 고정·모델 출력 보존·rule 필드, 모델 입력 변환 radial(가운데 1.003 ~ 모서리 1.124)·fill(255 → N mm·status 5) 이
//     PostureNet 에 직접 넣은 것과 비트 단위로 같은지, 끄면 그대로인지
// 5) 리플레이(있으면): 시뮬 세션을 두 센서 파일로 넣어 DevicePosture 전체 경로를 가상 시계로 돌리고
//    파일의 "label" 과 비교 (dataset/tof_sim_v5/tools/sim_to_toflines.py 가 만든 파일). 기본 설정과 리셋 끔 두 번. 끝에 /raw 확인
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <numeric>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "baseline.h"
#include "device_posture.h"
#include "facedown_rule.h"
#include "http_server.h"
#include "placement.h"
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

// (c) 둘 다 0: DevicePosture(센서 1개) 경로의 센서별 확률이 golden 의 softmax(logits) 와 같다. 기본값(둘 다 0 = 끔)이
//     곧 그 설정이고, 명시적으로 켠 설정(900/60)도 240 프레임(24 s, 사람 있음)에서는 같다. 기본값 자체도 검사한다
void testDevicePathMatchesGolden(const std::string& bin, const std::string& golden_path) {
    tof::PostureNet probe;
    std::string err;
    if (!probe.load(bin, &err)) { check(false, "device-golden: model load"); return; }
    Golden gd;
    if (!loadGolden(golden_path, probe, gd)) return;
    const int n = gd.H * gd.W, C = gd.C;
    for (int pass = 0; pass < 2; ++pass) {
        tof::DeviceConfig cfg;
        if (pass == 0) { cfg.reset_period_s = 0; cfg.away_reset_s = 0; }     // = 기본값
        else { cfg.reset_period_s = 900; cfg.away_reset_s = 60; }           // 켠 설정: 24 s 안에는 안 걸린다
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
                    pass == 0 ? "resets off (= defaults)" : "enabled 900/60", max_diff, mism,
                    (unsigned long long)r.resets.periodic, (unsigned long long)r.resets.away);
        check(max_diff < 1e-4 && mism == 0 && r.resets.periodic == 0 && r.resets.away == 0 && r.resets.manual == 0,
              pass == 0 ? "resets off (defaults): DevicePosture path matches golden (no regression)"
                        : "enabled 900/60: DevicePosture path matches golden over 24 s (no reset fired)");
    }
    const tof::DeviceConfig def;
    check(def.reset_period_s == 0 && def.away_reset_s == 0 && def.present_min_mm == 150 && def.present_max_mm == 1300 &&
              std::fabs(def.present_min_coverage - 0.06f) < 1e-6f,
          "defaults: periodic reset off (0), away reset off (0), presence 150-1300 mm, coverage 0.06");
    // 기본값으로 15 분 앉아 있다가 2 분 비워도 리셋은 걸리지 않는다 (둘 다 꺼짐). coverage·present_rule 은 그대로 보고된다
    tof::DevicePosture d(def);
    d.loadModel(bin, &err);
    const int s = d.addSensor("long");
    for (int k = 0; k <= 10300; ++k) {                     // 910 s 착석 + 120 s 비움
        const int64_t now = 100 * k;
        d.feed(s, makeFrame(k + 1, (uint32_t)now, k <= 9100 ? 20 : 0), now);
        d.tick(now);
    }
    const auto r = d.result();
    check(r.resets.periodic == 0 && r.resets.away == 0 && r.resets.manual == 0 && r.resets.last_reset_ms == 0 && !r.present_rule,
          "defaults: 910 s seated + 120 s empty -> no reset at all (both off), present_rule still reported");
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
    tof::HttpServer srv("127.0.0.1", port, tof::HttpHandlers{nullptr, nullptr, nullptr, [&d] { return d.rawJson(100); }, nullptr, nullptr, nullptr});
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

// 구조가 다른(은닉 16) 가짜 posture_net.bin — 헤더는 맞고 가중치는 0. 로더는 값을 검사하지 않으므로 읽히지만 모양이 다르다
std::string writeFakeModel(const std::string& path, int H, int W, int C, int hidden) {
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    f.write("TOFPN001", 8);
    const int32_t hdr[4] = {H, W, C, hidden};
    f.write(reinterpret_cast<const char*>(hdr), sizeof hdr);
    const size_t n_fc_in = 32 * 4 * 4;
    const size_t sizes[14] = {16u * 2 * 9, 16, 32u * 16 * 9, 32, 32u * 32 * 9, 32, (size_t)hidden * n_fc_in, (size_t)hidden,
                              (size_t)3 * hidden * hidden, (size_t)3 * hidden * hidden, (size_t)3 * hidden, (size_t)3 * hidden,
                              (size_t)C * hidden, (size_t)C};
    size_t total = 0;
    for (size_t s : sizes) total += s;
    std::vector<float> zeros(total, 0.f);
    f.write(reinterpret_cast<const char*>(zeros.data()), total * sizeof(float));
    return path;
}

void testPlacement(const std::string& bin) {
    // 맵 파싱
    tof::PlacementConfig pc;
    std::string err;
    check(tof::parsePlacementModels(" left = a/one.bin , center=b.bin,right=/abs/c.bin ", "/base", pc, &err) && pc.models.size() == 3 &&
              pc.models[0].first == "left" && pc.models[0].second == "/base/a/one.bin" && pc.models[1].second == "/base/b.bin" &&
              pc.models[2].first == "right" && pc.models[2].second == "/abs/c.bin",
          "placement: parse map with spaces, relative paths under exe dir, absolute kept");
    check(!tof::parsePlacementModels("left=a,up=b", "/base", pc, &err) && pc.models.empty() && err.find("unknown placement key") != std::string::npos,
          "placement: unknown key rejected (map cleared)");
    check(!tof::parsePlacementModels("left=a,left=b", "/base", pc, &err) && err.find("duplicate") != std::string::npos, "placement: duplicate key rejected");
    check(!tof::parsePlacementModels("left=", "/base", pc, &err), "placement: empty path rejected");
    check(!tof::parsePlacementModels("left", "/base", pc, &err), "placement: missing '=' rejected");
    check(tof::parsePlacementModels("   ", "/base", pc, &err) && pc.models.empty() && !pc.enabled(), "placement: empty spec = disabled");
    check(tof::parsePlacementBody("{\"placement\": \"left\"}") == "left" && tof::parsePlacementBody("{ \"placement\":\"right\" , \"x\":1}") == "right" &&
              tof::parsePlacementBody("{\"x\":\"left\"}").empty() && tof::parsePlacementBody("").empty(),
          "placement: POST body parser");

    const std::string tmpdir = "/tmp";
    const std::string pfile = tmpdir + "/tofposture_placement_test_" + std::to_string((long)::getpid()) + ".txt";
    const std::string bad = writeFakeModel(tmpdir + "/tofposture_badshape_" + std::to_string((long)::getpid()) + ".bin", 8, 8, 9, 16);
    const std::string garbage = tmpdir + "/tofposture_garbage_" + std::to_string((long)::getpid()) + ".bin";
    { std::ofstream g(garbage, std::ios::trunc); g << "not a model"; }
    std::remove(pfile.c_str());

    // 시작: 파일 없음 + TOFPOSTURE_MODEL 이 맵의 center 와 같은 경로 → center; 다른 경로 → unknown
    tof::PlacementConfig cfg;
    cfg.models = {{"left", bin}, {"center", bin}, {"right", bad}};
    cfg.file = pfile;
    {
        tof::DevicePosture d0;
        tof::Placement p0(cfg, d0);
        check(p0.initialModelPath(bin) == bin && p0.current() == "left", "placement: no file -> TOFPOSTURE_MODEL kept, key = first map entry with that path");
        tof::DevicePosture d1;
        tof::Placement p1(cfg, d1);
        check(p1.initialModelPath("/nowhere/other.bin") == "/nowhere/other.bin" && p1.current() == "unknown",
              "placement: no file, model not in map -> unknown");
        // 파일에 right 가 있으면 파일이 우선
        check(tof::writePlacementFile(pfile, "right", &err) && tof::readPlacementFile(pfile) == "right", "placement: file write (tmp+rename) and read back");
        tof::DevicePosture d2;
        tof::Placement p2(cfg, d2);
        check(p2.initialModelPath(bin) == bad && p2.current() == "right", "placement: file key wins over TOFPOSTURE_MODEL");
        std::ofstream(pfile, std::ios::trunc) << "  upside_down \n";
        tof::DevicePosture d3;
        tof::Placement p3(cfg, d3);
        check(p3.initialModelPath(bin) == bin && p3.current() == "left", "placement: file with a key not in the map is ignored");
        std::remove(pfile.c_str());
    }

    // 전환: 두 센서가 돌아가는 중에 POST
    tof::DevicePosture d;
    tof::Placement p(cfg, d);
    const std::string start_model = p.initialModelPath(bin);
    d.setPlacement(p.current());
    check(d.loadModel(start_model, &err), "placement: model loads");
    const int s0 = d.addSensor("p0"), s1 = d.addSensor("p1");
    const int C = d.classes();
    auto step = [&](int k) {
        const int64_t now = 100 * k;
        d.feed(s0, makeFrame(k + 1, (uint32_t)now, 20), now);
        d.feed(s1, makeFrame(k + 1, (uint32_t)now, 12), now);
        d.tick(now);
        return d.result();
    };
    for (int k = 0; k < 30; ++k) step(k);
    check(d.stateJson(2900).find("\"placement\":\"left\"") != std::string::npos, "placement: /api/state has placement before any switch");
    const auto before = d.result();
    check(!smoothEqualsInstant(before, C), "placement: EMA has history before the switch (sanity)");
    std::string out;
    int code = p.apply("{\"placement\":\"center\"}", 3000, out);
    const auto after_switch = d.result();
    check(code == 200 && out.find("\"placement\":\"center\"") != std::string::npos && out.find("\"switches\":1") != std::string::npos &&
              out.find("\"changed_ms\":3000") != std::string::npos && out.find("\"options\":[\"left\",\"center\",\"right\"]") != std::string::npos,
          "placement: POST center -> 200 with GET-shaped JSON (placement, switches, changed_ms, options)");
    check(after_switch.resets.model_switch == 1 && after_switch.resets.last_reset_ms == 3000 && after_switch.smooth_argmax == -1 &&
              d.sensors(3000)[0].resets == 1 && d.sensors(3000)[1].resets == 1,
          "placement: switch resets every sensor GRU and clears the fused EMA (resets.model_switch)");
    const auto first = step(30);
    check(smoothEqualsInstant(first, C) && first.label_since_ms == 3000 && first.valid, "placement: first step after the switch starts EMA from that frame's probabilities");
    check(tof::readPlacementFile(pfile) == "center", "placement: chosen key saved to the placement file");
    check(d.stateJson(3000).find("\"placement\":\"center\"") != std::string::npos && d.modelPath() == bin,
          "placement: /api/state placement updated, model.file is the new path");
    // 같은 키 다시: 200, 다시 읽고 리셋
    code = p.apply("{\"placement\":\"center\"}", 3100, out);
    check(code == 200 && out.find("\"switches\":2") != std::string::npos && d.result().resets.model_switch == 2,
          "placement: same key again -> 200, model reloaded and reset again");
    // 잘못된 키: 400, 아무것도 안 바뀜
    code = p.apply("{\"placement\":\"up\"}", 3200, out);
    check(code == 400 && out.find("\"error\"") != std::string::npos && p.current() == "center" && d.result().resets.model_switch == 2 &&
              tof::readPlacementFile(pfile) == "center",
          "placement: bad key -> 400, nothing changes");
    code = p.apply("{}", 3250, out);
    check(code == 400, "placement: body without placement -> 400");
    // 모양 다른 모델: 500, 기존 유지
    code = p.apply("{\"placement\":\"right\"}", 3300, out);
    check(code == 500 && out.find("shape mismatch") != std::string::npos && p.current() == "center" && d.modelPath() == bin &&
              d.result().resets.model_switch == 2 && tof::readPlacementFile(pfile) == "center",
          "placement: model with a different shape -> 500, previous model and key kept");
    const auto still = step(31);
    check(still.valid && still.smooth_argmax >= 0, "placement: service keeps running after the failed switch");
    // 못 읽는 파일: 500
    tof::PlacementConfig gcfg = cfg;
    gcfg.models = {{"left", bin}, {"center", garbage}, {"right", bin}};
    tof::Placement pg(gcfg, d);
    code = pg.apply("{\"placement\":\"center\"}", 3400, out);
    check(code == 500 && d.modelPath() == bin, "placement: unreadable model -> 500, previous model kept");
    // 꺼짐: placement 필드 없음
    tof::DevicePosture off;
    off.loadModel(bin, &err);
    check(off.stateJson(0).find("\"placement\"") == std::string::npos, "placement: disabled -> /api/state has no placement field");
    std::remove(pfile.c_str());
    std::remove(bad.c_str());
    std::remove(garbage.c_str());
}

// HttpServer 로 GET/POST /api/placement (본문 Content-Length 읽기, 코드 400). raw 전용 리스너에는 없다
void testPlacementHttp(const std::string& bin) {
    tof::DevicePosture d;
    std::string err;
    d.loadModel(bin, &err);
    tof::PlacementConfig cfg;
    cfg.models = {{"left", bin}, {"center", bin}, {"right", bin}};
    cfg.file = "/tmp/tofposture_placement_http_" + std::to_string((long)::getpid()) + ".txt";
    tof::Placement p(cfg, d);
    p.initialModelPath(bin);
    const int port = 38773;
    tof::HttpHandlers hs{nullptr, [&d] { return d.stateJson(0); }, nullptr, nullptr,
                         [&p] { return p.json(0); },
                         [&p](const std::string& body, std::string& out) { return p.apply(body, 5000, out); }, nullptr};
    tof::HttpServer srv("127.0.0.1", port, hs);
    if (!srv.start(&err)) { std::printf("skip placement http test (port %d: %s)\n", port, err.c_str()); return; }
    auto req = [&](const std::string& text) {
        const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in a{};
        a.sin_family = AF_INET; a.sin_port = htons((uint16_t)port); ::inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
        std::string out;
        if (::connect(fd, (sockaddr*)&a, sizeof a) == 0) {
            ::send(fd, text.data(), text.size(), 0);
            char buf[4096];
            ssize_t n;
            while ((n = ::recv(fd, buf, sizeof buf, 0)) > 0) out.append(buf, (size_t)n);
        }
        ::close(fd);
        return out;
    };
    const std::string g1 = req("GET /api/placement HTTP/1.0\r\n\r\n");
    const std::string body = "{\"placement\":\"right\"}";
    const std::string p1 = req("POST /api/placement HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: " +
                               std::to_string(body.size()) + "\r\n\r\n" + body);
    const std::string bad = "{\"placement\":\"up\"}";
    const std::string p2 = req("POST /api/placement HTTP/1.1\r\nContent-Length: " + std::to_string(bad.size()) + "\r\n\r\n" + bad);
    const std::string o1 = req("OPTIONS /api/placement HTTP/1.1\r\nOrigin: http://pi5\r\n\r\n");
    srv.stop();
    check(g1.rfind("HTTP/1.0 200", 0) == 0 && g1.find("\"placement\":\"left\"") != std::string::npos && g1.find("Access-Control-Allow-Origin: *") != std::string::npos,
          "placement http: GET /api/placement -> 200 with CORS");
    check(p1.rfind("HTTP/1.0 200", 0) == 0 && p1.find("\"placement\":\"right\"") != std::string::npos && p1.find("\"switches\":1") != std::string::npos &&
              p.current() == "right" && tof::readPlacementFile(cfg.file) == "right",
          "placement http: POST with Content-Length body -> 200, switched and saved");
    check(p2.rfind("HTTP/1.0 400", 0) == 0 && p2.find("\"error\"") != std::string::npos, "placement http: bad key -> 400");
    check(o1.rfind("HTTP/1.0 200", 0) == 0 && o1.find("Access-Control-Allow-Origin: *") != std::string::npos, "placement http: OPTIONS preflight -> 200 with CORS");
    tof::HttpServer rawonly("127.0.0.1", port, tof::HttpHandlers{nullptr, nullptr, nullptr, [&d] { return d.rawJson(0); }, nullptr, nullptr, nullptr});
    if (rawonly.start(&err)) {
        const std::string r404 = req("GET /api/placement HTTP/1.0\r\n\r\n");
        rawonly.stop();
        check(r404.rfind("HTTP/1.0 404", 0) == 0, "placement http: raw-only listener has no /api/placement");
    }
    std::remove(cfg.file.c_str());
}

// 8×8 프레임: 모든 칸 dist/status 를 직접 준다
tof::TofLine gridFrame(uint32_t seq, uint32_t ms, const int16_t* d, const uint8_t* st) {
    tof::TofLine l;
    l.kind = tof::LineKind::Frame;
    l.frame.seq = seq; l.frame.ms = ms; l.frame.w = 8; l.frame.h = 8; l.frame.has_status = true;
    for (int i = 0; i < 64; ++i) { l.frame.d[i] = d[i]; l.frame.st[i] = st[i]; }
    return l;
}

void testBaseline(const std::string& bin) {
    // 누적기: 칸 0 은 2900/3000/3100(중앙값 3000, σ 81.6), 칸 1 은 3 프레임 중 2 프레임 무효 → valid 0, 나머지 3000 고정
    tof::BaselineAccumulator acc;
    acc.reset(8, 8);
    int16_t d[64]; uint8_t st[64];
    for (int k = 0; k < 3; ++k) {
        for (int i = 0; i < 64; ++i) { d[i] = 3000; st[i] = 5; }
        d[0] = (int16_t)(2900 + 100 * k);
        if (k < 2) { d[1] = -1; st[1] = 255; }
        acc.add(gridFrame(k + 1, 100 * k, d, st).frame);
    }
    tof::SensorBaseline sb;
    check(acc.finish(sb, 1200) && sb.frames == 3 && sb.median_mm[0] == 3000 && std::fabs(sb.std_mm[0] - 81.65f) < 0.1f && sb.valid[0] == 1 &&
              sb.valid[1] == 0 && sb.median_mm[1] == -1 && sb.valid[2] == 1 && sb.std_mm[2] == 0.f && sb.bg_zones == 63 && sb.sigma_mm == 0.f,
          "baseline: accumulator median/std/valid (zone invalid in >= half the frames is dropped), bg zones, sigma = median std");
    // movedFraction: 배경 63칸(칸 1 제외). 같은 장면 → 0
    tof::MovedRuleParams p;
    for (int i = 0; i < 64; ++i) { d[i] = 3000; st[i] = 5; }
    int changed = 0, lost = 0, bg = 0;
    check(tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg) == 0.f && bg == 63 && changed == 0,
          "baseline: unchanged scene -> moved_frac 0 over 63 background zones");
    // 사람이 40칸을 600 mm 로 가림 → 안 센다
    for (int i = 0; i < 40; ++i) d[i] = 600;
    check(tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p) == 0.f, "baseline: person occluding 40 zones at 600 mm is not a device move");
    // 벽이 3300 으로 멀어진 40칸(그 중 칸 1 은 배경 아님 → 39) → 39/63
    for (int i = 0; i < 64; ++i) d[i] = 3000;
    for (int i = 0; i < 40; ++i) d[i] = 3300;
    float fr = tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg);
    check(std::fabs(fr - 38.f / 63.f) < 1e-4f && changed == 38 && lost == 0,
          "baseline: 40 background zones farther by 300 mm -> 38 changed (zone 1 not background, zone 0 under its 4 sigma)");
    // 더 가깝지만 사람 범위 밖(2000 mm, −1000) → 변화로 센다
    for (int i = 0; i < 64; ++i) d[i] = 3000;
    for (int i = 0; i < 40; ++i) d[i] = 2000;
    check(std::fabs(tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p) - 39.f / 63.f) < 1e-4f,
          "baseline: closer but beyond person range (2000 mm) counts as a change");
    // 보이던 벽 40칸이 무효 → lost
    for (int i = 0; i < 64; ++i) d[i] = 3000;
    for (int i = 0; i < 40; ++i) { d[i] = -1; st[i] = 255; }
    fr = tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg);
    check(fr == 0.f && lost == 39 && changed == 0, "baseline: background zones that vanish are 'lost' and not counted (person kills far returns)");
    for (int i = 0; i < 61; ++i) { d[i] = -1; st[i] = 255; }
    check(tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg) < 0.f && lost == 60,
          "baseline: fewer than min_eval_zones valid background zones -> undecided (-1)");
    for (int i = 0; i < 64; ++i) { d[i] = 3000; st[i] = 5; }
    // 문턱: 칸 0 σ 81.6 → thr max(80, 326) = 326: +300 은 안 세고 +400 은 센다. 칸 2 σ 0 → thr 80: +100 은 센다
    for (int i = 0; i < 64; ++i) { d[i] = 3000; st[i] = 5; }
    d[0] = 3300; d[2] = 3100;
    fr = tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg);
    check(changed == 1, "baseline: threshold = max(80 mm, 4 sigma) per zone (zone 0 +300 < 4*81.6 ignored, zone 2 +100 > 80 counted)");
    d[0] = 3400;
    tof::movedFraction(sb, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg);
    check(changed == 2, "baseline: zone 0 +400 exceeds 4 sigma");
    // 배경이 아닌 칸(베이스라인 700 mm)은 평가에서 빠진다
    tof::SensorBaseline near = sb;
    for (int i = 0; i < 32; ++i) near.median_mm[i] = 700;
    for (int i = 0; i < 64; ++i) { d[i] = 3300; st[i] = 5; }
    fr = tof::movedFraction(near, gridFrame(1, 0, d, st).frame, p, &changed, &lost, &bg);
    check(bg == 32 && changed == 32 && fr == 1.f, "baseline: zones with a near baseline (person/desk) are not background");
    tof::SensorBaseline nobg = sb;
    for (int i = 0; i < 64; ++i) nobg.median_mm[i] = 700;
    check(tof::movedFraction(nobg, gridFrame(1, 0, d, st).frame, p) < 0.f, "baseline: no background zones -> -1 (rule cannot fire)");

    // 저장/읽기
    const std::string file = "/tmp/tofposture_baseline_test_" + std::to_string((long)::getpid()) + ".json";
    tof::Baseline b;
    sb.id = 1;
    b.sensors.push_back(sb);
    b.captured_epoch_s = 1792984470.5; b.source = "post";
    std::string err;
    tof::Baseline back;
    check(tof::saveBaseline(file, b, &err) && tof::loadBaseline(file, back, &err) && back.sensors.size() == 1 && back.sensors[0].id == 1 &&
              back.sensors[0].median_mm[0] == 3000 && back.sensors[0].valid[1] == 0 && back.sensors[0].median_mm[63] == 3000 &&
              std::fabs(back.sensors[0].std_mm[0] - 81.6f) < 0.11f && back.sensors[0].bg_zones == 63 && back.source == "post" &&
              std::fabs(back.captured_epoch_s - 1792984470.5) < 1e-3 && back.forId(1) != nullptr && back.forId(0) == nullptr,
          "baseline: save (tmp+rename) and load round-trip");
    std::remove(file.c_str());
    check(!tof::loadBaseline(file, back, &err), "baseline: missing file -> load fails");

    // 서비스 경로: 캡처 → 규칙 → device_moved → 게이트. 센서 2개, 모드 and
    tof::DeviceConfig cfg;
    cfg.misplaced_rule = tof::MisplacedRule::And;
    cfg.moved.hold_s = 2;
    tof::DevicePosture dev(cfg);
    dev.loadModel(bin, &err);
    dev.setBaselineFile(file);
    const int s0 = dev.addSensor("a"), s1 = dev.addSensor("b");
    const int C = dev.classes();
    auto scene = [&](int16_t wall, int person_zones, int16_t person_mm) {
        for (int i = 0; i < 64; ++i) { d[i] = wall; st[i] = 5; }
        for (int i = 64 - person_zones; i < 64; ++i) d[i] = person_mm;     // 아래 줄부터 사람
    };
    int k = 0;
    auto run = [&](int ticks, int16_t wall, int person_zones) {
        for (int j = 0; j < ticks; ++j, ++k) {
            const int64_t now = 100 * k;
            scene(wall, person_zones, 650);
            dev.feed(s0, gridFrame(k + 1, (uint32_t)now, d, st), now);
            scene((int16_t)(wall + 100), person_zones, 700);
            dev.feed(s1, gridFrame(k + 1, (uint32_t)now, d, st), now);
            dev.tick(now);
        }
        return dev.result();
    };
    auto r0 = run(5, 3000, 0);
    check(!r0.device_moved && r0.moved_frac_max < 0.f && dev.stateJson(400).find("\"misplaced_rule\":{\"mode\":\"and\",\"baseline\":false") != std::string::npos &&
              dev.stateJson(400).find("\"gated\":") != std::string::npos,
          "gate: no baseline -> moved_frac -1, device_moved false, /api/state misplaced_rule reports baseline=false");
    check(r0.display_argmax >= 0 && r0.display[tof::kMisplacedIndex] == 0.f && std::fabs(std::accumulate(r0.display, r0.display + C, 0.f) - 1.f) < 1e-4f &&
              r0.gated == (r0.smooth_argmax == tof::kMisplacedIndex) && r0.model_misplaced == (r0.smooth_argmax == tof::kMisplacedIndex),
          "gate(and, no baseline): display has misplaced 0 and sums to 1, gated iff model said misplaced, model output kept");
    check(dev.startBaselineCapture(400, 1, "post") && dev.capturing() && !dev.startBaselineCapture(450, 1, "post"),
          "baseline: capture starts once (second start -> false while running)");
    auto r1 = run(12, 3000, 0);                              // 0.5~1.6 s: 빈 자리 캡처 1 s
    const tof::Baseline cap = dev.baseline();
    check(!dev.capturing() && cap.any() && cap.sensors.size() == 2 && cap.source == "post" && cap.forId(0) && cap.forId(1) &&
              cap.forId(0)->median_mm[0] == 3000 && cap.forId(1)->median_mm[0] == 3100 && cap.forId(0)->bg_zones == 64 && cap.forId(0)->frames >= 9,
          "baseline: capture over 1 s builds both sensors' baselines (slot index as id for non-UART input) and ends by itself");
    check(tof::loadBaseline(file, back, &err) && back.sensors.size() == 2, "baseline: finished capture is saved to the baseline file");
    check(dev.sensors(1700)[0].has_baseline && dev.sensors(1700)[0].baseline_bg_zones == 64 && r1.moved_frac_max == 0.f,
          "baseline: sensors report has_baseline, same scene -> moved_frac 0");
    auto r2 = run(30, 3000, 24);                             // 사람 착석(아래 3줄 650 mm): 가림은 이동이 아니다
    check(!r2.moved_candidate && r2.moved_frac_max == 0.f && !r2.device_moved, "gate: seated person -> no move candidate");
    auto r3 = run(15, 3400, 24);                             // 장치가 움직여 벽이 400 mm 멀어짐, 1.5 s: 후보지만 아직 아님
    check(r3.moved_candidate && r3.moved_frac_max >= 0.6f && !r3.device_moved && r3.moved_held_ms == 1400,
          "gate: wall moved 400 mm on 40 zones -> candidate (frac 40/64), not yet held 2 s");
    auto r4 = run(10, 3400, 24);                             // 2.5 s 유지 → device_moved
    check(r4.device_moved && r4.moved_held_ms >= 2000, "gate: candidate held >= hold_s -> device_moved");
    bool same = true;
    for (int i = 0; i < C; ++i) same = same && r4.display[i] == r4.smooth[i];
    check(same && !r4.gated && r4.display_argmax == r4.smooth_argmax, "gate(and, device moved): display == model output, nothing gated");
    auto r5 = run(3, 3000, 24);                              // 제자리로 → 후보 즉시 해제
    check(!r5.device_moved && !r5.moved_candidate && r5.moved_held_ms == 0, "gate: back in place -> device_moved false at once");
    check(dev.baselineJson(9999).find("\"captured\":true") != std::string::npos && dev.baselineJson(9999).find("\"rule\":{\"mode\":\"and\"") != std::string::npos &&
              dev.baselineJson(9999).find("\"median_mm\":[3000,") != std::string::npos,
          "baseline: /api/baseline JSON has grids, rule params and state");
    dev.clearBaseline();
    check(!dev.baseline().any() && !tof::loadBaseline(file, back, &err) && !dev.sensors(9999)[0].has_baseline, "baseline: clear removes baseline and file");
    // setBaseline(파일에서 읽은 것) 로 복원
    dev.setBaseline(cap, 10000);
    check(dev.baseline().any() && run(2, 3000, 24).moved_frac_max == 0.f, "baseline: setBaseline from a loaded file restores the rule");

    // 모드 off: 표시 == 평활(비트 동일). never: misplaced 0, 재정규화, 모델 출력 유지
    tof::DeviceConfig off_cfg;
    tof::DevicePosture off(off_cfg);
    off.loadModel(bin, &err);
    const int o0 = off.addSensor("o");
    tof::DeviceConfig nv_cfg;
    nv_cfg.misplaced_rule = tof::MisplacedRule::Never;
    tof::DevicePosture nv(nv_cfg);
    nv.loadModel(bin, &err);
    const int n0 = nv.addSensor("n");
    bool off_same = true, nv_ok = true;
    for (int j = 0; j < 30; ++j) {
        const int64_t now = 100 * j;
        scene(3000, 24, 650);
        off.feed(o0, gridFrame(j + 1, (uint32_t)now, d, st), now); off.tick(now);
        nv.feed(n0, gridFrame(j + 1, (uint32_t)now, d, st), now); nv.tick(now);
        const auto ro = off.result(), rn = nv.result();
        for (int i = 0; i < C; ++i) off_same = off_same && ro.display[i] == ro.smooth[i];
        off_same = off_same && ro.display_argmax == ro.smooth_argmax && !ro.gated;
        float sum = 0.f;
        for (int i = 0; i < C; ++i) sum += rn.display[i];
        nv_ok = nv_ok && rn.display[tof::kMisplacedIndex] == 0.f && std::fabs(sum - 1.f) < 1e-4f && rn.display_argmax != tof::kMisplacedIndex &&
                rn.smooth[0] == ro.smooth[0];
    }
    check(off_same, "gate(off): display identical to the smoothed model output (today's behaviour)");
    check(nv_ok, "gate(never): misplaced never displayed, probs renormalised, model output untouched");
    const std::string sj = off.stateJson(3000);
    check(sj.find("\"model_output\":{\"posture\":") != std::string::npos && sj.find("\"misplaced_rule\":{\"mode\":\"off\"") != std::string::npos,
          "state: model_output and misplaced_rule blocks present");
}

// HttpServer extra 경로: /api/baseline GET·capture 202/409·clear, 모르는 경로 404
void testBaselineHttp(const std::string& bin) {
    tof::DevicePosture d;
    std::string err;
    d.loadModel(bin, &err);
    const int port = 38774;
    tof::HttpHandlers hs{nullptr, [&d] { return d.stateJson(0); }, nullptr, nullptr, nullptr, nullptr,
                         [&d](const std::string& method, const std::string& path, const std::string& body, std::string& out) -> int {
                             (void)body;
                             if (path == "/api/baseline" && method == "GET") { out = d.baselineJson(0); return 200; }
                             if (path == "/api/baseline/capture" && method == "POST") {
                                 if (!d.startBaselineCapture(0, 5, "post")) { out = "{\"error\":\"capture already running\"}"; return 409; }
                                 out = d.baselineJson(0); return 202;
                             }
                             if (path == "/api/baseline/clear" && method == "POST") { d.clearBaseline(); out = d.baselineJson(0); return 200; }
                             return 0;
                         }};
    tof::HttpServer srv("127.0.0.1", port, hs);
    if (!srv.start(&err)) { std::printf("skip baseline http test (port %d: %s)\n", port, err.c_str()); return; }
    auto req = [&](const std::string& text) {
        const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in a{};
        a.sin_family = AF_INET; a.sin_port = htons((uint16_t)port); ::inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
        std::string out;
        if (::connect(fd, (sockaddr*)&a, sizeof a) == 0) {
            ::send(fd, text.data(), text.size(), 0);
            char buf[8192];
            ssize_t n;
            while ((n = ::recv(fd, buf, sizeof buf, 0)) > 0) out.append(buf, (size_t)n);
        }
        ::close(fd);
        return out;
    };
    const std::string g = req("GET /api/baseline HTTP/1.0\r\n\r\n");
    const std::string c1 = req("POST /api/baseline/capture HTTP/1.0\r\nContent-Length: 0\r\n\r\n");
    const std::string c2 = req("POST /api/baseline/capture HTTP/1.0\r\nContent-Length: 0\r\n\r\n");
    const std::string cl = req("POST /api/baseline/clear HTTP/1.0\r\n\r\n");
    const std::string nf = req("GET /api/nothing HTTP/1.0\r\n\r\n");
    srv.stop();
    check(g.rfind("HTTP/1.0 200", 0) == 0 && g.find("\"captured\":false") != std::string::npos && g.find("\"capturing\":false") != std::string::npos,
          "baseline http: GET /api/baseline -> 200 status JSON");
    check(c1.rfind("HTTP/1.0 202", 0) == 0 && c1.find("\"capturing\":true") != std::string::npos, "baseline http: POST capture -> 202 Accepted, capturing");
    check(c2.rfind("HTTP/1.0 409", 0) == 0, "baseline http: second capture while running -> 409");
    check(cl.rfind("HTTP/1.0 200", 0) == 0 && cl.find("\"capturing\":false") != std::string::npos, "baseline http: POST clear -> 200, capture cancelled");
    check(nf.rfind("HTTP/1.0 404", 0) == 0, "baseline http: unknown path still 404 through the extra handler");
}

// 8×8 격자 두 개(A·B)를 칸별로 채우는 도우미
struct Grid { int16_t d[64]; uint8_t s[64]; };
Grid emptyGrid() { Grid g; for (int i = 0; i < 64; ++i) { g.d[i] = -1; g.s[i] = 255; } return g; }
void setRow(Grid& g, int row, int n, int16_t mm, uint8_t st = 5) { for (int c = 0; c < n; ++c) { g.d[row * 8 + c] = mm; g.s[row * 8 + c] = st; } }

void testFaceDownRule() {
    tof::FaceDownParams p;
    // near 경계: 150·1200 은 제외, status 5·9 만, 6·10 은 아님
    Grid a = emptyGrid(), b = emptyGrid();
    a.d[0] = 150; a.s[0] = 5; a.d[1] = 151; a.s[1] = 5; a.d[2] = 1199; a.s[2] = 9; a.d[3] = 1200; a.s[3] = 5; a.d[4] = 600; a.s[4] = 6;
    a.d[5] = 600; a.s[5] = 10;
    auto f = tof::faceDownFeatures(a.d, a.s, b.d, b.s, p);
    check(f.up == 2 && f.lo == 0 && f.topA == 0 && f.topB == 8 && !f.has_dlo && f.has_dup && std::fabs(f.dup - 0.675) < 1e-12 && !f.flag,
          "facedown: near = status 5|9 and 150 < d < 1200 (bounds excluded), topA = first row with >= 2, even-count median = mean of middle two");
    // 몸이 아래(엎드림): 위 4행 비고, A·B 아래 4행에 near 각 14칸 (lo 28) 거리 500 → flag
    a = emptyGrid(); b = emptyGrid();
    for (int r = 4; r < 8; ++r) { setRow(a, r, r < 6 ? 4 : 3, 500); setRow(b, r, r < 6 ? 4 : 3, 520); }
    f = tof::faceDownFeatures(a.d, a.s, b.d, b.s, p);
    check(f.up == 0 && f.lo == 28 && f.topA == 4 && f.topB == 4 && f.has_dlo && std::fabs(f.dlo - 0.51) < 1e-12 && !f.has_dup && f.flag && !f.seated,
          "facedown: low-body branch (up 0, lo 28, dlo 0.51, top rows 4/4) flags");
    a.d[3 * 8 + 0] = 500; a.s[3 * 8 + 0] = 5; a.d[3 * 8 + 1] = 500; a.s[3 * 8 + 1] = 5;     // A 3행에 near 2칸 → topA 3
    f = tof::faceDownFeatures(a.d, a.s, b.d, b.s, p);
    check(f.topA == 3 && f.up == 2 && !f.flag, "facedown: topA < 4 breaks the low-body branch");
    // 몸이 가까움: lo ≥ 30, dlo ≤ 0.40, 위쪽 near 의 중앙값 ≤ 0.45
    a = emptyGrid(); b = emptyGrid();
    for (int r = 4; r < 8; ++r) { setRow(a, r, 8, 350); setRow(b, r, 8, 380); }
    setRow(a, 1, 8, 440); setRow(b, 1, 8, 440);                       // up 16 (몸이 아래 분기는 실패), dup 0.44
    f = tof::faceDownFeatures(a.d, a.s, b.d, b.s, p);
    check(f.lo == 64 && f.up == 16 && std::fabs(f.dlo - 0.365) < 1e-12 && std::fabs(f.dup - 0.44) < 1e-12 && f.flag,
          "facedown: near-body branch (lo 64, dlo 0.365, dup 0.44) flags even with an upper body");
    setRow(a, 1, 8, 460); setRow(b, 1, 8, 460);
    check(!tof::faceDownFeatures(a.d, a.s, b.d, b.s, p).flag, "facedown: near-body branch needs dup <= 0.45");
    // 착석: up ≥ 8, topA·topB ≤ 3
    a = emptyGrid(); b = emptyGrid();
    for (int r = 2; r < 8; ++r) { setRow(a, r, 4, 650); setRow(b, r, 4, 680); }
    f = tof::faceDownFeatures(a.d, a.s, b.d, b.s, p);
    check(f.seated && f.up == 16 && f.topA == 2 && f.topB == 2 && !f.flag, "facedown: seated geometry (up 16, top rows 2/2)");
    // 창: 5 Hz, flag 비율 ≥ 0.7 에서 켜지고 ≥ 0.4 동안 유지, t ≥ now − 4.0 포함
    Grid down_a = emptyGrid(), down_b = emptyGrid(), up_a = emptyGrid(), up_b = emptyGrid();
    for (int r = 4; r < 8; ++r) { setRow(down_a, r, 7, 500); setRow(down_b, r, 7, 500); }
    for (int r = 2; r < 8; ++r) { setRow(up_a, r, 4, 650); setRow(up_b, r, 4, 680); }
    tof::FaceDownRule rule;
    std::vector<bool> on;
    for (int k = 0; k < 40; ++k) {                       // 8 s 엎드림
        rule.update(k * 0.2, down_a.d, down_a.s, down_b.d, down_b.s);
        on.push_back(rule.on());
    }
    check(on[0] && rule.share() == 1.0, "facedown window: first flagged frame alone is share 1.0 -> on");
    // 10 프레임 착석 → 50 프레임 엎드림 → 40 프레임 착석. 켜짐은 share 가 처음 ≥ 0.7 인 프레임, 꺼짐은 처음 < 0.4 인 프레임이고,
    // 그 사이에는 share < 0.7 이어도 켜져 있다(이력). 창은 t ≥ now − 4.0 (부동소수 경계는 기준 구현과 같은 double 비교)
    tof::FaceDownRule r2;
    std::vector<double> sh;
    std::vector<bool> o2;
    for (int k = 0; k < 100; ++k) {
        const bool down = k >= 10 && k < 60;
        r2.update(k * 0.2, down ? down_a.d : up_a.d, down ? down_a.s : up_a.s, down ? down_b.d : up_b.d, down ? down_b.s : up_b.s);
        sh.push_back(r2.share()); o2.push_back(r2.on());
    }
    int first_on = -1, first_off = -1;
    bool held_below = false, ok_hyst = true;
    for (int k = 1; k < 100; ++k) {
        if (first_on < 0 && o2[k]) first_on = k;
        if (first_on >= 0 && first_off < 0 && !o2[k]) first_off = k;
        if (o2[k] && sh[k] < 0.7) held_below = true;
        if (o2[k] != (sh[k] >= (o2[k - 1] ? 0.4 : 0.7))) ok_hyst = false;
    }
    check(first_on > 10 && sh[first_on] >= 0.7 && sh[first_on - 1] < 0.7 && first_on <= 25,
          "facedown window: turns on at the first frame whose 4 s share reaches 0.7");
    check(first_off > 60 && sh[first_off] < 0.4 && sh[first_off - 1] >= 0.4 && held_below && ok_hyst,
          "facedown window: stays on while share >= 0.4 (hysteresis), off when it drops below");
    r2.reset();
    check(!r2.on() && r2.share() == 0.0, "facedown window: reset clears");
}

// 모델 입력 변환: 서비스(센서 1개)의 센서 확률 == 같은 변환을 손으로 한 입력을 PostureNet 에 직접 넣은 것
void testModelInputTransforms(const std::string& bin) {
    const double tan_half = std::tan(22.5 * 3.14159265358979323846 / 180.0);
    auto factor = [&](int r, int c) {
        const double x = (c + 0.5) * 2.0 * tan_half / 8.0 - tan_half, y = (r + 0.5) * 2.0 * tan_half / 8.0 - tan_half;
        return std::sqrt(1.0 + x * x + y * y);
    };
    check(std::fabs(factor(3, 3) - 1.0027) < 1e-4 && std::fabs(factor(0, 0) - 1.1237) < 1e-4 && std::fabs(factor(7, 7) - factor(0, 0)) < 1e-12,
          "radial factor: centre 1.003, corner 1.124, symmetric");
    int16_t d[64]; uint8_t st[64];
    for (int i = 0; i < 64; ++i) {
        d[i] = (int16_t)(500 + 37 * i); st[i] = (uint8_t)(i % 7 == 0 ? 255 : i % 11 == 0 ? 2 : 5);
        if (st[i] == 255) d[i] = -1;
    }
    for (int mode = 0; mode < 4; ++mode) {
        const bool radial = mode & 1;
        const int fill = (mode & 2) ? 3000 : 0;
        tof::DeviceConfig cfg;
        cfg.radial = radial; cfg.fill_notarget_mm = fill;
        tof::DevicePosture dev(cfg);
        std::string err;
        dev.loadModel(bin, &err);
        const int s = dev.addSensor("t");
        tof::PostureNet net;
        net.load(bin, &err);
        int16_t din[64]; uint8_t sin[64];
        for (int i = 0; i < 64; ++i) {
            din[i] = d[i]; sin[i] = st[i];
            if (radial && tof::zoneValid(din[i], sin[i])) din[i] = (int16_t)(din[i] * factor(i / 8, i % 8) + 0.5);
            if (fill && sin[i] == 255) { din[i] = (int16_t)fill; sin[i] = 5; }
        }
        float logits[tof::kMaxClasses], probs[tof::kMaxClasses];
        double maxd = 0.0;
        for (int k = 0; k < 5; ++k) {
            tof::TofLine l = gridFrame(k + 1, 100 * k, d, st);
            for (int i = 0; i < 64; ++i) { l.frame.raw_d[i] = d[i]; l.frame.raw_st[i] = st[i]; }
            dev.feed(s, l, 100 * k);
            dev.tick(100 * k);
            net.step(din, sin, logits);
            tof::softmax(logits, net.classes, probs);
            const auto ss = dev.sensors(100 * k);
            for (int c = 0; c < net.classes; ++c) maxd = std::max(maxd, std::fabs((double)ss[0].probs[c] - probs[c]));
        }
        char what[160];
        std::snprintf(what, sizeof what, "model input (radial %d, fill %d): service == hand-transformed input to PostureNet (max |dp| %.1g)", radial, fill, maxd);
        check(maxd == 0.0, what);
        // /raw 는 원본 그대로
        const std::string raw = dev.rawJson(500);
        check(raw.find("\"dist_mm\":[-1,537,574") != std::string::npos, "model input transforms leave /raw as received");
        const std::string sj = dev.stateJson(500);
        char want[96];
        std::snprintf(want, sizeof want, "\"radial\":%s,\"fill_notarget_mm\":%d", radial ? "true" : "false", fill);
        check(sj.find(want) != std::string::npos, "state: radial and fill_notarget_mm fields");
    }
}

// 서비스에서 규칙: A·B 같은 ts 짝마다 평가, 켜지면 face_down conf 0.995·present true, 모델 출력은 model_output/instant 에
void testFaceDownService(const std::string& bin) {
    Grid down_a = emptyGrid(), down_b = emptyGrid();
    for (int r = 4; r < 8; ++r) { setRow(down_a, r, 7, 500); setRow(down_b, r, 7, 500); }
    auto frame = [&](int id, uint32_t ms, const Grid& g) {
        tof::TofLine l = gridFrame(ms / 100 + 1, ms, g.d, g.s);
        l.sensor_id = id;
        for (int i = 0; i < 64; ++i) { l.frame.raw_d[i] = g.d[i]; l.frame.raw_st[i] = g.s[i]; }
        return l;
    };
    tof::DeviceConfig cfg;
    cfg.facedown_rule = true;
    tof::DevicePosture dev(cfg);
    std::string err;
    dev.loadModel(bin, &err);
    const int s0 = dev.addSensor("a"), s1 = dev.addSensor("b");
    for (int k = 0; k < 10; ++k) {
        const uint32_t ms = 200 * k;
        dev.feed(s0, frame(0, ms, down_a), ms);
        dev.feed(s1, frame(1, ms, down_b), ms);
        dev.tick(ms); dev.tick(ms + 100);
    }
    const auto r = dev.result();
    const auto fs = dev.faceDownState();
    const std::string sj = dev.stateJson(2000);
    check(fs.pairs == 10 && fs.on && r.fd_on && r.display_argmax == 3 && std::fabs(r.display[3] - 0.995f) < 1e-6f,
          "facedown service: A/B pairs evaluated, rule on -> posture face_down with conf 0.995");
    float sum = 0.f;
    for (int i = 0; i < dev.classes(); ++i) sum += r.display[i];
    check(std::fabs(sum - 1.f) < 1e-4f && sj.find("\"posture\":\"face_down\"") != std::string::npos && sj.find("\"present\":true") != std::string::npos &&
              sj.find("\"rule\":{\"enabled\":true,\"face_down\":true,\"applied\":true,\"conf\":0.995") != std::string::npos &&
              sj.find("\"model_output\":{\"posture\":\"" + std::string(tof::DevicePosture::labelName(r.smooth_argmax)) + "\"") != std::string::npos,
          "facedown service: probs sum to 1, present true, state rule block, model output kept in model_output");
    // 한쪽 센서만 오면 평가 안 함, ts 가 다르면 짝 아님
    tof::DevicePosture d2(cfg);
    d2.loadModel(bin, &err);
    const int t0 = d2.addSensor("a"), t1 = d2.addSensor("b");
    d2.feed(t0, frame(0, 100, down_a), 100);
    d2.feed(t1, frame(1, 300, down_b), 300);
    d2.feed(t0, frame(0, 500, down_a), 500);
    check(d2.faceDownState().pairs == 0, "facedown service: frames with different ts_ms are not a pair");
    // 끈 기본 설정: 짝도 안 만들고 rule.enabled false
    tof::DevicePosture d3;
    d3.loadModel(bin, &err);
    const int u0 = d3.addSensor("a"), u1 = d3.addSensor("b");
    d3.feed(u0, frame(0, 100, down_a), 100);
    d3.feed(u1, frame(1, 100, down_b), 100);
    d3.tick(100);
    check(d3.faceDownState().pairs == 0 && !d3.result().fd_on && d3.stateJson(100).find("\"rule\":{\"enabled\":false") != std::string::npos,
          "facedown service: off by default (no evaluation, display = model)");
    // 리셋(POST /api/reset)은 규칙 창도 비운다
    dev.reset(5000);
    check(!dev.faceDownState().on && dev.faceDownState().share == 0.0, "facedown service: reset clears the rule window");
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
    testPlacement(argv[1]);
    testPlacementHttp(argv[1]);
    testBaseline(argv[1]);
    testBaselineHttp(argv[1]);
    testFaceDownRule();
    testModelInputTransforms(argv[1]);
    testFaceDownService(argv[1]);
    if (argc > 3) {
        const std::vector<std::string> files(argv + 3, argv + argc);
        testReplay(argv[1], files, tof::DeviceConfig{}, "defaults (resets off)");
        tof::DeviceConfig on;
        on.reset_period_s = 900; on.away_reset_s = 60;
        testReplay(argv[1], files, on, "enabled 900/60");
    }
    std::printf("%s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
