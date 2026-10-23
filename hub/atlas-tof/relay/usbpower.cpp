// Pi 4 USB 허브 포트 전원을 usbfs 로 껐다 켠다 — ESP32·ToF 센서를 완전히 재시작시키는 용도 (2026-10-22).
//   usbpower [hub=/dev/bus/usb/001/002] [port=3] [off_ms=1500]
// 허브 class 요청: CLEAR_FEATURE/SET_FEATURE(PORT_POWER=8), bmRequestType 0x23(class, other=port), wIndex=port.
// Pi 4B 의 온보드 VL805 허브는 포트 전원이 묶여 있어 모든 USB 포트가 같이 꺼진다(uhubctl 과 같은 동작).
#include <fcntl.h>
#include <linux/usbdevice_fs.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static int portFeature(int fd, int req, int port) {
    usbdevfs_ctrltransfer t{};
    t.bRequestType = 0x23; t.bRequest = req; t.wValue = 8; t.wIndex = port; t.wLength = 0; t.timeout = 1000; t.data = nullptr;
    return ioctl(fd, USBDEVFS_CONTROL, &t);
}

int main(int argc, char** argv) {
    const char* hub = argc > 1 ? argv[1] : "/dev/bus/usb/001/002";
    const int port = argc > 2 ? atoi(argv[2]) : 3;
    const int off_ms = argc > 3 ? atoi(argv[3]) : 1500;
    const int fd = open(hub, O_RDWR);
    if (fd < 0) { printf("usbpower: open %s failed: %s", hub, strerror(errno)); puts(""); return 1; }
    if (portFeature(fd, 1, port) < 0) { printf("usbpower: CLEAR_FEATURE PORT_POWER port %d failed: %s", port, strerror(errno)); puts(""); close(fd); return 2; }
    printf("usbpower: port %d power OFF for %d ms", port, off_ms); puts("");
    usleep(off_ms * 1000);
    if (portFeature(fd, 3, port) < 0) { printf("usbpower: SET_FEATURE PORT_POWER port %d failed: %s", port, strerror(errno)); puts(""); close(fd); return 3; }
    puts("usbpower: power ON");
    close(fd);
    return 0;
}
