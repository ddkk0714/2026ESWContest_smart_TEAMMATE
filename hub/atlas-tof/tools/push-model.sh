#!/bin/sh
# 학습 PC 에서: export 폴더의 posture_net.bin 을 Pi 4 서비스 폴더 models/ 에 넣는다.
# (IPK 에는 모델을 넣지 않는다 — camsvc 와 같은 규약.)
#
#     sh hub/atlas-tof/tools/push-model.sh <export 폴더> <보드 IP 또는 ssh 호스트> [ssh 사용자=root]
#     예) sh hub/atlas-tof/tools/push-model.sh ../tof-sim_ubai_random/export_20261014_1957_best 192.168.0.100
#
# 넣은 뒤 서비스를 다시 띄우거나(pi4-tof-activate.sh) POST /api/reset 이 아니라 재시작이 필요하다 —
# 모델은 시작할 때 한 번 읽는다.
set -eu

EXPORT_DIR="${1:?export 폴더}"
HOST="${2:?보드 IP}"
USER="${3:-root}"
SERVICE_ID=com.deskmate.tofposture1
SERVICE_DIR=/data/share/usr/atlas/services/$SERVICE_ID

[ -f "$EXPORT_DIR/posture_net.bin" ] || { echo "posture_net.bin 이 없다: $EXPORT_DIR"; exit 1; }

ssh "$USER@$HOST" "mkdir -p $SERVICE_DIR/models"
scp "$EXPORT_DIR/posture_net.bin" "$USER@$HOST:$SERVICE_DIR/models/posture_net.bin"
[ -f "$EXPORT_DIR/model_card.json" ] && scp "$EXPORT_DIR/model_card.json" "$USER@$HOST:$SERVICE_DIR/models/model_card.json"
# 서비스와 같은 주인으로 (설치 계정은 매번 바뀌므로 실행 파일 소유자를 읽는다)
ssh "$USER@$HOST" "OWNER=\$(ls -l $SERVICE_DIR/deskmate_tofposture | awk '{print \$3\":\"\$4}'); chown -R \$OWNER $SERVICE_DIR/models; ls -l $SERVICE_DIR/models"
echo "넣었다. 서비스를 다시 띄운다: ssh $USER@$HOST sh /tmp/pi4-tof-activate.sh"
