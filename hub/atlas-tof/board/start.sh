#!/bin/sh
# Pi 4(ATLAS) 에서 ToF 자세 서비스를 띄운다 (개발용 직접 실행, root SSH).
#     ssh root@<PI4> sh /mnt/rawdata/deskmate_tof/start.sh
# 이 폴더가 그대로 보드의 /mnt/rawdata/deskmate_tof 다(쓰기 가능·재부팅 뒤에도 남는 개발 공간).
# 재부팅하면 프로세스는 사라지므로 다시 이 스크립트를 돌린다. BusyBox sh 기준.
D=$(cd "$(dirname "$0")" && pwd)
cd "$D" || exit 1
[ -x ./deskmate_tofposture ] || chmod +x ./deskmate_tofposture ./tofposture_selftest 2>/dev/null
[ -f tofposture.env ] || cp tofposture.env.example tofposture.env
[ -f models/posture_net.bin ] || { echo "models/posture_net.bin 이 없다"; exit 1; }

# 이미 떠 있으면 내린다 (pkill -f 는 ssh 셸까지 죽이므로 pidof)
for p in $(pidof deskmate_tofposture); do kill "$p"; done
sleep 1

# ESP32 USB 노드: 있으면 getty 가 잡고 있지 않게
for n in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3 /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2 /dev/ttyACM3; do
    [ -e "$n" ] && systemctl stop "serial-getty@$(basename "$n").service" 2>/dev/null
done

setsid ./deskmate_tofposture >> tofposture.out 2>> tofposture.log < /dev/null &
sleep 3
PID=$(pidof deskmate_tofposture)
[ -n "$PID" ] || { echo "기동 실패:"; tail -n 20 tofposture.log; exit 1; }
echo "deskmate_tofposture pid=$PID"
PORT=$(grep '^TOFPOSTURE_HTTP=' tofposture.env | cut -d= -f2)
[ -n "$PORT" ] || PORT=8771
wget -q -O - "http://127.0.0.1:$PORT/health"; echo
echo "로그: tail -f $D/tofposture.log   자세: wget -q -O - http://127.0.0.1:$PORT/api/state"
