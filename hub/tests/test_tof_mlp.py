"""Pi 4 순수 Python ToF 자세 MLP (posture/tof_mlp). numpy 없이 — 보드와 같은 조건이다.

numpy 순전파와의 일치는 dataset/tof_sim/tests/test_export_mlp_json.py 가 (가중치를 만든 쪽에서) 확인한다.
"""
from __future__ import annotations

import json
import math

import pytest

from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.ingest.uart_frame import TYPE_TOF_GRID, Frame, encode_frame, encode_tof_grid
from deskmate_hub.ingest.uart_source import RawUartReader
from deskmate_hub.posture.tof_mlp import FORMAT, TofMlpModel, TofMlpPosture, load_model, load_posture_from_config

# 2×2 격자(4 zone, 12 입력) 장난감 모델. 은닉 0 = zone 0 의 기준선 대비 변화(+ 멀어짐), 은닉 1 = 그 반대.
# 출력: upright 0, recline 2·h0, lean_forward 2·h1 → zone 0 이 멀어지면 recline, 가까워지면 lean_forward.
TOY = {
    "format": FORMAT, "source": "toy", "grid": {"width": 2, "height": 2},
    "classes": ["upright", "recline", "lean_forward"],
    "n_in": 12, "n_hidden": 2, "n_out": 3,
    "mu": [0.0] * 12, "sd": [1.0] * 12,
    "W1": [[0.0, 0.0]] * 4 + [[1.0, -1.0]] + [[0.0, 0.0]] * 7,
    "b1": [0.0, 0.0],
    "W2": [[0.0, 2.0, 0.0], [0.0, 0.0, 2.0]],
    "b2": [0.0, 0.0, 0.0],
}


def grid(dist, status=None, w=2, h=2):
    return {"grid_width": w, "grid_height": h, "distance_mm": list(dist), "status": list(status or [5] * len(dist))}


def test_features_match_training_definition():
    m = TofMlpModel(TOY)
    # zone 2 는 status 0(무효), zone 3 은 타깃 없음(-1/255) → FAR 2000, valid 0
    x = m.features([600, 900, 700, -1], [5, 10, 0, 255], baseline_mm=[650.0, None, 700.0, 700.0])
    assert x[:4] == [0.6, 0.9, 2.0, 2.0]
    # 기준선 없음(None)·무효 zone 은 FAR 2000 으로 계산하고 ±3 에서 자른다: (900-2000)/300, (2000-700)/300, (2000-700)/300
    assert x[4:8] == pytest.approx([(600 - 650) / 300, -3.0, 3.0, 3.0])
    assert x[8:] == [1.0, 1.0, 0.0, 0.0]
    # 큰 변화는 ±3 에서 잘린다, 기준선 없음 = 전부 FAR
    x = m.features([100, 100, 100, 100], [5, 5, 5, 5], baseline_mm=None)
    assert x[4:8] == [-3.0, -3.0, -3.0, -3.0]
    # semi-valid 를 끄면 6·10 이 무효
    assert m.valid_mask([500, 500], [6, 9], accept_semi_valid=False) == [False, True]
    with pytest.raises(ValueError):
        m.features([1, 2, 3], [5, 5, 5], None)


def test_forward_is_relu_then_softmax():
    m = TofMlpModel(TOY)
    x = [0.0] * 12
    x[4] = 0.5                                   # zone 0 이 150 mm 멀어짐
    p = m.forward(x)
    e = [math.exp(0.0), math.exp(1.0), math.exp(0.0)]
    assert p == pytest.approx([v / sum(e) for v in e]) and sum(p) == pytest.approx(1.0)
    assert m.predict(x)[0] == 1
    x[4] = -0.5
    assert m.predict(x)[0] == 2
    assert m.predict([0.0] * 12)[0] == 0        # 동률이면 첫 클래스


def test_bad_specs_are_rejected():
    with pytest.raises(ValueError):
        TofMlpModel(dict(TOY, format="other"))
    with pytest.raises(ValueError):
        TofMlpModel(dict(TOY, b1=[0.0]))
    with pytest.raises(ValueError):
        TofMlpModel(dict(TOY, b2=[0.0, float("nan"), 0.0]))


