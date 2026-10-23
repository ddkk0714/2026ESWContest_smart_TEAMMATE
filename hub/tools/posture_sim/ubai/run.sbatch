#!/bin/bash
# 시뮬 한 작업(시드 여러 개를 차례로). 보통은 submit.sh 가 시드마다 한 작업씩 낸다(병렬).
#   REPO=repo_v7 CMD=posture_sim SEEDS="1" ARGS="--presence any" sbatch run.sbatch <각본.yaml ...>
#   CMD = posture_sim(자세판정 시뮬) | life_sim(전체 시뮬)
#SBATCH --job-name=sim
#SBATCH --partition=gpu6
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=05:00:00
set -uo pipefail
DEST="${DEST:-$HOME/tof-sim-v5-lifesim}"
REPO="${REPO:?REPO=repo_<tag>}"
CMD="${CMD:-posture_sim}"
PY="${PY:-$HOME/tof-sim-v5-opt/.venv/bin/python}"
MODEL="${MODEL:-$DEST/models/opt_l8v5_c256_best.pt}"
OUT="${OUT:-$DEST/runs/${CMD}_${REPO#repo_}}"
cd "$DEST/$REPO/hub"
export PYTHONPATH="$DEST/pylib:$DEST/$REPO/hub"
export TOFSIM_ROOT="$DEST/sim"
export OMP_NUM_THREADS=2 MUJOCO_GL=egl LIFESIM_TORCH_THREADS=1
for s in ${SEEDS:-1}; do
  echo "[run.sbatch] $(date) $CMD seed=$s model=$MODEL out=$OUT args=$* ${ARGS:-}"
  "$PY" -X utf8 -m "tools.$CMD" run "$@" --seed "$s" --model "$MODEL" --out "$OUT" ${ARGS:-}
done
