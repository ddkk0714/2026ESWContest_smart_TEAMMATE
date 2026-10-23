#!/bin/sh
# Pi 4(ATLAS) 에서 ToF UART 중계기(uart_relay_board)를 띄우거나 내린다. BusyBox sh.
#   sh /mnt/rawdata/deskmate_tof/relay.sh          # (재)시작: auto(USB ttyUSB0/ttyACM0 → 없으면 ttyAMA0) → 127.0.0.1:9100
#   RELAY_DEV=/dev/ttyAMA0 sh relay.sh             # GPIO UART 로 강제
# 10-22 USB 직결: ESP32(esp32dev_usb 펌웨어) USB-C → Pi 4 USB-A. CH340 드라이버가 없어도 중계기가 generic usb-serial 에
# 붙이고 usbfs 로 보드율을 쓴다(ch340.h). 진단: ./ch340cfg auto 115200
#   sh /mnt/rawdata/deskmate_tof/relay.sh stop
#   sh /mnt/rawdata/deskmate_tof/relay.sh status
# 자세 서비스(start.sh, TOFPOSTURE_PORTS=tcp:9100)가 먼저 떠 있어야 한다. 재부팅하면 둘 다 다시 띄운다.
D=$(cd "$(dirname "$0")" && pwd)
cd "$D" || exit 1
DEV=${RELAY_DEV:-auto}
PORT=${RELAY_PORT:-9100}
BAUD=${RELAY_BAUD:-115200}
case "$1" in
stop)
    for p in $(pidof uart_relay_board); do kill "$p"; done
    echo "relay stopped"; exit 0 ;;
status)
    PID=$(pidof uart_relay_board)
    [ -n "$PID" ] && echo "uart_relay_board pid=$PID" || echo "uart_relay_board not running"
    tail -n 3 relay.log 2>/dev/null; exit 0 ;;
esac
[ -x ./uart_relay_board ] || chmod +x ./uart_relay_board
for p in $(pidof uart_relay_board); do kill "$p"; done
sleep 1
# USB 노드가 생기면 getty 가 잡지 않게 (start.sh 와 같은 처리)
for n in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyACM0 /dev/ttyACM1; do
    [ -e "$n" ] && systemctl stop "serial-getty@$(basename "$n").service" 2>/dev/null
done
setsid ./uart_relay_board "$DEV" 127.0.0.1 "$PORT" "$BAUD" >> relay.log 2>&1 < /dev/null &
sleep 3
PID=$(pidof uart_relay_board)
[ -n "$PID" ] || { echo "relay 기동 실패:"; tail -n 5 relay.log; exit 1; }
echo "uart_relay_board pid=$PID ($DEV -> 127.0.0.1:$PORT)"
tail -n 2 relay.log
