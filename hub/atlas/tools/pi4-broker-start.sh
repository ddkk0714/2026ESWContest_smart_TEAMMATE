#!/bin/sh
# Pi 4(ATLAS) 에서 DESKMATE 개발용 MQTT 브로커를 띄운다. 재부팅 후 한 번 돌린다.
#
#     ssh atlas sh /data/share/deskmate/bin/pi4-broker-start.sh
#
# 바이너리는 `tools/atlas-hotspot-broker/build_mosquitto.sh` 가 SDK 컨테이너에서
# 정적 크로스빌드한 mosquitto 2.0.20 이다. 보드 opkg 는 LG 내부 feed 만 보므로
# 설치 경로가 이것뿐이다.
#
# 기존 절차와 다른 점: `/tmp` 가 아니라 `/data/share/deskmate/bin` 에 둔다.
# `/tmp` 는 재부팅하면 비어서 매번 PC 에서 다시 밀어 넣어야 했다. 이 경로는
# mmcblk0p5 라 재부팅해도 남고, 이 스크립트만 다시 돌리면 된다.
# (자동 기동은 아직 없다 — native-service IPK 로 감싸는 것이 후보)
set -u

BIN_DIR=/data/share/deskmate/bin
LOG=/data/share/deskmate/mosquitto.log

[ -x "$BIN_DIR/mosquitto" ] || {
    echo "브로커 바이너리가 없다: $BIN_DIR/mosquitto"
    echo "PC 에서: tools/atlas-hotspot-broker/build_mosquitto.sh 후 dist/ 를 이 경로로 복사"
    exit 1
}

# ssh 로 `pkill -f mosquitto` 를 보내면 그 문자열이 든 ssh 셸 자신이 죽는다. pidof 를 쓴다.
for p in $(pidof mosquitto); do
    echo "기존 브로커 $p 정지"
    kill "$p"
done
sleep 1

setsid "$BIN_DIR/mosquitto" -c "$BIN_DIR/mosquitto.conf" -v > "$LOG" 2>&1 < /dev/null &
sleep 3

PID=$(pidof mosquitto)
[ -n "$PID" ] || { echo "기동 실패"; tail -n 20 "$LOG"; exit 1; }
echo "브로커 pid=$PID"

netstat -ltn 2>/dev/null | grep -q ":1883 " && echo "1883 LISTEN" || {
    echo "1883 리스닝 없음"; tail -n 20 "$LOG"; exit 1
}

"$BIN_DIR/mosquitto_pub" -h 127.0.0.1 -t deskmate/selftest -m ok && echo "로컬 pub 정상"

echo
echo "허브는 hub.env 의 DESKMATE_MQTT_HOST 로 붙는다 (같은 보드면 127.0.0.1)."
echo "Pi 5 앱은 이 보드 주소로 붙는다:"
ip -4 -o addr show 2>/dev/null | grep -v " lo " | awk '{print "  " $2 " " $4}'
