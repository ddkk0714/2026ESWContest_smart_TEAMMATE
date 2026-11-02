#!/bin/sh
# Pi 4 를 재부팅 뒤 한 번에 살린다: ESP32 USB(CH340) → 브로커·hub → ToF 자세 서비스.
# ATLAS 는 부팅 자동 시작이 안 돼서(docs/reboot-recovery.md) Pi 5 앱이 hub 를 못 찾으면 SSH 로 이걸 부른다.
# 여러 번 불러도 된다 — 이미 떠 있는 것은 건너뛴다. BusyBox sh 기준.
#
#   sh /data/share/deskmate/recovery/bringup.sh
#
# 출력 마지막 줄은 앱이 그대로 보여 준다: "bringup hub=ok tof=ok uart=/dev/ttyUSB0".
set -u
R=${DESKMATE_RECOVERY_PREFIX:-/data/share/deskmate/recovery}
TOF=${DESKMATE_TOF_DIR:-/mnt/rawdata/deskmate_tof}
LOG=$R/bringup.log

log() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
healthy() { wget -q -T 3 -O - "$1" 2>/dev/null | grep -q "$2"; }

log "bringup start"

# 1. ESP32 USB. ATLAS 커널에 ch341 이 없어 generic 드라이버에 붙이고 115200 으로 맞춘다.
#    재연결 때 ttyUSB1 로 잡히거나 지난번 링크가 남아 있으면 hub(hub.env: /dev/ttyUSB0)가 못 연다.
if [ -x "$TOF/ch340cfg" ]; then
    i=0
    until ls /dev/ttyUSB[0-9] >/dev/null 2>&1 || [ $i -ge 10 ]; do
        "$TOF/ch340cfg" auto 115200 >/dev/null 2>&1
        i=$((i + 1))
        sleep 1
    done
    "$TOF/ch340cfg" auto 115200 >>"$LOG" 2>&1
fi
if [ -L /dev/ttyUSB0 ] && [ ! -e /dev/ttyUSB0 ]; then
    rm -f /dev/ttyUSB0
fi
if [ ! -e /dev/ttyUSB0 ]; then
    for d in /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3; do
        [ -c "$d" ] && { ln -sf "$d" /dev/ttyUSB0; log "ttyUSB0 -> $d"; break; }
    done
fi
[ -e /dev/ttyUSB0 ] || log "ESP32 USB 없음 — FSM 은 뜨지만 센서 값이 없다"

# 2. 브로커 + hub (systemd runtime 유닛, 감독 포함). UART 권한은 lifecycle hub-prepare 가 준다.
if healthy http://127.0.0.1:8765/health '"status":"ok"'; then
    sh "$R/lifecycle.sh" hub-prepare >>"$LOG" 2>&1
    log "hub already up"
else
    sh "$R/install.sh" pi4 --runtime-only >>"$LOG" 2>&1
    i=0
    until healthy http://127.0.0.1:8765/health '"status":"ok"' || [ $i -ge 20 ]; do
        i=$((i + 1))
        sleep 1
    done
fi

# 3. ToF 자세 서비스(8771). hub 가 0x05 격자를 tcp:9100 으로 넘긴다.
if [ -f "$TOF/start.sh" ] && ! healthy http://127.0.0.1:8771/health '"ok":true'; then
    sh "$TOF/start.sh" >>"$LOG" 2>&1
fi

hub=down; tof=down
healthy http://127.0.0.1:8765/health '"status":"ok"' && hub=ok
healthy http://127.0.0.1:8771/health '"ok":true' && tof=ok
uart=none
[ -e /dev/ttyUSB0 ] && uart=$(readlink -f /dev/ttyUSB0)
log "bringup hub=$hub tof=$tof uart=$uart"
[ "$hub" = ok ]
