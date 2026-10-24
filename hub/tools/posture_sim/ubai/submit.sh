#!/bin/bash
# 시드마다 작업 하나씩(병렬) 낸다. 시드를 한 작업에서 차례로 돌리면 시간이 시드 수만큼 늘어난다.
#   bash submit.sh <tag> <posture_sim|life_sim> "<시드들>" <각본.yaml ...>   [ARGS="--presence any"]
# 각본 경로는 repo_<tag>/hub/ 기준 상대 경로도 된다(예: tools/posture_sim/scenarios/posture_tour.yaml).
# UBAI 한도: 동시 10 작업, 제출 20 (sacctmgr 10-20).
set -euo pipefail
TAG="${1:?tag}"; CMD="${2:?posture_sim|life_sim}"; SEEDS="${3:?시드들}"; shift 3
DEST="${DEST:-$HOME/tof-sim-v5-lifesim}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for s in $SEEDS; do
  for sc in "$@"; do
    case "$sc" in /*) p="$sc" ;; *) p="$DEST/repo_$TAG/hub/$sc" ;; esac
    REPO="repo_$TAG" CMD="$CMD" SEEDS="$s" ARGS="${ARGS:-}" \
      sbatch --parsable --job-name="${CMD:0:4}$TAG" --output="$DEST/logs/slurm-%j.out" "$HERE/run.sbatch" "$p"
  done
done
