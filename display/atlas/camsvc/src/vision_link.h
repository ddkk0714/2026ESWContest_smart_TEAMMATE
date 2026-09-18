// ESP32-CAM 프레임 수신. 장치를 열고, 속도를 맞추고, 프레임을 뜯는다.
//
// 원본은 `76EHwan/pico_esp32-cam_ftdi` 의 `tools/esp_source.py` 다. 와이어 포맷과
// 상수는 그쪽과 **정확히** 같아야 한다 - 다르면 프레임이 통째로 버려지거나 조용히
// 어긋난다.
//
// 프레임 한 장:
//   A5 5A | type(u8) seq(u8) w(u16) h(u16) len(u16) crc(u16) | payload[len]
//   모든 수는 리틀엔디안. crc 는 payload 에 대한 CRC-16/CCITT-FALSE.
//
// **C++ 은 termios 를 직접 쓴다.** Flutter 앱 쪽은 Dart 에 termios 가 없어서 보드에서
// `stty` 를 미리 걸어야 했는데(그리고 ATLAS 의 UART D-Bus `Open(u)` 은 속성만 바꾸고
// 실제 회선 속도를 안 건드린다), 여기서는 그 제약이 없다.
#ifndef DESKMATE_VISION_LINK_H
#define DESKMATE_VISION_LINK_H

#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

namespace deskmate {

inline constexpr std::uint8_t kMagic0 = 0xA5;
inline constexpr std::uint8_t kMagic1 = 0x5A;
inline constexpr std::size_t kHeaderLength = 12;
inline constexpr std::size_t kMaxPayload = 64 * 1024;

// `esp32cam_sender.ino` 의 enum 과 같은 번호다.
inline constexpr std::uint8_t kTypeDiff54 = 1;   // 54x42 coverage
inline constexpr std::uint8_t kTypeMask = 2;     // RP2040 이 만드는 이진 마스크
inline constexpr std::uint8_t kTypeSkeleton = 3;
inline constexpr std::uint8_t kTypePreview = 4;  // 160x120 회색 - 포즈 모델이 먹는 것
inline constexpr std::uint8_t kTypeRaw54 = 6;
inline constexpr std::uint8_t kTypeGraph = 7;

// ESP 펌웨어의 LINK_BAUD.
inline constexpr int kVisionBaud = 921600;

// 프레임이 아닌 바이트는 ESP 로그 텍스트다. 이만큼만 들고 있는다.
inline constexpr std::size_t kLogLimit = 8;

std::uint16_t crc16CcittFalse(const std::uint8_t* data, std::size_t size);

struct VisionFrame {
  std::uint8_t type = 0;
  std::uint16_t width = 0;
  std::uint16_t height = 0;
  std::vector<std::uint8_t> payload;
};

// 바이트를 계속 넣으면 온전한 프레임만 돌려준다.
class FrameDecoder {
 public:
  void feed(const std::uint8_t* data, std::size_t size,
            std::vector<VisionFrame>& out);

  std::size_t dropped() const { return dropped_; }
  std::size_t badCrc() const { return bad_crc_; }

  // ESP 가 같은 포트로 흘리는 로그 줄. 배경 재캡처가 끝났는지(`# bg captured`)
  // 같은 것을 여기서 본다.
  const std::deque<std::string>& log() const { return log_; }
  std::size_t countLog(const std::string& needle) const;

 private:
  void takeText(std::size_t upto);
  std::ptrdiff_t findMagic() const;

  std::vector<std::uint8_t> buf_;
  std::deque<std::string> log_;
  std::size_t dropped_ = 0;
  std::size_t bad_crc_ = 0;
};

// 장치 열기와 읽기. 테스트에서는 쓰지 않는다(디코더만 검사한다).
class VisionLink {
 public:
  ~VisionLink();

  // `Vision Stream` 이라고 적힌 포트를 이름으로 고른다. 보드는 CDC 두 개짜리
  // 복합 장치라 포트가 둘 뜨는데, 인터페이스 0(브리지)을 열면 포트는 멀쩡히
  // 열리고 **프레임만 영영 안 온다.**
  static std::string findVisionPort();

  bool open(const std::string& path, int baud = kVisionBaud);
  bool isOpen() const { return fd_ >= 0; }

  // 읽을 게 없으면 0 을 돌려준다(논블로킹).
  std::ptrdiff_t read(std::uint8_t* out, std::size_t size);

  // ESP 에 명령 한 줄. `p1` 은 preview 를 매 프레임 보내라는 뜻이다.
  bool writeLine(const std::string& line);

  void close();

  const std::string& path() const { return path_; }
  const std::string& error() const { return error_; }

 private:
  int fd_ = -1;
  std::string path_;
  std::string error_;
};

}  // namespace deskmate

#endif  // DESKMATE_VISION_LINK_H
