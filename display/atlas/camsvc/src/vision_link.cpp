#include "vision_link.h"

#include <dirent.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

#include <cstring>
#include <fstream>

namespace deskmate {
namespace {

bool isImageType(std::uint8_t type) {
  return type == kTypeDiff54 || type == kTypeMask || type == kTypeSkeleton ||
         type == kTypePreview || type == kTypeRaw54;
}

std::string trim(const std::string& text) {
  const auto begin = text.find_first_not_of(" \t\r\n");
  if (begin == std::string::npos) return {};
  const auto end = text.find_last_not_of(" \t\r\n");
  return text.substr(begin, end - begin + 1);
}

speed_t baudConstant(int baud) {
  switch (baud) {
    case 115200: return B115200;
    case 230400: return B230400;
    case 460800: return B460800;
    case 921600: return B921600;
    case 1000000: return B1000000;
    default: return B921600;
  }
}

}  // namespace

std::uint16_t crc16CcittFalse(const std::uint8_t* data, std::size_t size) {
  std::uint16_t crc = 0xFFFF;
  for (std::size_t i = 0; i < size; ++i) {
    crc ^= static_cast<std::uint16_t>(data[i]) << 8;
    for (int bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000) ? static_cast<std::uint16_t>((crc << 1) ^ 0x1021)
                           : static_cast<std::uint16_t>(crc << 1);
    }
  }
  return crc;
}

std::size_t FrameDecoder::countLog(const std::string& needle) const {
  std::size_t n = 0;
  for (const auto& line : log_) {
    if (line.find(needle) != std::string::npos) ++n;
  }
  return n;
}

void FrameDecoder::takeText(std::size_t upto) {
  if (upto == 0) return;
  std::string text;
  text.reserve(upto);
  for (std::size_t i = 0; i < upto; ++i) {
    const std::uint8_t b = buf_[i];
    // 프레임이 어긋나면 바이너리가 이 경로로 샌다. 인쇄 가능한 것만 남긴다.
    text.push_back((b == '\n' || b == '\t' || (b >= 32 && b < 127))
                       ? static_cast<char>(b)
                       : '.');
  }
  std::size_t start = 0;
  while (start <= text.size()) {
    const auto stop = text.find('\n', start);
    const std::string line =
        trim(text.substr(start, stop == std::string::npos ? std::string::npos
                                                          : stop - start));
    if (!line.empty()) {
      log_.push_back(line);
      while (log_.size() > kLogLimit) log_.pop_front();
    }
    if (stop == std::string::npos) break;
    start = stop + 1;
  }
}

std::ptrdiff_t FrameDecoder::findMagic() const {
  for (std::size_t i = 0; i + 1 < buf_.size(); ++i) {
    if (buf_[i] == kMagic0 && buf_[i + 1] == kMagic1) {
      return static_cast<std::ptrdiff_t>(i);
    }
  }
  return -1;
}

