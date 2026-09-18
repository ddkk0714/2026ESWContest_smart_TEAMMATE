#!/bin/sh
# Pi 5(ATLAS) 보드에서 한 번 돌린다. 앱이 ESP32-CAM 의 UART 를 읽을 수 있게 만든다.
#
#     scp board-uart-setup.sh root@<보드IP>:/tmp/ && ssh root@<보드IP> sh /tmp/board-uart-setup.sh
#
# 왜 필요한가 - 두 가지가 보드 기본값으로는 막혀 있다.
#
#   1) 권한. GPIO14/15 의 노드는 /dev/ttyAMA0 이고 기본이 `root:tty 0620` 이라
#      앱은 물론 peripheral-manager 도 못 읽는다. 앱이 가진 그룹(perm_peripheral)
#      으로 바꿔 준다. appinfo.json 에 peripheral 권한을 선언한 앱만 그 그룹에 든다.
#
#   2) 속도. Dart 에는 termios 가 없고, ATLAS 의 UART `Open(u)` 은 속성만 바꾸고
#      실제 회선 속도를 안 건드린다(실기 확인: Open(921600) 뒤에도 stty 는 115200).
#      그래서 여기서 stty 로 걸어 둔다. ESP 펌웨어의 LINK_BAUD 와 같은 값이다.
#
# 그리고 그 핀은 기본적으로 **커널 콘솔**이다. 로그인 프롬프트가 물고 있으면
# 센서 바이트를 나눠 먹으므로 getty 를 멈춘다. 콘솔은 ttyAMA10(디버그 커넥터)에
# 그대로 남으므로 보드를 못 만지게 되는 일은 없다.
#
# /dev 와 stty 설정은 재부팅하면 사라진다. 부팅 때마다 다시 돌려야 한다.
set -u

BAUD=921600
GROUP=perm_peripheral

# 어느 장치를 쓸지 고른다. USB 로 꽂힌 RP2040 브리지가 있으면 그쪽이다 -
# 판정이 먹는 이진 마스크는 그 브리지가 만든다(vision.c). ESP 를 GPIO 로 직결하면
# coverage 만 오고 마스크는 영영 안 온다.
DEV=""
for t in /sys/class/tty/ttyACM*; do
    [ -r "$t/device/interface" ] || continue
    if grep -qi "vision stream" "$t/device/interface" 2>/dev/null; then
        DEV="/dev/$(basename "$t")"
        echo "Vision Stream 포트: $DEV"
        break
    fi
done
[ -n "$DEV" ] || { DEV=/dev/ttyAMA0; echo "USB 브리지가 없어 GPIO UART 를 씁니다: $DEV"; }

echo "== getty 멈추기 (콘솔은 ttyAMA10 에 남는다)"
systemctl stop serial-getty@ttyAMA0 2>/dev/null || true

echo "== 권한: $GROUP 이 읽을 수 있게"
chgrp "$GROUP" "$DEV" || exit 1
chmod 660 "$DEV" || exit 1

echo "== 속도: $BAUD raw"
# min 0 time 0 - 읽을 게 없으면 즉시 0 바이트로 돌아온다. 앱이 20ms 마다 긁으므로
# 여기서 막히면 화면이 같이 멈춘다.
stty -F "$DEV" "$BAUD" raw -echo min 0 time 0 || exit 1

echo
ls -l "$DEV"
stty -F "$DEV" | head -n 1
echo
echo "완료. 앱을 다시 시작하세요:"
echo "  abusctl call com.atlas.AppManager1 Stop  \"com.atlas.app.deskmate_display_ui_test\""
echo "  abusctl call com.atlas.AppManager1 Start \"com.atlas.app.deskmate_display_ui_test\""
