#!/bin/sh
# Pi 4(ATLAS) 에서 ToF 자세 서비스(com.deskmate.tofposture1)를 기동 가능한 상태로 만든다.
# 재부팅·IPK 재설치 때마다 한 번씩 돌린다. hub/atlas/tools/pi4-hub-activate.sh 와 같은 이유·같은 구조다:
#
#     scp hub/atlas-tof/tools/pi4-tof-activate.sh atlas:/tmp/ && ssh atlas sh /tmp/pi4-tof-activate.sh
#
#   1) 서비스 계정 uid 는 설치할 때마다 바뀐다 → 실행 파일 소유자에서 읽는다.
#   2) D-Bus .service 에 User= 가 실제 설치 계정이어야 활성화된다.
#   3) .conf 의 own 정책에 그 계정을 넣어야 버스 이름을 가진다.
#   4) ESP32 USB 노드(/dev/ttyUSB*, /dev/ttyACM*)를 서비스 계정이 열 수 있어야 한다. 재부팅·재삽입 때 원복된다.
#   5) 모델 파일 models/posture_net.bin 이 서비스 계정 소유여야 읽힌다 (tools/push-model.sh 가 넣는다).
#
# tofposture.env 가 없으면 기본값으로 만든다. BusyBox 유저랜드 가정(GNU 전용 옵션 금지).
set -u

SERVICE_ID=com.deskmate.tofposture1
SERVICE_DIR=/data/share/usr/atlas/services/$SERVICE_ID
DBUS_SERVICE=/data/share/usr/share/dbus-1/system-services/$SERVICE_ID.service
DBUS_CONF=/data/share/usr/share/dbus-1/system.d/$SERVICE_ID.conf
BIN=$SERVICE_DIR/deskmate_tofposture
HTTP_PORT="${TOFPOSTURE_HTTP:-8771}"

[ -x "$BIN" ] || { echo "서비스 실행 파일이 없다: $BIN (IPK 설치 먼저)"; exit 1; }

OWNER=$(ls -l "$BIN" | awk '{print $3}')
GROUP=$(ls -l "$BIN" | awk '{print $4}')
echo "== 서비스 계정: $OWNER:$GROUP"

echo
echo "== 1. D-Bus 활성화 파일"
cat > "$DBUS_SERVICE" <<EOF
[D-BUS Service]
Name=$SERVICE_ID
Exec=$BIN
User=$OWNER
Group=$GROUP
EOF
cat "$DBUS_SERVICE"

echo
echo "== 2. 버스 이름 소유 정책"
cat > "$DBUS_CONF" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="root">
    <allow own="$SERVICE_ID"/>
    <allow send_destination="$SERVICE_ID"/>
    <allow receive_sender="$SERVICE_ID"/>
  </policy>
  <policy user="$OWNER">
    <allow own="$SERVICE_ID"/>
    <allow send_destination="$SERVICE_ID"/>
    <allow receive_sender="$SERVICE_ID"/>
  </policy>
  <policy context="default">
    <allow send_destination="$SERVICE_ID"/>
    <allow receive_sender="$SERVICE_ID"/>
  </policy>
</busconfig>
EOF
echo "   $DBUS_CONF 갱신"

echo
echo "== 3. tofposture.env"
if [ -f "$SERVICE_DIR/tofposture.env" ]; then
    echo "   이미 있다 - 그대로 둔다"
else
    cat > "$SERVICE_DIR/tofposture.env" <<EOF
# 비우면 /dev/ttyUSB* /dev/ttyACM* 를 이름순으로 최대 4개 쓴다. 고정하려면 콤마로 나열.
TOFPOSTURE_PORTS=
# auto = 921600/115200/460800/230400 중 센서 줄이 읽히는 값. 펌웨어 보드율을 알면 숫자로 고정
TOFPOSTURE_BAUD=auto
TOFPOSTURE_HTTP=$HTTP_PORT
TOFPOSTURE_BIND=0.0.0.0
TOFPOSTURE_FUSION=logprob
TOFPOSTURE_STDOUT=change
EOF
    echo "   새로 만들었다"
fi
chown "$OWNER:$GROUP" "$SERVICE_DIR/tofposture.env"
chmod 640 "$SERVICE_DIR/tofposture.env"
cat "$SERVICE_DIR/tofposture.env"

echo
echo "== 4. 모델 파일"
if [ -f "$SERVICE_DIR/models/posture_net.bin" ]; then
    chown -R "$OWNER:$GROUP" "$SERVICE_DIR/models"
    ls -l "$SERVICE_DIR/models/posture_net.bin"
else
    echo "   없다 - PC 에서 tools/push-model.sh <export 폴더> <보드 IP> 로 넣는다 (서비스는 모델 없이도 뜬다, /health 에 model_loaded=false)"
fi

echo
echo "== 5. ESP32 USB 노드 권한"
FOUND=0
for NODE in /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3 /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2 /dev/ttyACM3; do
    [ -e "$NODE" ] || continue
    FOUND=1
    systemctl stop "serial-getty@$(basename "$NODE")" 2>/dev/null
    chgrp "$GROUP" "$NODE" && chmod 660 "$NODE" && ls -l "$NODE"
done
[ "$FOUND" = 1 ] || echo "   ESP32 가 안 보인다 (/dev/ttyUSB*, /dev/ttyACM*). 꽂은 뒤 이 스크립트를 다시 돌리면 권한만 다시 준다"

echo
echo "== 6. dbus 재적재 후 활성화"
systemctl reload dbus >/dev/null 2>&1
sleep 2
OLD=$(pidof deskmate_tofposture)
[ -n "$OLD" ] && { echo "   기존 인스턴스 $OLD 정지"; kill "$OLD"; sleep 2; }

PID=""
TRY=1
while [ "$TRY" -le 4 ]; do
    busctl --system call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus StartServiceByName su "$SERVICE_ID" 0 2>&1 | head -n 2
    sleep 4
    PID=$(pidof deskmate_tofposture)
    [ -n "$PID" ] && break
    echo "   아직 안 떴다 - 재시도 $TRY"
    TRY=$((TRY + 1))
    sleep 3
done
if [ -z "$PID" ]; then
    echo "   기동 실패 - journal 을 본다"
    journalctl -n 40 --no-pager 2>/dev/null | grep -i "tofposture\|dbus-daemon" | grep -v audit | tail -n 8
    exit 1
fi
echo "   기동 완료 pid=$PID"

echo
echo "== 7. 확인"
sleep 3
wget -q -O - "http://127.0.0.1:$HTTP_PORT/health" 2>/dev/null; echo
echo
echo "   online 센서 수와 hz 가 보이면 정상. frames=0 이면 USB 노드 권한·펌웨어(env:tof_logger)·보드율 921600 을 본다."
echo "   자세: wget -q -O - http://127.0.0.1:$HTTP_PORT/api/state"
