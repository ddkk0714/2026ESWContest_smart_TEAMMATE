#!/bin/sh
# Pi 5(ATLAS) 에서 자세 판정 서비스(camsvc)를 기동한다. 재부팅 때마다 한 번 돌린다.
#
#     ssh pi5 sh /data/share/deskmate/bin/pi5-camsvc-start.sh
#
# 재부팅으로 날아가는 것이 세 가지다. 셋 다 증상이 "자세 탭이 데모로 뜬다" 라서
# 겉으로는 구분이 안 된다.
#
#   1) 서비스가 안 떠 있다. 앱의 소스 선택은 camsvc -> 보드 직결 -> 데모 순이라,
#      서비스가 없으면 조용히 데모로 떨어진다.
#   2) `/dev/ttyACM*` 권한이 root:dialout 로 돌아간다. camsvc 는 앱 계정으로
#      도는데 그 계정은 dialout 이 아니라 포트를 못 연다.
#      이때 /health 의 port 가 "(없음)" 으로 나온다 - 그게 단서다.
#   3) 포트 선택. camsvc 는 인터페이스 이름이 "Vision Stream" 인 ttyACM 을
#      찾는데, 실측(2026-09-18)에서는 `A5 5A` 프레임이 "ESP32-CAM UART" 쪽으로
#      왔다. 그래서 여기서 **매직이 실제로 보이는 포트**를 골라 넘긴다.
#
# BusyBox 유저랜드를 가정한다(GNU 전용 옵션 금지).
set -u

SERVICE_ID=com.deskmate.camsvc1
SERVICE_DIR=/data/share/usr/atlas/services/$SERVICE_ID
BIN=$SERVICE_DIR/deskmate_camsvc
LOG=/data/share/deskmate/camsvc.log

[ -x "$BIN" ] || { echo "camsvc 실행 파일이 없다: $BIN"; exit 1; }

# 설치 때 부여된 계정을 실행 파일에서 읽는다. 설치마다 바뀌므로 박아 두지 않는다.
# ls 의 그룹 칸은 길면 잘리므로 id 로 실제 이름을 얻는다.
OWNER=$(ls -l "$BIN" | awk '{print $3}')
GROUP=$(id -gn "$OWNER" 2>/dev/null)
[ -n "$GROUP" ] || GROUP="$OWNER"
echo "== 서비스 계정: $OWNER:$GROUP"

echo
echo "== 1. 카메라 포트 권한"
for dev in /dev/ttyACM0 /dev/ttyACM1; do
    [ -e "$dev" ] || continue
    chgrp "$GROUP" "$dev" && chmod 660 "$dev" && ls -l "$dev"
done

echo
echo "== 2. 프레임이 실제로 오는 포트 고르기"
# 매직 A5 5A 가 보이는 포트가 진짜다. 이름표(Vision Stream)만 믿지 않는다.
PICK="${CAMSVC_DEVICE:-}"
if [ -n "$PICK" ]; then
    echo "   환경변수 지정: $PICK"
else
    for dev in /dev/ttyACM0 /dev/ttyACM1; do
        [ -e "$dev" ] || continue
        rm -f /tmp/camprobe.bin
        dd if="$dev" of=/tmp/camprobe.bin bs=1 count=512 2>/dev/null &
        probe=$!
        sleep 3
        kill "$probe" 2>/dev/null
        size=$(wc -c < /tmp/camprobe.bin 2>/dev/null || echo 0)
        if [ "$size" -gt 0 ] && od -A n -t x1 /tmp/camprobe.bin 2>/dev/null | tr -d ' \n' | grep -q "a55a"; then
            echo "   $dev — A5 5A 프레임 확인 ($size B)"
            PICK="$dev"
            break
        fi
        echo "   $dev — 프레임 없음 ($size B)"
    done
fi
[ -n "$PICK" ] || { echo "   프레임이 오는 포트가 없다. ESP32-CAM 송신·배선을 본다."; exit 1; }

echo
echo "== 3. 기동"
for p in $(pidof deskmate_camsvc); do
    echo "   기존 $p 정지"
    kill "$p"
done
sleep 2
mkdir -p "$(dirname "$LOG")"
CAMSVC_DEVICE="$PICK" setsid "$BIN" > "$LOG" 2>&1 < /dev/null &
sleep 10

PID=$(pidof deskmate_camsvc)
[ -n "$PID" ] || { echo "   기동 실패"; tail -n 10 "$LOG"; exit 1; }
echo "   pid=$PID  port=$PICK"

echo
echo "== 4. 확인"
wget -q -O - --timeout=5 http://127.0.0.1:8770/health 2>&1; echo
echo
echo "   frames 가 0 이고 dropped 만 늘면, 오는 프레임이 camsvc 가 먹는 타입이"
echo "   아니라는 뜻이다(preview=4 를 원하는데 coverage=1 만 오는 경우)."
echo "   port 가 \"(없음)\" 이면 권한 문제다."