void FrameDecoder::feed(const std::uint8_t* data, std::size_t size,
                        std::vector<VisionFrame>& out) {
  buf_.insert(buf_.end(), data, data + size);

  for (;;) {
    const std::ptrdiff_t start = findMagic();
    if (start < 0) {
      // 매직을 못 찾았으면 전부 텍스트다. 헤더 앞부분일 수 있는 마지막 한
      // 바이트만 남긴다.
      const std::size_t keep =
          (!buf_.empty() && buf_.back() == kMagic0) ? 1 : 0;
      const std::size_t drop = buf_.size() - keep;
      takeText(drop);
      dropped_ += drop;
      buf_.erase(buf_.begin(), buf_.begin() + static_cast<long>(drop));
      return;
    }
    if (start > 0) {
      takeText(static_cast<std::size_t>(start));
      dropped_ += static_cast<std::size_t>(start);
      buf_.erase(buf_.begin(), buf_.begin() + start);
    }
    if (buf_.size() < kHeaderLength) return;

    const std::uint8_t type = buf_[2];
    const std::uint16_t width =
        static_cast<std::uint16_t>(buf_[4] | (buf_[5] << 8));
    const std::uint16_t height =
        static_cast<std::uint16_t>(buf_[6] | (buf_[7] << 8));
    const std::uint16_t length =
        static_cast<std::uint16_t>(buf_[8] | (buf_[9] << 8));
    const std::uint16_t crc =
        static_cast<std::uint16_t>(buf_[10] | (buf_[11] << 8));

    const bool known = isImageType(type) || type == kTypeGraph;
    const bool sane = known && length != 0 && length <= kMaxPayload &&
                      (!isImageType(type) ||
                       static_cast<std::size_t>(width) * height == length);
    if (!sane) {
      buf_.erase(buf_.begin(), buf_.begin() + 2);  // 매직처럼 보였을 뿐이다
      dropped_ += 2;
      continue;
    }
    if (buf_.size() < kHeaderLength + length) return;

    const std::uint8_t* payload = buf_.data() + kHeaderLength;
    if (crc16CcittFalse(payload, length) != crc) {
      buf_.erase(buf_.begin(), buf_.begin() + 2);
      ++bad_crc_;
      continue;
    }
    if (isImageType(type)) {
      VisionFrame frame;
      frame.type = type;
      frame.width = width;
      frame.height = height;
      frame.payload.assign(payload, payload + length);
      out.push_back(std::move(frame));
    }
    buf_.erase(buf_.begin(),
               buf_.begin() + static_cast<long>(kHeaderLength + length));
  }
}

// ------------------------------------------------------------------ 장치

VisionLink::~VisionLink() { close(); }

std::string VisionLink::findVisionPort() {
  DIR* dir = opendir("/sys/class/tty");
  if (dir == nullptr) return {};
  std::string found;
  while (const dirent* entry = readdir(dir)) {
    const std::string name = entry->d_name;
    if (name.rfind("ttyACM", 0) != 0) continue;
    std::ifstream label("/sys/class/tty/" + name + "/device/interface");
    if (!label) continue;
    std::string text;
    std::getline(label, text);
    text = trim(text);
    for (char& c : text) c = static_cast<char>(::tolower(c));
    if (text == "vision stream") {
      found = "/dev/" + name;
      break;
    }
  }
  closedir(dir);
  return found;
}

bool VisionLink::open(const std::string& path, int baud) {
  close();
  fd_ = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
  if (fd_ < 0) {
    error_ = std::string("열 수 없습니다: ") + std::strerror(errno);
    return false;
  }

  termios tty{};
  if (tcgetattr(fd_, &tty) != 0) {
    error_ = std::string("tcgetattr: ") + std::strerror(errno);
    close();
    return false;
  }
  cfmakeraw(&tty);
  cfsetispeed(&tty, baudConstant(baud));
  cfsetospeed(&tty, baudConstant(baud));
  tty.c_cflag |= (CLOCAL | CREAD);
  tty.c_cflag &= ~CRTSCTS;
  // 읽을 게 없으면 바로 돌아온다. 여기서 막히면 판정 루프가 같이 멈춘다.
  tty.c_cc[VMIN] = 0;
  tty.c_cc[VTIME] = 0;
  if (tcsetattr(fd_, TCSANOW, &tty) != 0) {
    error_ = std::string("tcsetattr: ") + std::strerror(errno);
    close();
    return false;
  }
  tcflush(fd_, TCIFLUSH);
  path_ = path;
  error_.clear();
  return true;
}

std::ptrdiff_t VisionLink::read(std::uint8_t* out, std::size_t size) {
  if (fd_ < 0) return 0;
  const ssize_t n = ::read(fd_, out, size);
  if (n < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
    error_ = std::string("read: ") + std::strerror(errno);
    return -1;
  }
  return n;
}

bool VisionLink::writeLine(const std::string& line) {
  if (fd_ < 0) return false;
  const std::string payload = line + "\n";
  const ssize_t n = ::write(fd_, payload.data(), payload.size());
  return n == static_cast<ssize_t>(payload.size());
}

void VisionLink::close() {
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
}

}  // namespace deskmate
