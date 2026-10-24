"""2단계 피로 모델(stage2_portable_v9_20261024, portable CNN) 보드 동봉 — 관찰 전용(2026-10-25).

config/stage2/ 의 모델·메타데이터가 personalization.yaml 로 기본 켜져 PC 에서는 패키지 파일로, 보드에서는 payload zip 에서
pkgutil 로 읽힌다(personalization/assets.py). FSM 결정은 바꾸지 않고 sensor_summary.personalization 에만 실린다.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from deskmate_hub.ingest import SensorCache
from deskmate_hub.live import LiveHub
from deskmate_hub.personalization import load_personalization_config
from deskmate_hub.personalization.assets import read_asset
from deskmate_hub.personalization.portable import sha256_hex
from deskmate_hub.personalization.runtime import PersonalizationRuntime

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "hub/deskmate_hub/config"


def test_shipped_stage2_files_match_metadata_and_contract():
    cfg = load_personalization_config()
    assert cfg["enabled"] is True and cfg["mode"] == "observe" and cfg["backend"] == "portable_cnn"
    model = read_asset(cfg["model_path"])
    meta = json.loads(read_asset(cfg["metadata_path"]).decode("utf-8"))
    assert sha256_hex(model) == meta["model_sha256"] == "fc85cccf97569db543a07cdf97d314192f5b01c71b8f36bd379a7139c1460c67"
    assert meta["input_shape"] == [1, cfg["window_ticks"], 17] and meta["normalization"] == "baseline" and meta["frame_period_sec"] == 10
    # 모델 카드: 시뮬(life_sim) 학습 — 실데이터가 아니라 커밋 가능(tof_mlp.json 선례)
    card = (CONFIG_DIR / "stage2/README_stage2.txt").read_text(encoding="utf-8")
    assert "life_sim" in card and "관찰 전용" in card


def test_runtime_loads_from_package_relative_path_and_rejects_linear():
    cfg = load_personalization_config()
    rt = PersonalizationRuntime(cfg, period=10.0, normalization="baseline")
    assert rt.status == "warming_up" and rt.backend is not None
    # demo-b(선형 정규화)에서는 계약이 어긋나 꺼진다 — FSM 은 그대로
    assert PersonalizationRuntime(cfg, period=10.0, normalization="linear").status == "unavailable"


def test_live_hub_default_reports_observe_only_personalization():
    hub = LiveHub(SensorCache(), out=io.StringIO(), profile="")
    assert hub.personalization.status == "warming_up"
    envelope = hub.tick_once(0.0)
    view = envelope["data"]["sensor_summary"]["personalization"]
    assert view["mode"] == "observe" and view["decision_source"] == "fsm" and view["status"] in ("warming_up", "predicted")


def test_payload_bundles_stage2_and_board_runtime_loads_it(tmp_path):
    executable = tmp_path / "hub.elf"
    executable.write_bytes(b"ELF fixture")
    (tmp_path / "stdlib").mkdir()
    result = subprocess.run([sys.executable, str(ROOT / "hub/atlas/tools/build_payload.py"),
                             "--executable", str(executable), "--hub", str(ROOT / "hub/deskmate_hub"),
                             "--config", str(CONFIG_DIR / "fsm.yaml"), "--ingest-config", str(CONFIG_DIR / "ingest.yaml"),
                             "--stdlib", str(tmp_path / "stdlib")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(executable) as archive:
        names = archive.namelist()
        assert {"deskmate_hub/config/stage2/common.portable.json", "deskmate_hub/config/stage2/common.metadata.json"} <= set(names)
        assert "deskmate_hub/personalization/assets.py" in names
    # 보드처럼 zip 에서 import — 파일 경로가 없으니 pkgutil 로 읽어 backend 가 서야 한다(YAML 없이)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); sys.modules['yaml'] = None\n"
            "from deskmate_hub.personalization import load_personalization_config\n"
            "from deskmate_hub.personalization.runtime import PersonalizationRuntime\n"
            "rt = PersonalizationRuntime(load_personalization_config(), period=10.0, normalization='baseline')\n"
            "print(rt.status, type(rt.backend).__name__)")
    probe = subprocess.run([sys.executable, "-c", code, str(executable)], capture_output=True, text=True, cwd=str(tmp_path))
    assert probe.returncode == 0 and probe.stdout.split() == ["warming_up", "PortableBackend"], probe.stderr[-2000:]
