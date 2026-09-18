#!/bin/sh
# Pi 4(ATLAS) 에서 DESKMATE Hub 서비스를 기동 가능한 상태로 만든다.
# 재부팅·IPK 재설치 때마다 한 번씩 돌린다.
#
#     scp hub/atlas/tools/pi4-hub-activate.sh atlas:/tmp/ && ssh atlas sh /tmp/pi4-hub-activate.sh
#
# 왜 필요한가 — 재설치·재부팅으로 날아가는 것이 네 가지다.
#
#   1) 서비스 계정이 바뀐다. ATLAS 앱 매니저가 설치할 때마다 uid 를 새로 준다
#      (실측: u0_a5010 -> u0_a5015). 계정을 박아 둔 설정은 전부 어긋난다.
#   2) D-Bus 활성화에는 `User=` 가 **필수**다. 없으면 dbus 가
#      "Cannot do system-bus activation with no user" 로 거절하고,
#      `User=root` 도 "Failed to setup environment correctly" 로 막힌다.
#      반드시 서비스 디렉터리의 실제 소유 계정이어야 한다.
#   3) 그 계정이 버스 이름을 소유할 수 있어야 한다. IPK 가 넣는 .conf 는
#      `own` 을 root 에만 열어 두므로 실제 계정 정책을 추가해야 한다.
#   4) `/dev/serial0` 노드 권한은 재부팅하면 root:tty 0620 으로 돌아온다.
#      서비스 계정이 못 열면 UART 수신이 조용히 0 이 된다.
#
# hub.env 도 재설치 때 사라지므로 없으면 기본값으로 만든다.
# BusyBox 유저랜드를 가정한다(GNU 전용 옵션 금지).
set -u

SERVICE_ID=com.deskmate.hub1
SERVICE_DIR=/data/share/usr/atlas/services/$SERVICE_ID
DBUS_SERVICE=/data/share/usr/share/dbus-1/system-services/$SERVICE_ID.service
DBUS_CONF=/data/share/usr/share/dbus-1/system.d/$SERVICE_ID.conf
BIN=$SERVICE_DIR/deskmate_hub_service
MQTT_HOST="${DESKMATE_MQTT_HOST:-192.168.137.1}"
MQTT_PORT="${DESKMATE_MQTT_PORT:-1883}"

[ -x "$BIN" ] || { echo "서비스 실행 파일이 없다: $BIN"; exit 1; }

# 설치 때 부여된 실제 계정을 실행 파일 소유자에서 읽는다. 박아 두지 않는다.
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
echo "== 3. hub.env"
if [ -f "$SERVICE_DIR/hub.env" ]; then
    echo "   이미 있다 - 그대로 둔다"
else
    cat > "$SERVICE_DIR/hub.env" <<EOF
DESKMATE_HUB_MODE=live
DESKMATE_UART_DEV=/dev/serial0
DESKMATE_UART_BAUD=115200
DESKMATE_MQTT_HOST=$MQTT_HOST
DESKMATE_MQTT_PORT=$MQTT_PORT
EOF
    echo "   새로 만들었다"
fi
chown "$OWNER:$GROUP" "$SERVICE_DIR/hub.env"
chmod 640 "$SERVICE_DIR/hub.env"
cat "$SERVICE_DIR/hub.env"

echo
echo "== 4. UART 노드 권한"
NODE=$(readlink -f /dev/serial0 2>/dev/null)
[ -n "$NODE" ] || NODE=/dev/serial0
echo "   /dev/serial0 -> $NODE"
systemctl stop "serial-getty@$(basename "$NODE")" 2>/dev/null
chgrp "$GROUP" "$NODE" && chmod 660 "$NODE" && ls -l "$NODE"

echo
echo "== 5. dbus 재적재 후 활성화"
systemctl reload dbus >/dev/null 2>&1
sleep 2
OLD=$(pidof deskmate_hub_service)
[ -n "$OLD" ] && { echo "   기존 인스턴스 $OLD 정지"; kill "$OLD"; sleep 2; }

# dbus 가 이전 연결의 해제를 아직 못 봤으면 StartServiceByName 이 프로세스를 띄우지
# 않고 ALREADY_RUNNING(u 2)만 돌려준다. 프로세스가 실제로 뜰 때까지 재시도한다.
PID=""
TRY=1
while [ "$TRY" -le 4 ]; do
    busctl --system call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus StartServiceByName su "$SERVICE_ID" 0 2>&1 | head -n 2
    sleep 6
    PID=$(pidof deskmate_hub_service)
    [ -n "$PID" ] && break
    echo "   아직 안 떴다 - 재시도 $TRY"
    TRY=$((TRY + 1))
    sleep 3
done
if [ -z "$PID" ]; then
    echo "   기동 실패 - journal 을 본다"
    journalctl -n 40 --no-pager 2>/dev/null | grep -i "deskmate\|dbus-daemon" | grep -v audit | tail -n 6
    exit 1
fi
echo "   기동 완료 pid=$PID"

echo
echo "== 6. 확인 (UART 리포트는 30 s 주기)"
wget -q -O - http://127.0.0.1:8765/health 2>/dev/null; echo
sleep 35
journalctl -n 200 --no-pager 2>/dev/null | grep "DESKMATE UART" | tail -n 2
echo
echo "   rx>0 discarded=0 이면 정상이다. discarded 가 rx 와 같으면 Python 브리지가"
echo "   안 떠서 프레임을 받을 곳이 없다는 뜻이다(로그의 'python exec' 줄 확인)."
