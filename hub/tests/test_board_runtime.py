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
    "deskmate_hub.personalization.portable",
    "deskmate_hub.personalization.ondevice",
    "deskmate_hub.personalization.self_check",
)


def test_board_path_imports_without_missing_c_extensions():
    # 새 인터프리터에서 C 확장을 막은 뒤 import 한다(이 프로세스는 이미 random 등을 불러 놓았을 수 있다).
    code = textwrap.dedent(f"""
        import sys
        for name in {MISSING_ON_BOARD!r} + ("numpy", "tflite_runtime", "tensorflow", "hashlib"):
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


def test_pure_sha256_matches_hashlib():
    import hashlib

    from deskmate_hub.personalization.portable import pure_sha256_hex

    # 55/56/64 바이트는 패딩이 블록 하나·둘로 갈리는 경계다.
    for data in (b"", b"abc", b"a" * 55, b"a" * 56, b"a" * 64, bytes(range(256)) * 160):
        assert pure_sha256_hex(data) == hashlib.sha256(data).hexdigest()


def test_portable_fingerprint_without_hashlib_sha256():
    # 10-12 Pi 4 실측: 제한 Python 의 hashlib 에 sha256 이 없어(AttributeError) 공통 모델이 unavailable 이 됐다.
    code = textwrap.dedent("""
        import sys, types
        sys.modules["hashlib"] = types.ModuleType("hashlib")
        from deskmate_hub.personalization.portable import sha256_hex
        print(sha256_hex(b"abc"))
    """)
    result = subprocess.run([sys.executable, "-c", code], cwd=HUB, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
