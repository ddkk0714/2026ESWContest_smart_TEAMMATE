"""사람별 자세 보정(posture_calib.py, 11-03) — 그 사람 파일이 있을 때만 바꾸고, 다른 프로필·파일 없는 프로필은 그대로."""
from __future__ import annotations

import json

from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.cache import Sample
from deskmate_hub.posture_calib import PersonCalib, PostureCalibrator

from test_tof_service import svc

LABELS = ["upright", "lean_forward", "recline", "face_down", "chin_rest", "drowsy", "away", "misplaced", "forward_head"]


def _calib(target: str) -> dict:
    """어떤 입력이든 target 을 고르는 보정(편향만)."""
    n = 3 * len(LABELS)
    return {"labels": LABELS, "mean": [0.0] * n, "std": [1.0] * n, "W": [[0.0] * len(LABELS) for _ in range(n)],
            "b": [5.0 if c == target else 0.0 for c in LABELS]}


def _with_sensor_probs(sample: Sample) -> Sample:
    data = dict(sample.data)
    data["sensors"] = [{"name": f"tcp:9100#s{i}", "probs": dict(data["probs"])} for i in (0, 1)]
    return Sample(received=sample.received, ts=sample.ts, seq=None, data=data)


def test_classify_uses_weights_on_log_probs():
    c = _calib("lean_forward")
    c["b"] = [0.0] * len(LABELS)
    for i, label in enumerate(LABELS):           # 융합 log p 를 그대로 점수로: 가장 큰 확률의 라벨
        c["W"][i][i] = 1.0
    data = _with_sensor_probs(svc(1.0, posture="chin_rest", conf=0.9)).data
    label, conf = PersonCalib(c).classify(data)
    assert label == "chin_rest" and 0.0 < conf < 1.0


def test_only_the_profile_with_a_file_changes(tmp_path):
    (tmp_path / "pA").mkdir()
    (tmp_path / "pA" / "posture_calib.json").write_text(json.dumps(_calib("lean_forward")), encoding="utf-8")
    cache = SensorCache()
    cache.put("tof_service", _with_sensor_probs(svc(1.0, posture="chin_rest", conf=0.9)))
    cal = PostureCalibrator(str(tmp_path))

    cal.follow("pA")
    out = cal.apply(cache.snapshot())
    assert out.tof_service.data["posture"] == "lean_forward" and out.tof_service.data["model_posture"] == "chin_rest"
    assert cache.snapshot().tof_service.data["posture"] == "chin_rest"          # 캐시 원본은 그대로(사본만 바꿈)

    cal.follow("pB")                                                            # 파일 없는 사람 → 판정 그대로
    view = cache.snapshot()
    assert cal.apply(view) is view and cal.snapshot()["active"] is False
    cal.follow("pA")
    assert cal.apply(cache.snapshot()).tof_service.data["posture"] == "lean_forward"


def test_broken_or_missing_inputs_leave_view_alone(tmp_path):
    (tmp_path / "pA").mkdir()
    (tmp_path / "pA" / "posture_calib.json").write_text("{not json", encoding="utf-8")
    cal = PostureCalibrator(str(tmp_path))
    cal.follow("pA")
    assert cal.calib is None
    cal = PostureCalibrator(None)
    cal.follow("pA")
    cache = SensorCache()
    cache.put("tof_service", svc(1.0, posture="upright", conf=0.9))
    view = cache.snapshot()
    assert cal.apply(view) is view
