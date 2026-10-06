"""Pi 4 보드의 제한 Python 에서도 hub 가 import 되는지 — 없는 C 확장을 막고 보드 경로 모듈을 불러 본다.

ATLAS `/restricted/python3` 에는 C 확장이 16개뿐이라 `_socket`·`_random`·`_ssl` 이 없다(09-17 실측,
`hub/atlas/README.md`). 순수 Python stdlib 은 IPK 에 함께 묶이지만(`atlas/tools/build_payload.py`), 그 모듈이
C 확장을 import 하면 보드에서 ImportError 로 hub 가 아예 뜨지 않는다. 예: `statistics` → `random` → `_random`.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
MISSING_ON_BOARD = ("_socket", "_random", "_ssl")

# 보드 bridge 가 실제로 불러오는 경로(service_bridge._run_live_bridge)
BOARD_MODULES = (
    "deskmate_hub.service_bridge",
    "deskmate_hub.live",
    "deskmate_hub.ingest.mqtt_lines",
    "deskmate_hub.ingest.uart_source",
    "deskmate_hub.esm",
    "deskmate_hub.features.baseline",
    "deskmate_hub.control.dispatcher",
    "deskmate_hub.inference.report",
    "deskmate_hub.personalization.runtime",
    "deskmate_hub.personalization.tflite_backend",
)


def test_board_path_imports_without_missing_c_extensions():
    # 새 인터프리터에서 C 확장을 막은 뒤 import 한다(이 프로세스는 이미 random 등을 불러 놓았을 수 있다).
    code = textwrap.dedent(f"""
        import sys
        for name in {MISSING_ON_BOARD!r} + ("numpy", "tflite_runtime", "tensorflow"):
            sys.modules[name] = None          # import 하면 ImportError
        for name in ("random", "socket", "ssl", "statistics", "uuid", "tempfile"):
            sys.modules.pop(name, None)
        import importlib
        for module in {BOARD_MODULES!r}:
            importlib.import_module(module)
        print("ok")
    """)
    result = subprocess.run([sys.executable, "-c", code], cwd=HUB, capture_output=True, text=True)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr[-2000:]


def test_pure_median_matches_statistics():
    import statistics

    import pytest

    from deskmate_hub.mathutil import median

    for xs in ([3.0], [1.0, 2.0], [5, 1, 4, 2, 3], [2.5, -1.0, 7.25, 0.0], (x * 0.1 for x in range(11))):
        xs = list(xs)
        assert median(xs) == statistics.median(xs)
    with pytest.raises(ValueError):
        median([])
