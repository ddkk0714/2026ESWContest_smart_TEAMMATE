#include "TofLine.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>

namespace deskmate {
namespace {

// snprintf 를 이어 붙이는 작은 도우미. 한 번이라도 넘치면 ok 가 false 로 남는다.
struct Out {
  Out(char *b, size_t c) : buf(b), cap(c) {}

  char *buf;
  size_t cap;
  size_t len = 0;
  bool ok = true;

  void add(const char *fmt, ...) {
    if (!ok) return;
    va_list args;
    va_start(args, fmt);
    const int n = vsnprintf(buf + len, cap - len, fmt, args);
    va_end(args);
    if (n < 0 || static_cast<size_t>(n) >= cap - len) {
      ok = false;
      return;
    }
    len += static_cast<size_t>(n);
  }

  void num(bool valid, float v, int digits) {
    if (valid && isfinite(v)) {
      add("%.*f", digits, static_cast<double>(v));
    } else {
      add("null");
    }
  }

  size_t finish() {
    add("\n");
    return ok ? len : 0;
  }
};

}  // namespace

void remapZones(const int16_t *src_d, const uint8_t *src_st, int16_t *dst_d, uint8_t *dst_st,
                uint8_t w, uint8_t h, bool flip_rows, bool flip_cols) {
  for (uint8_t r = 0; r < h; ++r) {
    const uint8_t sr = flip_rows ? static_cast<uint8_t>(h - 1 - r) : r;
    for (uint8_t c = 0; c < w; ++c) {
      const uint8_t sc = flip_cols ? static_cast<uint8_t>(w - 1 - c) : c;
      const uint16_t dst = static_cast<uint16_t>(r * w + c);
      const uint16_t src = static_cast<uint16_t>(sr * w + sc);
      dst_d[dst] = src_d[src];
      dst_st[dst] = src_st[src];
    }
  }
}

size_t formatTofLine(char *buf, size_t cap, uint32_t seq, uint32_t ms, uint8_t w, uint8_t h,
                     const int16_t *d, const uint8_t *st, const TofFeatures &f) {
  if (buf == nullptr || cap == 0) return 0;
  Out o{buf, cap};
  const uint16_t n = static_cast<uint16_t>(w * h);
  o.add("{\"t\":\"tof\",\"seq\":%lu,\"ms\":%lu,\"w\":%u,\"h\":%u,\"d\":[",
        static_cast<unsigned long>(seq), static_cast<unsigned long>(ms), w, h);
  for (uint16_t i = 0; i < n; ++i) o.add(i ? ",%d" : "%d", d[i]);
  o.add("],\"st\":[");
  for (uint16_t i = 0; i < n; ++i) o.add(i ? ",%u" : "%u", st[i]);
  o.add("],\"rule\":{\"present\":%s,\"posture\":\"%s\",\"conf\":", f.present ? "true" : "false",
        tofPostureName(f.posture));
  o.num(true, f.posture_confidence, 2);
  o.add(",\"baseline\":%s,\"head_mm\":", f.baseline_ready ? "true" : "false");
  o.num(f.geometry_valid, f.head_depth_mm, 0);
  o.add(",\"torso_mm\":");
  o.num(f.torso_valid, f.torso_depth_mm, 0);
  o.add(",\"drop_mm\":");
  o.num(f.baseline_ready && f.geometry_valid, f.head_drop_mm, 0);
  o.add(",\"chin\":");
  o.num(true, f.chin_arm_ratio, 2);
  o.add(",\"motion\":");
  o.num(true, f.motion_score, 2);
  o.add(",\"nods\":%u}}", f.nods_in_window);
  return o.finish();
}

size_t formatHelloLine(char *buf, size_t cap, uint32_t ms, const TofLoggerInfo &info) {
  if (buf == nullptr || cap == 0) return 0;
  Out o{buf, cap};
  o.add("{\"t\":\"tof_hello\",\"ms\":%lu,\"sensor\":\"%s\",\"w\":%u,\"h\":%u,\"hz\":%u,"
        "\"flip_rows\":%s,\"flip_cols\":%s,\"target_order\":\"%s\",\"fw\":%u}",
        static_cast<unsigned long>(ms), info.sensor, info.w, info.h, info.hz,
        info.flip_rows ? "true" : "false", info.flip_cols ? "true" : "false", info.target_order,
        info.fw_version);
  return o.finish();
}

size_t formatErrorLine(char *buf, size_t cap, uint32_t ms, const char *where, int code) {
  if (buf == nullptr || cap == 0) return 0;
  Out o{buf, cap};
  o.add("{\"t\":\"tof_err\",\"ms\":%lu,\"where\":\"%s\",\"code\":%d}",
        static_cast<unsigned long>(ms), where, code);
  return o.finish();
}

}  // namespace deskmate
