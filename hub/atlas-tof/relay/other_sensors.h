// ToF 격자(0x05) 말고 나머지 프레임의 마지막 값 — HTTP /raw 의 "other" 로 같이 싣는다 (2026-10-22, 모든 센서 raw 확인).
// 바이트 배치(모두 little-endian):
//   0x20 mmwave 11 B: present u8 · motion_state u8(0 none/1 still/2 active) · motion_level u8 · distance_cm u16(0xFFFF null)
//                     · resp_bpm u8(0xFF null) · resp_valid u8 · heart_bpm u8(0xFF null) · heart_valid u8
//                     · drowsy_state u8(0 NOPERSON/1 NOLOCK/2 WARMUP/3 AWAKE/4 DROWSY) · valid u8       (main.cpp writeMmwaveUart)
//   0x10 env 9 B:     co2_ppm u16 · temp_c×10 i16 · humidity×10 u16 · lux u16 · valid u8(b0 co2 b1 temp b2 hum b3 lux)  (EnvironmentPayload.cpp)
//   0x03 tof_feature 26 B, 0x04 tof_posture 4 B: sensors/tof/TofPayload.h
//   0xF0 heartbeat 7/8 B: uptime_ms u32 · fw u16 · mmwave_ready u8 · [tof_ok u8 b0 A b1 B]                (main.cpp writeHeartbeatUart)
// 해석은 hub/deskmate_hub/ingest/uart_frame.py decode_payload 와 같다.
#pragma once
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace other {

enum Kind { kMmwave = 0, kEnv, kFeature, kPosture, kHeartbeat, kN };

struct Last {
    bool have = false;
    uint16_t seq = 0;
    uint32_t ts_ms = 0;
    unsigned long count = 0;
    std::vector<uint8_t> p;
    std::chrono::steady_clock::time_point at;
};

inline Last g[kN];

inline int kindOf(uint8_t type) {
    switch (type) {
    case 0x20: return kMmwave;
    case 0x10: return kEnv;
    case 0x03: return kFeature;
    case 0x04: return kPosture;
    case 0xF0: return kHeartbeat;
    default: return -1;
    }
}

// CRC 를 통과한 프레임마다 부른다. 0x05 는 여기 안 온다(격자는 LastFrame 이 맡는다).
inline void record(uint8_t type, uint16_t seq, uint32_t ts_ms, const uint8_t* p, size_t n) {
    const int k = kindOf(type);
    if (k < 0) return;
    Last& L = g[k];
    L.have = true;
    L.seq = seq;
    L.ts_ms = ts_ms;
    L.p.assign(p, p + n);
    L.at = std::chrono::steady_clock::now();
    ++L.count;
}

inline uint16_t u16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
inline int16_t i16(const uint8_t* p) { return static_cast<int16_t>(u16(p)); }
inline uint32_t u32(const uint8_t* p) { return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24); }
inline std::string q(const std::string& s) { return "\"" + s + "\""; }
inline std::string bl(bool b) { return b ? "true" : "false"; }
inline std::string nn(bool valid, long v) { return valid ? std::to_string(v) : std::string("null"); }
inline std::string x10(long v) {  // 234 → "23.4", -12 → "-1.2"
    char b[32];
    std::snprintf(b, sizeof b, "%s%ld.%ld", v < 0 ? "-" : "", (v < 0 ? -v : v) / 10, (v < 0 ? -v : v) % 10);
    return b;
}
inline std::string x1000(unsigned long v) {
    char b[32];
    std::snprintf(b, sizeof b, "%lu.%03lu", v / 1000, v % 1000);
    return b;
}

inline std::string head(const Last& L) {
    const long age = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - L.at).count();
    return "\"have\":true,\"age_ms\":" + std::to_string(age) + ",\"seq\":" + std::to_string(L.seq) + ",\"ts_ms\":" + std::to_string(L.ts_ms) +
           ",\"count\":" + std::to_string(L.count) + ",\"len\":" + std::to_string(L.p.size());
}

inline const char* motionName(uint8_t v) { return v == 1 ? "still" : v == 2 ? "active" : "none"; }
inline const char* drowsyName(uint8_t v) {
    static const char* n[] = {"NOPERSON", "NOLOCK", "WARMUP", "AWAKE", "DROWSY"};
    return v < 5 ? n[v] : "NOLOCK";
}
inline const char* postureName(uint8_t v) {
    static const char* n[] = {"unknown", "upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away"};
    return v < 8 ? n[v] : "unknown";
}

inline std::string mmwaveJson() {
    const Last& L = g[kMmwave];
    if (!L.have || L.p.size() < 11) return "{\"have\":false}";
    const uint8_t* p = L.p.data();
    const uint16_t dist = u16(p + 3);
    return "{" + head(L) + ",\"present\":" + bl(p[0]) + ",\"motion_state\":" + q(motionName(p[1])) + ",\"motion_level\":" + std::to_string(p[2]) +
           ",\"distance_cm\":" + nn(dist != 0xFFFF, dist) + ",\"resp_bpm\":" + nn(p[5] != 0xFF, p[5]) + ",\"resp_valid\":" + bl(p[6]) +
           ",\"heart_bpm\":" + nn(p[7] != 0xFF, p[7]) + ",\"heart_valid\":" + bl(p[8]) + ",\"drowsy_state\":" + q(drowsyName(p[9])) +
           ",\"valid\":" + bl(p[10]) + "}";
}

