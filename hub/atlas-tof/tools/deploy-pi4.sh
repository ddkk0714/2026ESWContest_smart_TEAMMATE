#!/usr/bin/env bash
# PC(Linux/macOS/Git Bash)에서 Pi 4(ATLAS)로 ToF 자세 서비스 묶음을 올리고 자가 시험 뒤 띄운다.
#   bash deploy-pi4.sh 172.16.34.146 [묶음 폴더=../../../../rpi4_tof_deploy/pi4]
set -euo pipefail
IP="${1:?보드 IP}"; HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="${2:-}"; [ -n "$BUNDLE" ] || { [ -d "$HERE/pi4" ] && BUNDLE="$HERE/pi4" || BUNDLE="$HERE/../../../../rpi4_tof_deploy/pi4"; }; DEST=/mnt/rawdata/deskmate_tof; T="root@$IP"
SSH="ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
[ -f "$BUNDLE/deskmate_tofposture" ] || { echo "묶음이 아니다: $BUNDLE" >&2; exit 1; }
$SSH "$T" "tr -d '\0' < /proc/device-tree/model; echo; for p in \$(pidof deskmate_tofposture); do kill \$p; done; mkdir -p $DEST"
tar -cf - -C "$BUNDLE" . | $SSH "$T" "tar -xf - -C $DEST"
$SSH "$T" "chmod +x $DEST/deskmate_tofposture $DEST/tofposture_selftest $DEST/*.sh && sh $DEST/selftest.sh && sh $DEST/start.sh"
echo "PC 에서 확인: curl http://$IP:8771/api/state"