def test_posture_waits_for_baseline_then_votes():
    p = TofMlpPosture(TofMlpModel(TOY), {"baseline_frames": 4, "vote_frames": 3, "still_mm": 15,
                                         "present_min_coverage": 0.25, "max_person_mm": 1300})
    still = [600, 700, 650, 620]
    for i in range(3):
        v = p.update(grid(still), i * 0.2)
        assert v["posture"] == "unknown" and not v["baseline_ready"] and v["present"]
    v = p.update(grid(still), 0.6)                # 4번째 조용한 프레임 → 기준선 확정, 바로 판정
    assert v["baseline_ready"] and v["posture"] == "upright" and v["posture_confidence"] > 0.3
    assert p.baseline == [600.0, 700.0, 650.0, 620.0]
    moved = [900, 700, 650, 620]                  # zone 0 이 300 mm 멀어짐 → recline
    v = p.update(grid(moved), 0.8)
    assert v["raw_posture"] == "recline" and v["posture"] == "upright"      # 3표 중 1표 → 아직 다수 아님
    v = p.update(grid(moved), 1.0)
    assert v["posture"] == "recline" and v["source"] == "mlp"              # 2/3
    # 빈 자리가 away_reset_sec 이어지면 기준선을 버린다
    p.away_reset_sec = 1.0
    far = [2000, 2000, 2000, 2000]
    assert p.update(grid(far), 2.0)["baseline_ready"]
    v = p.update(grid(far), 3.5)
    assert not v["baseline_ready"] and v["posture"] == "unknown" and not v["present"]


def test_baseline_waits_while_moving():
    p = TofMlpPosture(TofMlpModel(TOY), {"baseline_frames": 4, "baseline_max_moving_frac": 0.2, "still_mm": 15,
                                         "present_min_coverage": 0.25})
    frames = [[600, 700, 650, 620], [700, 700, 650, 620], [600, 700, 650, 620], [700, 700, 650, 620]]
    for i, d in enumerate(frames):
        assert p.update(grid(d), i * 0.2)["posture"] == "unknown"
    assert p.baseline is None                     # 움직인 프레임 3/4 → 기준선 보류
    # 조용해지면 창(4프레임)에 움직인 프레임이 0 이 되는 5번째 프레임에서 확정된다(첫 조용한 프레임은 직전 대비 움직임)
    for i in range(4):
        assert not p.update(grid(frames[0]), 1.0 + i * 0.2)["baseline_ready"]
    assert p.update(grid(frames[0]), 2.0)["baseline_ready"]
    p.reset_baseline()
    assert p.baseline is None and p.update(grid(frames[0]), 5.0)["posture"] == "unknown"


def test_grid_shape_mismatch_is_reported_not_raised():
    p = TofMlpPosture(TofMlpModel(TOY), {"baseline_frames": 1})
    v = p.update(grid([600] * 6, w=3, h=2), 0.0)
    assert v["posture"] == "unknown" and "3x2" in v["error"] and p.last_error


def test_packaged_sim_weights_load_and_run_through_uart_path():
    """config/tof_mlp.json (export_mlp_json.py 산출물) 을 보드처럼 pkgutil 로 읽고 0x05 조각 → 판정까지 돈다."""
    m = load_model()
    assert (m.n_in, m.n_out) == (192, 10) and (m.grid_width, m.grid_height) == (8, 8)
    assert "away" in m.classes and "drowsy" not in m.classes            # 졸음은 모델 밖(시간 현상)
    assert m.source.endswith("mlp_sim60.npz")
    cfg = load_ingest_config()["tof"]
    posture = load_posture_from_config(dict(cfg, mlp=dict(cfg["mlp"], baseline_frames=5, vote_frames=5)))
    assert isinstance(posture, TofMlpPosture) and posture.vote_frames == 5
    reader = RawUartReader(SensorCache(), posture=posture)
    # 머리(위 2행)만 빈 벽, 아래 6행이 650 mm 인 앉은 사람 흉내. 8×8 은 조각 1개
    dist = [1500 if r < 2 else 650 for r in range(8) for _ in range(8)]
    status = [5] * 64
    kinds = []
    for i in range(8):
        payload = encode_tof_grid(8, 8, dist, status, frame_id=i, sensors_ok=2)[0]
        kinds += reader.feed_bytes(encode_frame(Frame(TYPE_TOF_GRID, i, i * 200, payload)))
    assert kinds == ["tof_grid"] * 8
    verdict = reader.cache.snapshot().tof_mlp.data
    assert verdict["baseline_ready"] and verdict["posture"] in m.classes and 0.0 <= verdict["posture_confidence"] <= 1.0
    assert reader.cache.snapshot().tof_grid.data["sensors_ok"] == 2


def test_missing_or_disabled_weights_fall_back_to_rule(tmp_path):
    logs = []
    assert load_posture_from_config({"mlp": {"enabled": False}}) is None
    assert load_posture_from_config({}) is None
    assert load_posture_from_config({"mlp": {"weights": str(tmp_path / "none.json")}}, log=logs.append) is None
    assert logs and "폴백" in logs[0]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "x"}), encoding="utf-8")
    assert load_posture_from_config({"mlp": {"weights": str(bad)}}, log=logs.append) is None
