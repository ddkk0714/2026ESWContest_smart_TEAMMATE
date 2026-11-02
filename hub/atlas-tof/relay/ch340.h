// CH340/CH341 USB-UART 를 커널 ch341 드라이버 없이 usbfs 제어 전송으로 설정한다 (2026-10-22).
// ATLAS 커널(6.12.25-v8)에는 ch341 모듈이 없다. 데이터 경로는 generic usb-serial 드라이버
// (/sys/bus/usb-serial/drivers/generic/new_id 에 "1a86 7523")가 만든 /dev/ttyUSBn 을 쓰는데, 그 드라이버는
// 보드율·LCR 을 칩에 안 쓴다 → 칩이 전원 기본 보드율에 머물러 글자가 깨진다. 그래서 여기서 /dev/bus/usb 로 직접 쓴다.
// 요청 번호·레지스터·분주비 계산은 Linux drivers/usb/serial/ch341.c 와 같다.
#pragma once
#include <dirent.h>
#include <fcntl.h>
#include <linux/usbdevice_fs.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace ch340 {

constexpr uint16_t kVendor = 0x1a86;
constexpr uint16_t kProduct = 0x7523;
constexpr uint8_t kReqReadVersion = 0x5F, kReqWriteReg = 0x9A, kReqSerialInit = 0xA1, kReqModemCtrl = 0xA4;
constexpr uint8_t kRegPrescaler = 0x12, kRegDivisor = 0x13, kRegLcr = 0x18, kRegLcr2 = 0x25;
constexpr uint8_t kLcrEnableRx = 0x80, kLcrEnableTx = 0x40, kLcrCs8 = 0x03;
constexpr uint32_t kClkRate = 48000000;

inline bool readSysFile(const std::string& path, std::string& out) {
    FILE* f = std::fopen(path.c_str(), "r");
    if (!f) return false;
    char buf[256];
    out.clear();
    if (std::fgets(buf, sizeof buf, f)) out = buf;
    std::fclose(f);
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r' || out.back() == ' ')) out.pop_back();
    return true;
}

// sysfs 의 USB 장치 디렉터리(/sys/bus/usb/devices/1-1.3 같은) → /dev/bus/usb/BBB/DDD
inline bool devNodeOf(const std::string& sysdir, std::string& node) {
    std::string bus, dev;
    if (!readSysFile(sysdir + "/busnum", bus) || !readSysFile(sysdir + "/devnum", dev)) return false;
    char b[64];
    std::snprintf(b, sizeof b, "/dev/bus/usb/%03d/%03d", std::atoi(bus.c_str()), std::atoi(dev.c_str()));
    node = b;
    return true;
}

inline bool isCh340(const std::string& sysdir) {
    std::string v, p;
    return readSysFile(sysdir + "/idVendor", v) && readSysFile(sysdir + "/idProduct", p) && v == "1a86" && p == "7523";
}

// /sys/bus/usb/devices 에서 CH340 장치 디렉터리를 찾는다(첫 번째). 없으면 false.
inline bool findDevice(std::string& sysdir) {
    DIR* d = ::opendir("/sys/bus/usb/devices");
    if (!d) return false;
    bool found = false;
    while (dirent* e = ::readdir(d)) {
        if (e->d_name[0] == '.' || std::strchr(e->d_name, ':')) continue;  // 인터페이스(1-1.3:1.0)는 건너뛴다
        const std::string p = std::string("/sys/bus/usb/devices/") + e->d_name;
        if (isCh340(p)) { sysdir = p; found = true; break; }
    }
    ::closedir(d);
    return found;
}

// /dev/ttyUSBn → 그 tty 가 붙은 USB 장치 디렉터리. /sys/class/tty/ttyUSBn/device 는 인터페이스(…:1.0)를 가리키고 그 부모가 장치.
inline bool deviceOfTty(const std::string& ttydev, std::string& sysdir) {
    const char* base = std::strrchr(ttydev.c_str(), '/');
    const std::string name = base ? base + 1 : ttydev;
    char resolved[4096];
    const std::string link = "/sys/class/tty/" + name + "/device";
    if (!::realpath(link.c_str(), resolved)) return false;
    // usb-serial 은 .../1-1.3/1-1.3:1.0/ttyUSB0 (포트 디렉터리)을 가리킨다. idVendor 가 있는 디렉터리가 나올 때까지 올라간다.
    std::string dir = resolved;
    for (int up = 0; up < 5; ++up) {
        const size_t cut = dir.rfind('/');
        if (cut == std::string::npos || cut == 0) return false;
        dir = dir.substr(0, cut);
        std::string v;
        if (readSysFile(dir + "/idVendor", v)) { sysdir = dir; return isCh340(sysdir); }
    }
    return false;
}

// 인터페이스 0 에 드라이버가 안 붙어 있으면 generic usb-serial 드라이버에 ID 를 등록한다(커널이 바로 ttyUSBn 을 만든다).
// 재부팅 전까지 남으므로 한 번이면 되고, 이미 붙어 있으면 아무것도 안 한다. 돌아오는 값: 0 = 이미 있음, 1 = 등록함, -1 = 실패.
inline int bindGeneric(const std::string& sysdir) {
    const std::string iface = sysdir + ":1.0";
    char resolved[4096];
    if (::realpath((iface + "/driver").c_str(), resolved)) return 0;
    FILE* f = std::fopen("/sys/bus/usb-serial/drivers/generic/new_id", "w");
    if (!f) return -1;
    const bool ok = std::fputs("1a86 7523\n", f) >= 0;
    std::fclose(f);
    return ok ? 1 : -1;
}

