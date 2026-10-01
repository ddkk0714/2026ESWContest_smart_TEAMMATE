"""실기기 없이 동일한 dry-run 시나리오로 세션별 SensorFrame 을 만든다."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from .frames import ROOT, phases_from_trace

sys.path.insert(0, str(ROOT / "tools"))
from demo_dryrun import T0, run_dryrun  # noqa: E402


def generate_session(name: str, seed: int, offset: int) -> tuple[list[dict], list[str | None], bytes]:
    output = io.StringIO()
    # 응답이 없으면 dry-run 이 ESM JSONL 을 logs/ 에 쓰지 않는다.
    result = run_dryrun(name, seed=seed, offset=offset, respond=None, frame_log=output)
    raw = output.getvalue().encode("utf-8")
    frames = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
    return frames, phases_from_trace(frames, result["trace"], origin=T0), raw
