// 프레임 디코더를 보드 없이 검사한다. 장치(VisionLink)는 건드리지 않는다.
//
//     g++ -std=c++17 -O2 vision_link.cpp vision_link_host_test.cpp -o t && ./t
//
// 실기에서 링크가 이상할 때 "바이트는 오는데 프레임이 0" 인지 "바이트가 0" 인지로
// 원인을 가르는데, 그 전제는 디코더가 옳다는 것이다. 여기서 그걸 고정한다.
#include "vision_link.h"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

int failures = 0;

void check(bool ok, const char* what) {
  std::printf("  %-44s %s\n", what, ok ? "ok" : "FAIL");
  if (!ok) ++failures;
}

// 한 프레임을 와이어 모양으로 만든다. crc 를 주면 일부러 깨진 프레임이 된다.
std::vector<std::uint8_t> framed(std::uint8_t type, std::uint16_t w,
                                 std::uint16_t h,
                                 const std::vector<std::uint8_t>& payload,
                                 int crc_override = -1) {
  const std::uint16_t crc =
      crc_override >= 0 ? static_cast<std::uint16_t>(crc_override)
                        : deskmate::crc16CcittFalse(payload.data(), payload.size());
  const std::uint16_t len = static_cast<std::uint16_t>(payload.size());
  std::vector<std::uint8_t> out = {deskmate::kMagic0, deskmate::kMagic1, type, 0};
  auto push16 = [&out](std::uint16_t v) {
    out.push_back(static_cast<std::uint8_t>(v & 0xFF));
    out.push_back(static_cast<std::uint8_t>(v >> 8));
  };
  push16(w);
  push16(h);
  push16(len);
  push16(crc);
  out.insert(out.end(), payload.begin(), payload.end());
  return out;
}

std::vector<std::uint8_t> pattern(std::size_t n) {
  std::vector<std::uint8_t> out(n);
  for (std::size_t i = 0; i < n; ++i) {
    out[i] = static_cast<std::uint8_t>((i * 7) % 256);
  }
  return out;
}

}  // namespace

int main() {
  // CRC 가 파이썬 binascii.crc_hqx(data, 0xFFFF) 와 같아야 한다. 다르면 모든
  // 프레임이 조용히 버려진다.
  const std::uint8_t check_input[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  check(deskmate::crc16CcittFalse(check_input, sizeof(check_input)) == 0x29B1,
        "CRC-16/CCITT-FALSE 표준 벡터 0x29B1");

  const auto mask = pattern(54 * 42);
  const auto preview = pattern(160 * 120);

  {
    deskmate::FrameDecoder decoder;
    std::vector<deskmate::VisionFrame> out;
    const std::string noise = "# bg captured\n";
    decoder.feed(reinterpret_cast<const std::uint8_t*>(noise.data()),
                 noise.size(), out);
    const auto wire = framed(deskmate::kTypeMask, 54, 42, mask);
    decoder.feed(wire.data(), wire.size(), out);

    check(out.size() == 1 && out[0].type == deskmate::kTypeMask &&
              out[0].width == 54 && out[0].height == 42 &&
              out[0].payload == mask,
          "로그 뒤에 온 마스크 프레임을 뜯는다");
    check(decoder.countLog("bg captured") == 1,
          "프레임이 아닌 바이트는 ESP 로그로 모인다");
  }

  {
    deskmate::FrameDecoder decoder;
    std::vector<deskmate::VisionFrame> out;
    const auto wire = framed(deskmate::kTypeMask, 54, 42, mask, 0x0000);
    decoder.feed(wire.data(), wire.size(), out);
    check(out.empty() && decoder.badCrc() == 1, "CRC 가 틀린 프레임은 버린다");
  }

  {
    // 실제 링크는 조각으로 들어온다. 97 바이트씩 끊어 넣어도 한 장이 나와야 한다.
    deskmate::FrameDecoder decoder;
    std::vector<deskmate::VisionFrame> out;
    const auto wire = framed(deskmate::kTypePreview, 160, 120, preview);
    for (std::size_t i = 0; i < wire.size(); i += 97) {
      const std::size_t n = std::min<std::size_t>(97, wire.size() - i);
      decoder.feed(wire.data() + i, n, out);
    }
    check(out.size() == 1 && out[0].type == deskmate::kTypePreview &&
              out[0].payload == preview,
          "조각나서 들어온 preview 를 이어 붙인다");
  }

  {
    // w*h != len 이면 매직처럼 보였을 뿐이다. 두 바이트만 버리고 이어서 찾는다.
    deskmate::FrameDecoder decoder;
    std::vector<deskmate::VisionFrame> out;
    auto bad = framed(deskmate::kTypeMask, 54, 41, mask);  // 높이가 안 맞는다
    const auto good = framed(deskmate::kTypeMask, 54, 42, mask);
    bad.insert(bad.end(), good.begin(), good.end());
    decoder.feed(bad.data(), bad.size(), out);
    check(out.size() == 1 && out[0].payload == mask,
          "크기가 안 맞는 헤더를 넘기고 다음 프레임을 찾는다");
  }

  {
    // 여러 장이 연달아 와도 순서대로 나와야 한다.
    deskmate::FrameDecoder decoder;
    std::vector<deskmate::VisionFrame> out;
    std::vector<std::uint8_t> wire;
    for (int i = 0; i < 3; ++i) {
      const auto one = framed(deskmate::kTypeDiff54, 54, 42, mask);
      wire.insert(wire.end(), one.begin(), one.end());
    }
    decoder.feed(wire.data(), wire.size(), out);
    check(out.size() == 3, "연속 프레임을 전부 뜯는다");
    check(decoder.dropped() == 0 && decoder.badCrc() == 0,
          "깨끗한 스트림에서는 버리는 바이트가 없다");
  }

  std::printf("\n%s\n", failures == 0 ? "전부 통과" : "실패 있음");
  return failures == 0 ? 0 : 1;
}
