#include "tof_line.h"

#include <cstdlib>
#include <cstring>

namespace tof {
namespace {

// line 에서 "key": 다음 위치. 없으면 npos.
// "key" 와 ':' 사이·뒤의 공백은 건너뛴다(Python json.dumps 기본값이 ": " 이다).
size_t findKey(const std::string& line, const char* key) {
    std::string pat = "\"";
    pat += key;
    pat += "\"";
    size_t from = 0;
    for (;;) {
        size_t p = line.find(pat, from);
        if (p == std::string::npos) return p;
        p += pat.size();
        while (p < line.size() && (line[p] == ' ' || line[p] == '	')) ++p;
        if (p < line.size() && line[p] == ':') {
            ++p;
            while (p < line.size() && (line[p] == ' ' || line[p] == '	')) ++p;
            return p;
        }
        from = p;   // 값 안에 같은 글자가 있었다 — 다음 것을 찾는다
    }
}

bool readInt(const std::string& line, const char* key, long& value) {
    const size_t p = findKey(line, key);
    if (p == std::string::npos) return false;
    char* end = nullptr;
    value = std::strtol(line.c_str() + p, &end, 10);
    return end != line.c_str() + p;
}

bool readBool(const std::string& line, const char* key, bool& value) {
    const size_t p = findKey(line, key);
    if (p == std::string::npos) return false;
    if (line.compare(p, 4, "true") == 0) { value = true; return true; }
    if (line.compare(p, 5, "false") == 0) { value = false; return true; }
    return false;
}

bool readString(const std::string& line, const char* key, std::string& value) {
    const size_t p = findKey(line, key);
    if (p == std::string::npos || p >= line.size() || line[p] != '"') return false;
    const size_t e = line.find('"', p + 1);
    if (e == std::string::npos) return false;
    value = line.substr(p + 1, e - p - 1);
    return true;
}

// "key":[a,b,c] 를 최대 cap 개 읽어 count 에 개수를 남긴다. 개수가 cap 을 넘으면 false.
bool readIntArray(const std::string& line, const char* key, long* out, int cap, int& count) {
    size_t p = findKey(line, key);
    if (p == std::string::npos || p >= line.size() || line[p] != '[') return false;
    ++p;
    count = 0;
    const char* s = line.c_str();
    while (p < line.size()) {
        while (p < line.size() && (s[p] == ' ' || s[p] == ',')) ++p;
        if (p < line.size() && s[p] == ']') return true;
        char* end = nullptr;
        const long v = std::strtol(s + p, &end, 10);
        if (end == s + p) return false;
        if (count >= cap) return false;
        out[count++] = v;
        p = static_cast<size_t>(end - s);
    }
    return false;
}

// "F,<ms>,v,v,…" 의 콤마 구분 정수를 최대 cap 개. 실패면 -1, 아니면 개수.
int readCsvInts(const char* s, long* out, int cap) {
    int n = 0;
    while (*s) {
        while (*s == ',' || *s == ' ') ++s;
        if (!*s || *s == '\r' || *s == '\n') break;
        char* end = nullptr;
        const long v = std::strtol(s, &end, 10);
        if (end == s) return -1;
        if (n >= cap) return -1;
        out[n++] = v;
        s = end;
    }
    return n;
}

void fillFrame(TofFrame& f, const long* d, const long* st, int n, bool has_status) {
    f.has_status = has_status;
    for (int i = 0; i < n; ++i) {
        f.d[i] = static_cast<int16_t>(d[i] < -32768 ? -32768 : d[i] > 32767 ? 32767 : d[i]);
        if (has_status) f.st[i] = static_cast<uint8_t>(st[i] < 0 || st[i] > 255 ? 255 : st[i]);
        else f.st[i] = d[i] > 0 ? 5 : 255;
        f.raw_d[i] = f.d[i]; f.raw_st[i] = f.st[i];           // /raw 원본 = 이 형식은 변환이 없다
    }
}

// JSON 문자열 값(따옴표 안)을 풀어 out 에. p 는 여는 따옴표 다음. 닫는 따옴표 다음 위치를 돌려주고, 실패면 npos.
size_t readJsonString(const std::string& s, size_t p, std::string& out) {
    out.clear();
    while (p < s.size()) {
        const char c = s[p++];
        if (c == '"') return p;
        if (c != '\\') { out += c; continue; }
        if (p >= s.size()) return std::string::npos;
        const char e = s[p++];
        switch (e) {
        case '"': out += '"'; break;
        case '\\': out += '\\'; break;
        case '/': out += '/'; break;
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'b': case 'f': break;
        case 'u': {                                   // 센서 줄은 ASCII 라 \u00XX 만 의미 있다
            if (p + 4 > s.size()) return std::string::npos;
            const long cp = std::strtol(s.substr(p, 4).c_str(), nullptr, 16);
            if (cp > 0 && cp < 0x80) out += static_cast<char>(cp);
            p += 4;
            break;
        }
        default: return std::string::npos;
        }
    }
    return std::string::npos;
}

}  // namespace

namespace {
// "key" : "value" 의 value 를 푼다. Python json.dumps 기본값은 ": " 처럼 공백을 넣으므로 공백을 건너뛴다.
bool readSpacedString(const std::string& line, size_t from, const char* key, std::string& value) {
    const std::string pat = std::string("\"") + key + "\"";
    size_t p = line.find(pat, from);
    if (p == std::string::npos) return false;
    p += pat.size();
    while (p < line.size() && (line[p] == ' ' || line[p] == '\t')) ++p;
    if (p >= line.size() || line[p] != ':') return false;
    ++p;
    while (p < line.size() && (line[p] == ' ' || line[p] == '\t')) ++p;
    if (p >= line.size() || line[p] != '"') return false;
    return readJsonString(line, p + 1, value) != std::string::npos;
}
}  // namespace

bool unwrapRecordLine(const std::string& line, std::string& raw, std::string& port) {
    const size_t b = line.find_first_not_of(" \t\r\n");
    if (b == std::string::npos || line.compare(b, 9, "{\"host_t\"") != 0) return false;
    if (!readSpacedString(line, b, "port", port)) port.clear();
    return readSpacedString(line, b, "raw", raw);
}

namespace {
bool peekLineMsRaw(const std::string& line, uint32_t& ms);
}  // namespace

bool peekLineMs(const std::string& line, uint32_t& ms) {
    std::string raw, port;
    if (unwrapRecordLine(line, raw, port)) return peekLineMsRaw(raw, ms);
    return peekLineMsRaw(line, ms);
}

namespace {
bool peekLineMsRaw(const std::string& line, uint32_t& ms) {
    long v = 0;
    if (readInt(line, "ms", v) && v >= 0) { ms = static_cast<uint32_t>(v); return true; }
    const size_t b = line.find_first_not_of(" \t\r\n");
    if (b != std::string::npos && b + 2 <= line.size() && (line[b] == 'F' || line[b] == 'P') && line[b + 1] == ',') {
        char* end = nullptr;
        v = std::strtol(line.c_str() + b + 2, &end, 10);
        if (end != line.c_str() + b + 2 && v >= 0) { ms = static_cast<uint32_t>(v); return true; }
    }
    return false;
}
}  // namespace

bool looksLikeTofLine(const std::string& line) {
    const size_t b = line.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return false;
    if (line.compare(b, 5, "{\"t\":") == 0 || line.compare(b, 10, "{\"host_t\":") == 0) return true;
    if (line.compare(b, 16, "UART\t{\"type\":5,") == 0) return true;
    if (b + 2 <= line.size() && (line[b] == 'F' || line[b] == 'P') && line[b + 1] == ',' && line.size() - b > 3 &&
        line[b + 2] >= '0' && line[b + 2] <= '9')
        return true;
    return line.find("VL53L8CX") != std::string::npos || line.find("Ranging started") != std::string::npos;
}

namespace {
int hexNibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// UART\t{"type":5,...} 줄 → 프레임. 0x05 가 아니면 false(다른 TYPE 은 이 서비스가 쓰지 않는다).
bool parseUartZonesLine(const std::string& line, TofLine& out) {
    long type = -1, seq = 0, ts = 0;
    const std::string json = line.substr(5);
    if (!readInt(json, "type", type) || type != 5) return false;
    readInt(json, "seq", seq);
    readInt(json, "ts_ms", ts);
    std::string hex;
    if (!readString(json, "payload_hex", hex) || hex.size() % 2 != 0) return false;
    std::string p(hex.size() / 2, '\0');
    for (size_t i = 0; i < p.size(); ++i) {
        const int hi = hexNibble(hex[2 * i]), lo = hexNibble(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        p[i] = static_cast<char>((hi << 4) | lo);
    }
    const auto u8 = [&](size_t o) { return static_cast<uint8_t>(p[o]); };
    const auto u16 = [&](size_t o) { return static_cast<uint16_t>(u8(o) | (u8(o + 1) << 8)); };
    if (p.size() < 8) return false;
    if (u8(0) != 1) {
        // 표준 배치(data-spec §13.1): grid_w · grid_h · sensor · sensors · frame_id · sensor_mask · flags · reserved
        const int gw = u8(0), gh = u8(1), gn = gw * gh;
        if (gw <= 0 || gh <= 0 || gn > kMaxZones || p.size() < 8 + 3 * static_cast<size_t>(gn)) return false;
        const int sensor = u8(2), mask = u8(5);
        if (sensor >= 8 || !((mask >> sensor) & 1)) return false;
        TofFrame& g = out.frame;
        g.w = gw; g.h = gh;
        g.seq = u8(4);
        g.seq16 = true;
        g.seq_mask = 0xFFu;
        g.ms = static_cast<uint32_t>(ts);
        g.has_status = true;
        for (int i = 0; i < gn; ++i) {
            const int16_t dmm = static_cast<int16_t>(u16(8 + 2 * i));
            const uint8_t st = u8(8 + 2 * gn + i);
            g.d[i] = (dmm <= 0 || st == 255) ? int16_t(-1) : dmm;
            g.st[i] = st;
            g.raw_d[i] = dmm; g.raw_st[i] = st;
        }
        g.flip_rows_hint = 0;                                      // 펌웨어가 이미 장면 방향으로 돌려 보냈다
        g.flip_cols_hint = 0;
        g.uart_seq = static_cast<uint32_t>(seq);
        g.frame_id = u8(4); g.sensor_mask = mask; g.raw_flags = u8(6);
        out.sensor_id = sensor;
        out.kind = LineKind::Frame;
        return true;
    }
    const int w = u8(3), h = u8(4), n = w * h;
    if (w <= 0 || h <= 0 || n > kMaxZones || p.size() < 8 + 3 * static_cast<size_t>(n)) return false;
    const uint8_t flags = u8(2);
    TofFrame& f = out.frame;
    if (!(flags & 1)) {                                            // valid=0: 새 측정 없음. 순서 추적용 frame_seq 만
        f.seq = u16(6);
        f.ms = static_cast<uint32_t>(ts);
        f.seq16 = true;
        out.sensor_id = u8(1);
        out.kind = LineKind::Idle;
        return true;
    }
    f.w = w; f.h = h;
    f.seq = u16(6);
    f.ms = static_cast<uint32_t>(ts);
    f.has_status = (flags & 2) != 0;
    for (int i = 0; i < n; ++i) {
        // 0 = 타깃 없음(펌웨어가 센서 원본 -1 을 0 으로 보냄), 0xFFFF 도 무효. 학습 규약의 "없음 = -1" 로 바꾼다.
        const uint16_t dmm = u16(8 + 2 * i);
        f.d[i] = (dmm == 0 || dmm == 0xFFFF) ? int16_t(-1) : static_cast<int16_t>(dmm > 32767 ? 32767 : dmm);
        const uint8_t st = u8(8 + 2 * n + i);
        f.st[i] = f.has_status ? st : (f.d[i] > 0 ? 5 : 255);
        f.raw_d[i] = f.d[i]; f.raw_st[i] = st;
    }
    f.flip_rows_hint = (flags & 4) ? 1 : 0;
    f.flip_cols_hint = (flags & 8) ? 1 : 0;
    f.seq16 = true;
    f.uart_seq = static_cast<uint32_t>(seq);
    f.raw_flags = flags;
    out.sensor_id = u8(1);
    out.kind = LineKind::Frame;
    return true;
}
}  // namespace

bool parseTofLine(const std::string& raw, TofLine& out) {
    out = TofLine{};
    if (raw.compare(0, 6, "UART\t{") == 0) return parseUartZonesLine(raw, out);
    {
        std::string inner, port;
        if (unwrapRecordLine(raw, inner, port)) return inner.compare(0, 10, "{\"host_t\":") != 0 && parseTofLine(inner, out);
    }
    size_t b = raw.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return false;
    const std::string line = raw.substr(b);
    long v = 0;

    // ---- (B) bring-up CSV ----
    if (line.size() > 2 && line[1] == ',' && (line[0] == 'F' || line[0] == 'P')) {
        if (line[0] == 'F') {
            long vals[2 + 2 * kMaxZones];
            const int n = readCsvInts(line.c_str() + 2, vals, 1 + 2 * kMaxZones);
            if (n < 1 + kMaxZones || vals[0] < 0) return false;
            if (n != 1 + kMaxZones && n != 1 + 2 * kMaxZones) return false;
            TofFrame& f = out.frame;
            f.w = 8; f.h = 8;
            f.ms = static_cast<uint32_t>(vals[0]);
            f.seq = f.ms;
            fillFrame(f, vals + 1, vals + 1 + kMaxZones, kMaxZones, n == 1 + 2 * kMaxZones);
            out.kind = LineKind::Frame;
            return true;
        }
        // P,<ms>,<present>,<posture>,<conf>,…
        size_t c1 = line.find(',', 2);
        size_t c2 = c1 == std::string::npos ? c1 : line.find(',', c1 + 1);
        size_t c3 = c2 == std::string::npos ? c2 : line.find(',', c2 + 1);
        if (c2 == std::string::npos) return false;
        out.rule_posture = line.substr(c2 + 1, (c3 == std::string::npos ? line.size() : c3) - c2 - 1);
        out.kind = LineKind::Rule;
        return true;
    }
    if (line[0] == '#' || line.compare(0, 15, "Ranging started") == 0 || line.find("VL53L8CX I2C bring-up") != std::string::npos ||
        line.compare(0, 9, "init took") == 0 || line.compare(0, 9, "Uploading") == 0) {
        out.info = line;
        if (line.find("Ranging started") != std::string::npos) {   // 센서가 (다시) 시작됐다 → hello 로 취급
            out.kind = LineKind::Hello;
            out.hello.sensor = "VL53L8CX-bringup"; out.hello.w = 8; out.hello.h = 8;
            const size_t at = line.find('@');
            if (at != std::string::npos) out.hello.hz = static_cast<int>(std::strtol(line.c_str() + at + 1, nullptr, 10));
            return true;
        }
        out.kind = LineKind::Info;
        return true;
    }
    if (line.compare(0, 5, "[ERR]") == 0) {
        out.err_where = line.substr(6);
        out.kind = LineKind::Error;
        return true;
    }

    // ---- (A) tof_logger JSON ----
    if (line.compare(0, 5, "{\"t\":") != 0) return false;
    std::string kind;
    if (!readString(line, "t", kind)) return false;

    if (kind == "tof") {
        TofFrame& f = out.frame;
        long w = 0, h = 0, seq = 0, ms = 0;
        if (!readInt(line, "w", w) || !readInt(line, "h", h) || !readInt(line, "seq", seq) || !readInt(line, "ms", ms))
            return false;
        if (w <= 0 || h <= 0 || w * h > kMaxZones || seq < 0 || ms < 0) return false;
        long d[kMaxZones], st[kMaxZones];
        int nd = 0, ns = 0;
        if (!readIntArray(line, "d", d, kMaxZones, nd) || !readIntArray(line, "st", st, kMaxZones, ns)) return false;
        if (nd != w * h || ns != w * h) return false;
        f.w = static_cast<int>(w); f.h = static_cast<int>(h);
        f.seq = static_cast<uint32_t>(seq); f.ms = static_cast<uint32_t>(ms);
        fillFrame(f, d, st, nd, true);
        readString(line, "label", f.label);
        std::string rp;
        if (readString(line, "posture", rp)) out.rule_posture = rp;
        out.kind = LineKind::Frame;
        return true;
    }
    if (kind == "tof_hello") {
        TofHello& h = out.hello;
        if (readInt(line, "ms", v)) h.ms = static_cast<uint32_t>(v);
        if (readInt(line, "w", v)) h.w = static_cast<int>(v);
        if (readInt(line, "h", v)) h.h = static_cast<int>(v);
        if (readInt(line, "hz", v)) h.hz = static_cast<int>(v);
        readBool(line, "flip_rows", h.flip_rows);
        readBool(line, "flip_cols", h.flip_cols);
        readString(line, "sensor", h.sensor);
        out.kind = LineKind::Hello;
        return true;
    }
    if (kind == "tof_err") {
        readString(line, "where", out.err_where);
        if (readInt(line, "code", v)) out.err_code = static_cast<int>(v);
        out.kind = LineKind::Error;
        return true;
    }
    return false;
}

}  // namespace tof