// WRITE_REG 는 wValue = 레지스터 번호 쌍(상위·하위), wIndex = 그 두 레지스터에 쓸 값(ch341.c 의 인자 순서 그대로).
inline int ctrlOut(int fd, uint8_t req, uint16_t value, uint16_t index) {
    usbdevfs_ctrltransfer t{};
    t.bRequestType = 0x40;  // vendor, host→device, recipient device (인터페이스 claim 불필요)
    t.bRequest = req;
    t.wValue = value;
    t.wIndex = index;
    t.wLength = 0;
    t.timeout = 1000;
    t.data = nullptr;
    return ::ioctl(fd, USBDEVFS_CONTROL, &t);
}

inline int ctrlIn(int fd, uint8_t req, uint16_t value, uint16_t index, uint8_t* buf, uint16_t len) {
    usbdevfs_ctrltransfer t{};
    t.bRequestType = 0xC0;
    t.bRequest = req;
    t.wValue = value;
    t.wIndex = index;
    t.wLength = len;
    t.timeout = 1000;
    t.data = buf;
    return ::ioctl(fd, USBDEVFS_CONTROL, &t);
}

// ch341_get_divisor 그대로: (0x100 - div) << 8 | fact << 2 | ps. 실패하면 false.
inline bool divisorFor(uint32_t speed, uint16_t& out) {
    if (speed < 46) speed = 46;
    if (speed > 3000000) speed = 3000000;
    auto clkDiv = [](int ps, int fact) { return 1u << (12 - 3 * ps - fact); };
    int ps = 3;
    for (; ps >= 0; --ps) {
        const uint32_t min_rate = kClkRate / (clkDiv(ps, 1) * 512);
        if (speed > min_rate) break;
    }
    if (ps < 0) return false;
    uint32_t clk_div = clkDiv(ps, 1);
    uint32_t div = kClkRate / (clk_div * speed);
    int fact;
    if (div < 9 || div > 255) { div /= 2; clk_div *= 2; fact = 0; } else { fact = 1; }
    if (div < 2) return false;
    if (16 * kClkRate / (clk_div * div) - 16 * speed >= 16 * speed - 16 * kClkRate / (clk_div * (div + 1))) ++div;
    if (fact == 1 && div % 2 == 0) { div /= 2; fact = 0; }
    out = static_cast<uint16_t>(((0x100 - div) << 8) | (fact << 2) | ps);
    return true;
}

// 칩 초기화 + 보드율 + 8N1 + DTR/RTS 둘 다 내림(ESP32 자동 리셋 회로는 둘이 같으면 아무 일도 없다).
// 돌아오는 값: 칩 버전(≥0) 또는 음수(실패 단계: -1 open, -2 version, -3 init, -4 baud, -5 lcr, -6 modem).
inline int configure(const std::string& devnode, uint32_t baud, char* why, size_t why_len) {
    const int fd = ::open(devnode.c_str(), O_RDWR);
    if (fd < 0) { std::snprintf(why, why_len, "open %s: %s", devnode.c_str(), std::strerror(errno)); return -1; }
    uint8_t ver[2] = {0, 0};
    if (ctrlIn(fd, kReqReadVersion, 0, 0, ver, 2) < 0) { std::snprintf(why, why_len, "read version: %s", std::strerror(errno)); ::close(fd); return -2; }
    if (ctrlOut(fd, kReqSerialInit, 0, 0) < 0) { std::snprintf(why, why_len, "serial init: %s", std::strerror(errno)); ::close(fd); return -3; }
    uint16_t val = 0;
    if (!divisorFor(baud, val)) { std::snprintf(why, why_len, "baud %u not representable", static_cast<unsigned>(baud)); ::close(fd); return -4; }
    if (ver[0] > 0x27) val |= 0x80;  // 32 B 모일 때까지 안 기다리고 바로 올린다(ch341.c 주석)
    if (ctrlOut(fd, kReqWriteReg, static_cast<uint16_t>((kRegDivisor << 8) | kRegPrescaler), val) < 0) { std::snprintf(why, why_len, "write baud: %s", std::strerror(errno)); ::close(fd); return -4; }
    if (ver[0] >= 0x30) {
        const uint16_t lcr = kLcrEnableRx | kLcrEnableTx | kLcrCs8;
        if (ctrlOut(fd, kReqWriteReg, static_cast<uint16_t>((kRegLcr2 << 8) | kRegLcr), lcr) < 0) { std::snprintf(why, why_len, "write lcr: %s", std::strerror(errno)); ::close(fd); return -5; }
    }
    if (ctrlOut(fd, kReqModemCtrl, 0xFFFF, 0) < 0) { std::snprintf(why, why_len, "modem ctrl: %s", std::strerror(errno)); ::close(fd); return -6; }
    ::close(fd);
    std::snprintf(why, why_len, "version=0x%02x baud=%u reg1312=0x%04x", ver[0], static_cast<unsigned>(baud), val);
    return ver[0];
}

// tty 경로가 CH340 이면 설정하고 true. CH340 이 아니면(진짜 드라이버가 있는 다른 칩, UART) 건드리지 않고 false.
inline bool configureTty(const std::string& ttydev, uint32_t baud, char* why, size_t why_len) {
    std::string sysdir, node;
    if (!deviceOfTty(ttydev, sysdir)) { std::snprintf(why, why_len, "not a CH340 tty"); return false; }
    if (!devNodeOf(sysdir, node)) { std::snprintf(why, why_len, "no busnum/devnum under %s", sysdir.c_str()); return false; }
    return configure(node, baud, why, why_len) >= 0;
}

}  // namespace ch340
