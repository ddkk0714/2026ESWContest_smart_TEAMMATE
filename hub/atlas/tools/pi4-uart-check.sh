#!/bin/sh
# Pi 4(ATLAS) 에서 ESP32 UART 수신이 rx=0 일 때 원인을 갈라내는 점검 스크립트.
#
#     scp hub/atlas/tools/pi4-uart-check.sh atlas:/tmp/ && ssh atlas sh /tmp/pi4-uart-check.sh
#     ssh atlas sh /tmp/pi4-uart-check.sh --fix      # getty 정지·권한·보드율까지 손본다
#
# rx=0 은 원인이 세 가지로 갈리는데 로그만으로는 구분이 안 된다. 이 스크립트가
# 갈라 주는 것이 그 세 가지다.
#
#   1) 노드가 바뀌었다 - /dev/serial0 은 심볼릭 링크다. Pi 4 기본은 ttyS0(mini
#      UART)이고 ttyAMA0 은 블루투스 쪽이다. 부팅 설정(dtoverlay=disable-bt 등)이
#      들어가면 serial0 이 ttyAMA0 을 가리키게 되고, 그때까지 ttyS0 앞으로 해 둔
#      권한·getty 정지가 전부 헛것이 된다. 2026-09-16 실측 기록은 ttyS0 이다.
#   2) 남이 물고 있다 - 커널 콘솔(console=...)과 serial-getty 가 같은 핀을 쓰면
#      ESP32 바이트를 나눠 먹는다. 재부팅하면 되살아나므로 매번 확인해야 한다.
#   3) 권한이 없다 - 서비스 계정이 노드를 못 연다. 이 경우 Hub 로그에
#      "DESKMATE UART open failed: ... (Permission denied)" 가 찍힌다.
#
# 마지막의 원시 캡처는 "선에 바이트가 오기는 하는가"를 본다. 0 B 면 배선·ESP32
# 송신 문제, 바이트는 오는데 Hub rx 가 0 이면 보드율·프레이밍 문제다.
#
# BusyBox 유저랜드를 가정한다(GNU 전용 옵션 금지).
set -u

BAUD="${DESKMATE_UART_BAUD:-115200}"
DEV="${DESKMATE_UART_DEV:-/dev/serial0}"
CAPTURE_SECONDS="${DESKMATE_UART_CAPTURE_SECONDS:-8}"
FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

NODE=$(readlink -f "$DEV" 2>/dev/null)
[ -n "$NODE" ] || NODE="$DEV"
NAME=$(basename "$NODE")

echo "== 1. 장치 노드"
echo "   $DEV -> $NODE"
ls -l "$DEV" "$NODE" 2>&1
case "$NAME" in
ttyS0)
    echo "   OK: 2026-09-16 실측과 같은 노드다(mini UART, GPIO14/15)."
    ;;
ttyAMA0)
    echo "   주의: 09-16 기록은 ttyS0 이었다. 부팅 설정이 바뀌었다는 뜻이고,"
    echo "         ttyS0 앞으로 해 둔 권한·getty 정지는 이 노드에 적용되지 않는다."
    ;;
*)
    echo "   주의: 예상 밖 노드다($NAME). 배선 핀과 맞는지 먼저 확인한다."
    ;;
esac

echo
echo "== 2. 회선 설정 (기대: $BAUD, raw)"
stty -F "$NODE" 2>&1

echo
echo "== 3. 커널 콘솔"
tr ' ' '\n' < /proc/cmdline | grep '^console=' || echo "   (console= 없음)"

echo
echo "== 4. getty"
if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active "serial-getty@$NAME" 2>&1
else
    echo "   systemctl 없음 - 프로세스로 확인한다"
fi
ps 2>/dev/null | grep -i getty | grep -v grep || echo "   getty 프로세스 없음"

echo
echo "== 5. 이 노드를 열고 있는 프로세스"
HOLDERS=0
for proc in /proc/[0-9]*; do
    pid=$(basename "$proc")
    for fd in "$proc"/fd/*; do
        [ -e "$fd" ] || continue
        target=$(readlink "$fd" 2>/dev/null)
        [ "$target" = "$NODE" ] || continue
        cmd=$(cat "$proc/cmdline" 2>/dev/null | tr '\0' ' ')
        echo "   pid=$pid  $cmd"
        HOLDERS=$((HOLDERS + 1))
        break
    done
done
[ "$HOLDERS" -eq 0 ] && echo "   (없음)"

if [ "$FIX" -eq 1 ]; then
    echo
    echo "== 6. --fix: getty 정지 · 권한 · 보드율"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop "serial-getty@$NAME" 2>/dev/null || true
        echo "   serial-getty@$NAME 정지 시도"
    fi
    # Hub 서비스 계정. 보드의 /etc/group 이 읽기 전용이라 그룹 추가는 실패하므로
    # 노드 쪽 그룹을 바꾼다. 재부팅하면 원복된다.
    GROUP="${DESKMATE_UART_GROUP:-g0_a5010}"
    chgrp "$GROUP" "$NODE" 2>&1 && chmod 660 "$NODE" 2>&1 && echo "   권한: $GROUP 660"
    stty -F "$NODE" "$BAUD" raw -echo min 1 time 0 2>&1 && echo "   회선: $BAUD raw"
    ls -l "$NODE"
fi

echo
echo "== 7. 원시 캡처 ${CAPTURE_SECONDS}s (Hub 가 떠 있으면 바이트를 나눠 먹는다)"
# 캡처 전에 회선을 raw·blocking 으로 맞춘다. 기본 canonical 모드로 읽으면
# ERASE·EOF 같은 제어문자가 해석되어 이진 프레임이 조각나고(실측: 26 B 프레임이
# 9~14 B 로 깨짐), min 0 이면 dd 가 즉시 EOF 로 끝나 0 B 가 나온다.
# Hub 는 열 때 스스로 cfmakeraw 를 걸므로 이 설정에 영향받지 않는다.
stty -F "$NODE" "$BAUD" raw -echo min 1 time 0 2>/dev/null
OUT=/tmp/deskmate-uart-capture.bin
rm -f "$OUT"
if command -v timeout >/dev/null 2>&1; then
    timeout "$CAPTURE_SECONDS" dd if="$NODE" of="$OUT" bs=1 2>/dev/null
else
    dd if="$NODE" of="$OUT" bs=1 count=4096 2>/dev/null &
    DD_PID=$!
    sleep "$CAPTURE_SECONDS"
    kill "$DD_PID" 2>/dev/null
    wait "$DD_PID" 2>/dev/null
fi
SIZE=$(wc -c < "$OUT" 2>/dev/null || echo 0)
echo "   $SIZE B -> $OUT"
if [ "$SIZE" -gt 0 ]; then
    od -A d -t x1 "$OUT" 2>/dev/null | head -n 8
    echo
    echo "   판독: 0x00 경계, TYPE 0x20=mmWave / 0x10=env / 0xF0=heartbeat,"
    echo "         프레임 선두는 0xA5 0x01 이다. 이게 안 보이고 바이트만 많으면"
    echo "         보드율 불일치다(ESP32 는 115200 으로 쏜다)."
else
    echo "   판독: 선에 아무것도 오지 않는다. ESP32 GPIO25 -> Pi 4 물리 10번(GPIO15),"
    echo "         공통 GND, ESP32 전원·USB JSON 출력을 먼저 확인한다."
fi
