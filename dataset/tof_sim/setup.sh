#!/usr/bin/env bash
# Linux server setup (e.g. UBAI). Creates .venv next to this file.
#   bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q "numpy>=1.26,<2.0" "mujoco>=3.2"
# The model is ~70 k parameters and data generation is CPU-bound: the CPU build is enough.
.venv/bin/python -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -c "import numpy, mujoco, torch; print('numpy', numpy.__version__, '| mujoco', mujoco.__version__, '| torch', torch.__version__)"
.venv/bin/python preview.py --bench 600 | tail -1
