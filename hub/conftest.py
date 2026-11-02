import os
import sys

# hub/ 를 sys.path 에 올려 `import deskmate_hub` 가 되도록 한다.
sys.path.insert(0, os.path.dirname(__file__))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _control_log_to_tmp(tmp_path, monkeypatch):
    """제어 진행 로그(control_log.py) 기본 경로를 테스트마다 tmp 로 — hub/logs 에 control-*.jsonl 이 쌓이지 않게."""
    from deskmate_hub.control import control_log
    monkeypatch.setattr(control_log, "DEFAULT_DIR", str(tmp_path / "logs"))
