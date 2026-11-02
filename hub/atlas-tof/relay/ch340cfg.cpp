// CH340 을 usbfs 로 설정하는 단독 도구 (relay 에도 같은 ch340.h 가 들어 있다. 진단·수동 복구용).
//   ch340cfg [tty=/dev/ttyUSB0 | auto] [baud=115200]
//   auto: /sys/bus/usb/devices 에서 1a86:7523 을 찾아 generic 드라이버에 붙이고(없으면) 설정한다.
#include "ch340.h"

int main(int argc, char** argv) {
    const std::string tty = argc > 1 ? argv[1] : "auto";
    const uint32_t baud = argc > 2 ? static_cast<uint32_t>(std::atoi(argv[2])) : 115200;
    char why[256];
    std::string sysdir, node;
    if (tty == "auto") {
        if (!ch340::findDevice(sysdir)) { std::fprintf(stderr, "ch340cfg: no 1a86:7523 on USB\n"); return 2; }
        const int b = ch340::bindGeneric(sysdir);
        std::printf("ch340cfg: device %s generic-bind=%s\n", sysdir.c_str(), b == 0 ? "already" : b == 1 ? "registered" : "FAILED");
        if (!ch340::devNodeOf(sysdir, node)) { std::fprintf(stderr, "ch340cfg: no busnum/devnum\n"); return 2; }
    } else {
        if (!ch340::deviceOfTty(tty, sysdir)) { std::fprintf(stderr, "ch340cfg: %s is not a CH340 tty\n", tty.c_str()); return 2; }
        if (!ch340::devNodeOf(sysdir, node)) { std::fprintf(stderr, "ch340cfg: no busnum/devnum\n"); return 2; }
    }
    const int r = ch340::configure(node, baud, why, sizeof why);
    std::printf("ch340cfg: %s %s\n", node.c_str(), why);
    return r >= 0 ? 0 : 1;
}
