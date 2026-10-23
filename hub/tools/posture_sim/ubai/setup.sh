#!/bin/bash
# UBAI 에서 한 번: 시뮬 실행 폴더를 만든다. 이미 있으면 빠진 것만 채운다.
#   bash setup.sh [--tofsim-from ~/tof-sim-v5-opt] [--model <ckpt.pt> ...]
# 결과 $DEST(기본 ~/tof-sim-v5-lifesim):
#   sim/     tofsim 사본(dataset/tof_sim_v5 = ~/tof-sim-v5-opt 코드: cad04 + l8 패치, l8/*.json 보정값)
#   models/  PostureNet 체크포인트
#   pylib/   pyyaml·matplotlib·pytest (--target 설치, PYTHONPATH 로)
#   logs/ runs/
# 파이썬은 $PY(기본 ~/tof-sim-v5-opt/.venv/bin/python — torch·mujoco·numpy 가 이미 있는 venv).
set -euo pipefail
DEST="${DEST:-$HOME/tof-sim-v5-lifesim}"
PY="${PY:-$HOME/tof-sim-v5-opt/.venv/bin/python}"
TOFSIM_FROM=""
MODELS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --tofsim-from) TOFSIM_FROM="$2"; shift 2 ;;
    --model) MODELS+=("$2"); shift 2 ;;
    *) echo "모르는 인자 $1"; exit 2 ;;
  esac
done
mkdir -p "$DEST"/{sim,models,pylib,logs,runs}
if [ -n "$TOFSIM_FROM" ]; then
  rsync -a --exclude runs --exclude '.venv' --exclude '__pycache__' --exclude '*.pt' "$TOFSIM_FROM"/ "$DEST/sim/"
fi
for m in "${MODELS[@]}"; do cp -n "$m" "$DEST/models/"; done
"$PY" -m pip install -q --target "$DEST/pylib" pyyaml matplotlib pytest 2>/dev/null || echo "[setup] pip --target 실패 — pylib 수동 확인"
echo "[setup] 점검"
"$PY" - <<PYEOF
import importlib, os, sys
sys.path[:0] = ["$DEST/pylib", "$DEST/sim"]
for m in ("numpy", "torch", "mujoco", "yaml", "tofsim"):
    try:
        importlib.import_module(m); print(f"  ok  {m}")
    except Exception as exc:
        print(f"  NO  {m}: {exc}")
print("  models:", sorted(os.listdir("$DEST/models")))
PYEOF
