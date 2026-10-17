#!/bin/sh
# Pi 4(ATLAS) 에서 ToF UART 중계기(uart_relay_board)를 띄우거나 내린다. BusyBox sh.
#   sh /mnt/rawdata/deskmate_tof/relay.sh          # (재)시작: ttyAMA0 → 127.0.0.1:9100
#   sh /mnt/rawdata/deskmate_tof/relay.sh stop
#   sh /mnt/rawdata/deskmate_tof/relay.sh status
# 자세 서비스(start.sh, TOFPOSTURE_PORTS=tcp:9100)가 먼저 떠 있어야 한다. 재부팅하면 둘 다 다시 띄운다.
D=$(cd "$(dirname "$0")" && pwd)
cd "$D" || exit 1
DEV=${RELAY_DEV:-/dev/ttyAMA0}
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
setsid ./uart_relay_board "$DEV" 127.0.0.1 "$PORT" "$BAUD" >> relay.log 2>&1 < /dev/null &
sleep 3
PID=$(pidof uart_relay_board)
[ -n "$PID" ] || { echo "relay 기동 실패:"; tail -n 5 relay.log; exit 1; }
echo "uart_relay_board pid=$PID ($DEV -> 127.0.0.1:$PORT)"
tail -n 2 relay.log
