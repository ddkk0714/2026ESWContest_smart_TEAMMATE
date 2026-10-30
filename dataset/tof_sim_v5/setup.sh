#!/usr/bin/env bash
# 리눅스 서버(UBAI 등) 준비. 이 파일 옆에 가상환경 .venv 를 만든다.
#   bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p runs          # git 에는 없는 폴더. 체크포인트·로그·검증셋이 여기 쌓인다
PY="${PYTHON:-python3}"
echo "[1/4] 가상환경 만드는 중 ($PY)"
"$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
echo "[2/4] numpy, mujoco, onnx 설치 중"
.venv/bin/python -m pip install -q "numpy>=1.26,<2.0" "mujoco>=3.2" onnx onnxruntime
# 학습은 GPU 에서 한다(slurm_train.sh). GPU 없는 PC 는 TORCH_INDEX=cpu bash setup.sh
# cu121 은 UBAI GPU 노드에서 동작 확인된 판(conda ailab 의 torch 2.4.1+cu121 과 같은 CUDA)
TORCH_INDEX="${TORCH_INDEX:-cu121}"
TORCH_PKG=torch
# cu121 판의 마지막 버전으로 고정한다. 고정하지 않으면 이미 깔린 CPU 판을 "설치됨"으로 보고 넘어간다
[ "$TORCH_INDEX" = cu121 ] && TORCH_PKG="torch==2.5.1"
echo "[3/4] torch($TORCH_INDEX) 설치 중"
.venv/bin/python -m pip install -q "$TORCH_PKG" --index-url "https://download.pytorch.org/whl/$TORCH_INDEX"
.venv/bin/python -c "import numpy, mujoco, torch; print('설치 확인 — numpy', numpy.__version__, '| mujoco', mujoco.__version__, '| torch', torch.__version__)"
echo "[4/4] 생성 속도 측정 (프로세스 1개)"
.venv/bin/python preview.py --bench 600 | tail -1
echo "준비 끝. 학습 시작: bash run_forever.sh <실행 이름> <시간>"