inline std::string envJson() {
    const Last& L = g[kEnv];
    if (!L.have || L.p.size() < 9) return "{\"have\":false}";
    const uint8_t* p = L.p.data();
    const uint8_t v = p[8];
    return "{" + head(L) + ",\"co2_ppm\":" + nn(v & 1, u16(p)) + ",\"temp_c\":" + ((v & 2) ? x10(i16(p + 2)) : std::string("null")) +
           ",\"humidity_pct\":" + ((v & 4) ? x10(u16(p + 4)) : std::string("null")) + ",\"lux\":" + nn(v & 8, u16(p + 6)) +
           ",\"co2_valid\":" + bl(v & 1) + ",\"temp_valid\":" + bl(v & 2) + ",\"humidity_valid\":" + bl(v & 4) + ",\"lux_valid\":" + bl(v & 8) + "}";
}

inline std::string featureJson() {
    const Last& L = g[kFeature];
    if (!L.have || L.p.size() < 26) return "{\"have\":false}";
    const uint8_t* p = L.p.data();
    const int16_t head_delta = i16(p + 4);
    const uint16_t nod = u16(p + 6);
    const uint16_t centroid = u16(p + 15);
    const int16_t tilt = i16(p + 18);
    const uint8_t f = p[24];
    return "{" + head(L) + ",\"present\":" + bl(p[0]) + ",\"posture\":" + q(postureName(p[1])) + ",\"confidence_pct\":" + std::to_string(p[2]) +
           ",\"motion_pct\":" + std::to_string(p[3]) + ",\"head_delta_mm\":" + nn(head_delta != 0x7FFF, head_delta) +
           ",\"nod_rate_hz\":" + (nod != 0xFFFF ? x1000(nod) : std::string("null")) + ",\"valid_zones\":" + std::to_string(u16(p + 8)) +
           ",\"grid_w\":" + std::to_string(p[10]) + ",\"grid_h\":" + std::to_string(p[11]) + ",\"coverage_pct\":" + std::to_string(p[12]) +
           ",\"presence_count\":" + std::to_string(u16(p + 13)) + ",\"centroid_mm\":" + nn(centroid != 0xFFFF, centroid) +
           ",\"head_row_pct\":" + nn(p[17] != 0xFF, p[17]) + ",\"shoulder_tilt_mm\":" + nn(tilt != 0x7FFF, tilt) +
           ",\"motion_mm_s\":" + std::to_string(u16(p + 20)) + ",\"change_per_min\":" + std::to_string(p[22]) + ",\"baseline_dev_pct\":" + std::to_string(p[23]) +
           ",\"baseline_ready\":" + bl(f & 0x01) + ",\"drowsy\":" + bl(f & 0x02) + ",\"geometry_valid\":" + bl(f & 0x04) +
           ",\"sensor_a\":" + bl(f & 0x08) + ",\"sensor_b\":" + bl(f & 0x10) + ",\"nods\":" + std::to_string(p[25]) + "}";
}

inline std::string postureJson() {
    const Last& L = g[kPosture];
    if (!L.have || L.p.size() < 4) return "{\"have\":false}";
    const uint8_t* p = L.p.data();
    return "{" + head(L) + ",\"present\":" + bl(p[0]) + ",\"posture\":" + q(postureName(p[1])) + ",\"confidence_pct\":" + std::to_string(p[2]) +
           ",\"drowsy\":" + bl(p[3]) + "}";
}

inline std::string heartbeatJson() {
    const Last& L = g[kHeartbeat];
    if (!L.have || L.p.size() < 7) return "{\"have\":false}";
    const uint8_t* p = L.p.data();
    char fw[16];
    std::snprintf(fw, sizeof fw, "0x%04x", u16(p + 4));
    const bool has_tof = L.p.size() >= 8;
    return "{" + head(L) + ",\"uptime_ms\":" + std::to_string(u32(p)) + ",\"fw\":" + q(fw) + ",\"mmwave_ready\":" + bl(p[6]) +
           ",\"tof_ok_mask\":" + nn(has_tof, has_tof ? p[7] : 0) + ",\"tof_a\":" + bl(has_tof && (p[7] & 1)) + ",\"tof_b\":" + bl(has_tof && (p[7] & 2)) + "}";
}

// rawJson 의 닫는 "]}" 대신 "]" 뒤에 붙인다: ,"other":{...}}
inline std::string tail() {
    return ",\"other\":{\"mmwave\":" + mmwaveJson() + ",\"env\":" + envJson() + ",\"tof_feature\":" + featureJson() +
           ",\"tof_posture\":" + postureJson() + ",\"heartbeat\":" + heartbeatJson() + "}}";
}

}  // namespace other
