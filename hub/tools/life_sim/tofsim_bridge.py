"""dataset/tof_sim_v5 (`tofsim` 패키지) 찾기.

tofsim 은 origin/main 에 없다(feat/tof-sim-v5 의 dataset/tof_sim_v5, UBAI 의 ~/tof-sim-v5-* 사본). 다음 순서로 찾는다.
  1. 환경변수 TOFSIM_ROOT (tofsim/ 폴더를 품은 디렉터리)
  2. 레포의 dataset/tof_sim_v5
  3. ~/tof-sim-v5-lifesim/sim, ~/tof-sim-v5-opt, ~/tof-sim-v5-cad04 (UBAI 관례)
없으면 None — 시험은 건너뛰고(skip), CLI 는 안내 메시지로 끝난다.
"""
from __future__ import annotations

import importlib
import os
import sys
from types import ModuleType

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

CANDIDATES = (
    os.environ.get("TOFSIM_ROOT", ""),
    os.path.join(_REPO, "dataset", "tof_sim_v5"),
    os.path.expanduser("~/tof-sim-v5-lifesim/sim"),
    os.path.expanduser("~/tof-sim-v5-opt"),
    os.path.expanduser("~/tof-sim-v5-cad04"),
)

_cache: dict[str, ModuleType | None] = {}


def find_root() -> str | None:
    for root in CANDIDATES:
        if root and os.path.isfile(os.path.join(root, "tofsim", "session.py")):
            return root
    return None


def load(root: str | None = None) -> ModuleType | None:
    """`tofsim` 패키지를 import 해 돌려준다(없으면 None). 같은 프로세스에서는 한 번만 올린다."""
    if "tofsim" in _cache:
        return _cache["tofsim"]
    root = root or find_root()
    if root is None:
        _cache["tofsim"] = None
        return None
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        mod = importlib.import_module("tofsim")
        importlib.import_module("tofsim.session")
    except Exception as exc:  # noqa: BLE001 — import 실패 사유를 그대로 안내한다
        print(f"[life_sim] tofsim import 실패 ({root}): {exc}", file=sys.stderr)
        mod = None
    _cache["tofsim"] = mod
    return mod
