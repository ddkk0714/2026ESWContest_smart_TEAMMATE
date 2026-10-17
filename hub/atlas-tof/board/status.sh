#!/bin/sh
# 무엇이 붙어 있고 무엇이 도는지 한 번에 본다. 문제가 생기면 이 출력부터 확인.
D=$(cd "$(dirname "$0")" && pwd)
echo "== 보드"; tr -d '\0' < /proc/device-tree/model 2>/dev/null; echo; grep PRETTY /etc/os-release 2>/dev/null; uname -r
echo "== ESP32 USB 노드 (없으면 USB 시리얼 드라이버가 없거나 케이블이 충전 전용 — README 'USB 가 안 보일 때')"
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo "   없음"

# 꽂힌 USB 장치와, 이 커널이 가진 USB 시리얼 드라이버.
# 2026-10-15 실기: ESP32 DevKit 의 CH340(1a86:7523)은 꽂혀도 드라이버(ch341)가 없어 /dev/ttyUSB* 가 안 생겼다.
# 아래에 cdc_acm 이 보이면 USB CDC 로 보이는 장치(ESP32-S3/C3 의 내장 USB, RP2040 USB-UART 브리지)는 /dev/ttyACM* 로 잡힌다.
echo "== 꽂힌 USB 장치 (vendor:product 이름)"
for d in /sys/bus/usb/devices/*; do
    [ -f "$d/idVendor" ] || continue
    v=$(cat "$d/idVendor"); p=$(cat "$d/idProduct"); n=$(cat "$d/product" 2>/dev/null)
    case "$v:$p" in 1d6b:*) continue;; esac          # 루트 허브는 뺀다
    note=""
    case "$v:$p" in
        1a86:7523|1a86:55d4) note="(CH340/CH9102 — ch341 드라이버 필요)";;
        10c4:ea60) note="(CP210x — cp210x 드라이버 필요)";;
        0403:*) note="(FTDI — ftdi_sio 드라이버 필요)";;
        303a:*) note="(Espressif 내장 USB — cdc_acm 이면 ttyACM)";;
        2e8a:*) note="(Raspberry Pi RP2040 — cdc_acm 이면 ttyACM)";;
    esac
    echo "   $v:$p $n $note"
done
echo "== 이 커널의 USB 시리얼 드라이버"
ls /sys/bus/usb-serial/drivers 2>/dev/null | sed 's/^/   usb-serial: /'
ls /sys/bus/usb/drivers 2>/dev/null | grep -i -E "acm|serial" | sed 's/^/   usb: /'
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz 2>/dev/null | grep -E "^CONFIG_USB_(ACM|SERIAL|SERIAL_CH341|SERIAL_CP210X|SERIAL_FTDI_SIO)=" | sed 's/^/   /'
fi
M=/lib/modules/$(uname -r)
[ -d "$M" ] && find "$M" \( -name 'ch341*' -o -name 'cp210x*' -o -name 'ftdi_sio*' -o -name 'cdc-acm*' -o -name 'cdc_acm*' \) 2>/dev/null | sed 's/^/   module file: /'

echo "== 커널 메시지 (USB 시리얼)"
dmesg 2>/dev/null | grep -i -E "ch34|ch341|cp210|ftdi|cdc_acm|ttyUSB|ttyACM|usb .*new" | tail -n 8
echo "== 프로세스"; P=$(pidof deskmate_tofposture); [ -n "$P" ] && echo "   deskmate_tofposture pid=$P" || echo "   안 떠 있음 (sh $D/start.sh)"
PORT=$(grep '^TOFPOSTURE_HTTP=' "$D/tofposture.env" 2>/dev/null | cut -d= -f2); [ -n "$PORT" ] || PORT=8771
echo "== /health"; wget -q -O - "http://127.0.0.1:$PORT/health" 2>/dev/null || echo "   응답 없음"; echo
echo "== 최근 로그"; tail -n 12 "$D/tofposture.log" 2>/dev/null
