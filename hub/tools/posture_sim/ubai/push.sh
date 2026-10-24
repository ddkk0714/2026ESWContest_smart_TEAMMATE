#!/bin/bash
# PC → UBAI: 지금 워크트리의 hub/ · tools/ 를 묶어 UBAI 의 $DEST/repo_<tag> 로 올린다(git 이 아니라 워킹트리 그대로).
#   bash hub/tools/posture_sim/ubai/push.sh v7            # 레포 루트에서
# 환경변수: UBAI_HOST(기본 ubai, ~/.ssh/config 별칭) · DEST(기본 ~/tof-sim-v5-lifesim)
set -euo pipefail
TAG="${1:?사용: push.sh <tag>}"
HOST="${UBAI_HOST:-ubai}"
DEST="${DEST:-tof-sim-v5-lifesim}"
ROOT="$(git rev-parse --show-toplevel)"
TMP="$(mktemp -d)"
tar -C "$ROOT" --exclude='__pycache__' --exclude='.pytest_cache' --exclude='runs' --exclude='*.mp4' --exclude='*.gif' \
    -czf "$TMP/repo.tgz" hub tools
scp -q "$TMP/repo.tgz" "$HOST:$DEST/repo_$TAG.tgz"
ssh "$HOST" "cd $DEST && rm -rf repo_$TAG && mkdir repo_$TAG && tar -xzf repo_$TAG.tgz -C repo_$TAG && rm repo_$TAG.tgz && echo \"[push] $DEST/repo_$TAG ($(git -C "$ROOT" rev-parse --short HEAD), $(git -C "$ROOT" rev-parse --abbrev-ref HEAD))\" | tee repo_$TAG/PUSHED"
rm -rf "$TMP"
echo "다음: ssh $HOST 'cd $DEST && bash repo_$TAG/hub/tools/posture_sim/ubai/submit.sh $TAG posture_sim \"1 2\" <각본...>'"
