"""Pi 4 순수 Python ToF 자세 MLP (posture/tof_mlp, 후보 A). numpy 없이 — 보드와 같은 조건이다.

표준(10-17): 센서별 8×8 을 따로 추론해 log-softmax 합으로 융합한다. 16×8 스티칭은 쓰지 않는다.
numpy 순전파와의 일치는 dataset/tof_sim/tests/test_export_mlp_json.py 가 (가중치를 만든 쪽에서) 확인한다.
"""
from __future__ import annotations

import json
import math
import time

import pytest

from deskmate_hub.ingest import Sample, SensorCache, load_ingest_config
from deskmate_hub.ingest.uart_frame import TYPE_TOF_GRID, Frame, encode_frame, encode_tof_sensor_frames
from deskmate_hub.ingest.uart_source import RawUartReader
from deskmate_hub.posture.tof_mlp import (
    FORMAT, TofMlpModel, TofMlpPosture, fuse_log_softmax, load_model, load_posture_from_config,
)

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
STILL = [600, 700, 650, 620]
FAR = [2000, 2000, 2000, 2000]


def sample(*frames, w=2, h=2):
    """센서 0, 1, … 순서의 dist 또는 (dist, status) → 조립 완료 표본 dict."""
    sensors = {}
    for i, f in enumerate(frames):
        dist, status = (f, [5] * len(f)) if isinstance(f[0], int) else f
        sensors[i] = {"distance_mm": list(dist), "status": list(status)}
    return {"grid_width": w, "grid_height": h, "frame_id": 0, "sensors_ok": (1 << len(frames)) - 1, "flags": 0,
            "sensors": sensors}


def posture(**cfg):
    base = {"baseline_frames": 4, "vote_frames": 3, "still_mm": 15, "present_min_coverage": 0.25,
            "max_person_mm": 1300}
    return TofMlpPosture(TofMlpModel(TOY), dict(base, **cfg))


def test_features_match_training_definition():
    m = TofMlpModel(TOY)
    # zone 2 는 status 0(무효), zone 3 은 타깃 없음(-1/255) → FAR 2000, valid 0
    x = m.features([600, 900, 700, -1], [5, 10, 0, 255], baseline_mm=[650.0, None, 700.0, 700.0])
    assert x[:4] == [0.6, 0.9, 2.0, 2.0]
    # 기준선 없음(None)·무효 zone 은 FAR 2000 으로 계산하고 ±3 에서 자른다: (900-2000)/300, (2000-700)/300, (2000-700)/300
    assert x[4:8] == pytest.approx([(600 - 650) / 300, -3.0, 3.0, 3.0])
    assert x[8:] == [1.0, 1.0, 0.0, 0.0]
    x = m.features([100, 100, 100, 100], [5, 5, 5, 5], baseline_mm=None)
    assert x[4:8] == [-3.0, -3.0, -3.0, -3.0]
    assert m.valid_mask([500, 500], [6, 9], accept_semi_valid=False) == [False, True]
    with pytest.raises(ValueError):
        m.features([1, 2, 3], [5, 5, 5], None)


def test_forward_is_relu_then_softmax_and_fusion_is_log_sum():
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
    # 융합: log 합 = 곱에 비례. 한쪽이 확신하면 다른 쪽이 균등해도 그쪽을 따른다
    sure = [0.1, 0.8, 0.1]
    flat = [1 / 3] * 3
    assert fuse_log_softmax([sure, flat]) == pytest.approx(sure)
    assert fuse_log_softmax([sure]) == pytest.approx(sure)
    both = fuse_log_softmax([sure, sure])
    assert both[1] > sure[1] and sum(both) == pytest.approx(1.0)


def test_bad_specs_are_rejected():
    with pytest.raises(ValueError):
        TofMlpModel(dict(TOY, format="other"))
    with pytest.raises(ValueError):
        TofMlpModel(dict(TOY, b1=[0.0]))
    with pytest.raises(ValueError):
        TofMlpModel(dict(TOY, b2=[0.0, float("nan"), 0.0]))


def test_single_sensor_waits_for_baseline_then_votes():
    p = posture()
    for i in range(3):
        v = p.update(sample(STILL), i * 0.2)
        assert v["posture"] == "unknown" and not v["baseline_ready"] and v["present"] and v["sensors_used"] == 0
    v = p.update(sample(STILL), 0.6)              # 4번째 조용한 프레임 → 기준선 확정, 바로 판정
    assert v["baseline_ready"] and v["posture"] == "upright" and v["sensors_used"] == 1
    assert p.sensors[0].baseline == [600.0, 700.0, 650.0, 620.0]
    moved = [900, 700, 650, 620]                  # zone 0 이 300 mm 멀어짐 → recline
    v = p.update(sample(moved), 0.8)
    assert v["raw_posture"] == "recline" and v["posture"] == "upright"      # 3표 중 1표 → 아직 다수 아님
    v = p.update(sample(moved), 1.0)
    assert v["posture"] == "recline" and v["source"] == "mlp"              # 2/3
    # 빈 자리가 away_reset_sec 이어지면 기준선을 버린다
    p.away_reset_sec = 1.0
    assert p.update(sample(FAR), 2.0)["baseline_ready"]
    v = p.update(sample(FAR), 3.5)
    assert not v["baseline_ready"] and v["posture"] == "unknown" and not v["present"]


def test_two_sensors_are_fused_and_one_dead_sensor_is_fine():
    p = posture()
    for i in range(4):
        v = p.update(sample(STILL, STILL), i * 0.2)
    assert v["baseline_ready"] and v["sensors_used"] == 2 and v["posture"] == "upright"
    # 센서 0 만 멀어짐(확신 recline), 센서 1 은 그대로(균등) → 융합 결과 recline
    moved = [900, 700, 650, 620]
    for t in (1.0, 1.2):
        v = p.update(sample(moved, STILL), t)
    assert v["posture"] == "recline" and v["sensors_used"] == 2
    # 센서 1 이 죽어 표본에 센서 0 만 와도 계속 판정한다(기준선은 센서별로 남아 있다)
    v = p.update(sample(moved), 1.4)
    assert v["posture"] == "recline" and v["sensors_used"] == 1
    # 서로 반대로 보면 log 합이 동률 — 융합 자체는 둘 중 하나를 내고 다수결이 튀는 것을 막는다
    closer = [300, 700, 650, 620]
    v = p.update(sample(moved, closer), 1.6)
    assert v["raw_posture"] in ("recline", "lean_forward") and v["sensors_used"] == 2 and v["posture"] == "recline"


def test_baseline_waits_while_moving_and_reset_clears_all_sensors():
    p = posture(baseline_max_moving_frac=0.2)
    frames = [[600, 700, 650, 620], [700, 700, 650, 620], [600, 700, 650, 620], [700, 700, 650, 620]]
    for i, d in enumerate(frames):
        assert p.update(sample(d, d), i * 0.2)["posture"] == "unknown"
    assert not p.baseline_ready                   # 움직인 프레임 3/4 → 기준선 보류
    for i in range(4):
        assert not p.update(sample(frames[0], frames[0]), 1.0 + i * 0.2)["baseline_ready"]
    assert p.update(sample(frames[0], frames[0]), 2.0)["baseline_ready"]
    p.reset_baseline()
    assert not p.baseline_ready and p.update(sample(frames[0], frames[0]), 5.0)["posture"] == "unknown"


def test_grid_shape_mismatch_is_reported_not_raised():
    p = posture(baseline_frames=1)
    v = p.update(sample([600] * 6, w=3, h=2), 0.0)
    assert v["posture"] == "unknown" and "3x2" in v["error"] and p.last_error
    v = p.update({"grid_width": 2, "grid_height": 2, "sensors": {}}, 0.1)
    assert v["posture"] == "unknown" and "no sensor" in v["error"]


def test_packaged_sim_weights_load_and_run_through_uart_path():
    """config/tof_mlp.json (export_mlp_json.py 산출물) 을 보드처럼 pkgutil 로 읽고 센서 두 개의 0x05 → 융합 판정까지 돈다."""
    m = load_model()
    assert (m.n_in, m.n_out) == (192, 10) and (m.grid_width, m.grid_height) == (8, 8)
    assert "away" in m.classes and "drowsy" not in m.classes            # 졸음은 모델 밖(시간 현상)
    assert m.source.endswith("mlp_sim60.npz")
    cfg = load_ingest_config()["tof"]
    post = load_posture_from_config(dict(cfg, mlp=dict(cfg["mlp"], baseline_frames=5, vote_frames=5)))
    assert isinstance(post, TofMlpPosture) and post.vote_frames == 5
    reader = RawUartReader(SensorCache(), posture=post)
    # 머리(위 2행)만 빈 벽, 아래 6행이 650 mm 인 앉은 사람 흉내. 두 센서가 거의 같은 장면을 본다
    dist = [1500 if r < 2 else 650 for r in range(8) for _ in range(8)]
    status = [5] * 64
    kinds = []
    for i in range(8):
        for payload in encode_tof_sensor_frames({0: (dist, status), 1: (dist, status)}, frame_id=i, flags=2):
            kinds += reader.feed_bytes(encode_frame(Frame(TYPE_TOF_GRID, 2 * i, i * 200, payload)))
    assert kinds == ["tof_grid_part", "tof_grid"] * 8
    verdict = reader.cache.snapshot().tof_mlp.data
    assert verdict["baseline_ready"] and verdict["sensors_used"] == 2
    assert verdict["posture"] in m.classes and 0.0 <= verdict["posture_confidence"] <= 1.0
    grid = reader.cache.snapshot().tof_grid.data
    assert grid["sensors_ok"] == 3 and sorted(grid["sensors"]) == [0, 1] and grid["flags"] == 2


def test_missing_or_disabled_weights_fall_back_to_rule(tmp_path):
    logs = []
    assert load_posture_from_config({"mlp": {"enabled": False}}) is None
    assert load_posture_from_config({}) is None
    assert load_posture_from_config({"mlp": {"weights": str(tmp_path / "none.json")}}, log=logs.append) is None
    assert logs and "폴백" in logs[0]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "x"}), encoding="utf-8")
    assert load_posture_from_config({"mlp": {"weights": str(bad)}}, log=logs.append) is None


def test_mmwave_absent_gates_model_and_baseline():
    """mmWave 가 '없음' 이면 격자에 무엇이 보여도(빈 의자·지나가는 사람) 모델을 돌리지 않고 기준선도 모으지 않는다."""
    p = posture()
    for i in range(6):
        v = p.update(sample(STILL), i * 0.2, False)
        assert v["posture"] == "unknown" and not v["present"] and v["gated"] == "mmwave_absent"
    assert p.sensors[0].baseline is None
    for i in range(4):                             # mmWave 가 사람을 보면 그때부터 기준선을 모은다
        v = p.update(sample(STILL), 2.0 + i * 0.2, True)
    assert v["baseline_ready"] and v["posture"] == "upright" and "gated" not in v
    p.away_reset_sec = 1.0                         # 오래 비면 기준선 폐기 — mmWave 부재로도 같은 규칙
    p.update(sample(STILL), 3.0, False)
    v = p.update(sample(STILL), 4.5, False)
    assert p.sensors[0].baseline is None and not v["baseline_ready"]


def test_uart_path_asks_mmwave_presence_before_posture():
    """보드 경로: 0x05 표본이 완성될 때마다 mmWave 재실을 묻고, 없으면 모델·기준선을 건너뛴다. mmWave 가 없거나 낡으면 묻지 않는다."""
    from deskmate_hub.ingest.mapping import mmwave_presence_hint

    icfg = load_ingest_config()
    cfg = icfg["tof"]
    post = load_posture_from_config(dict(cfg, mlp=dict(cfg["mlp"], baseline_frames=5, vote_frames=5)))
    cache = SensorCache()
    reader = RawUartReader(cache, posture=post, presence=mmwave_presence_hint(cache, icfg))
    dist = [1500 if r < 2 else 650 for r in range(8) for _ in range(8)]
    status = [5] * 64

    def feed(i):
        for payload in encode_tof_sensor_frames({0: (dist, status), 1: (dist, status)}, frame_id=i, flags=2):
            reader.feed_bytes(encode_frame(Frame(TYPE_TOF_GRID, 2 * i, i * 200, payload)))
        return cache.snapshot().tof_mlp.data

    now = time.time()                               # RawUartReader 는 수신 시각을 벽시계로 찍는다
    cache.put("mmwave", Sample(received=now, ts=now, seq=None, data={"present": False}))
    for i in range(8):                              # ToF 엔 사람 모양이 보여도 mmWave 가 없다고 하면 게이트
        v = feed(i)
    assert v["gated"] == "mmwave_absent" and v["posture"] == "unknown" and not v["present"]
    assert all(s.baseline is None for s in post.sensors.values())
    hint = mmwave_presence_hint(cache, icfg)
    cache.put("mmwave", Sample(received=10.0, ts=10.0, seq=None, data={"present": True, "distance_cm": 60}))
    assert hint(10.5) is True and hint(10.0 + icfg["freshness_sec"]["mmwave"] + 1) is None   # 낡으면 모름
    cache.put("mmwave", Sample(received=11.0, ts=11.0, seq=None, data={"present": True, "distance_cm": 320}))
    assert hint(11.5) is False                       # 방 안 다른 자리(침대·주방) → 책상 빔 → 게이트
    cache.put("mmwave", Sample(received=12.0, ts=12.0, seq=None, data={"present": True}))
    assert hint(12.5) is None                        # 거리 없음 → 격자에 맡긴다
    assert mmwave_presence_hint(cache, dict(icfg, presence={"gate_posture": False}))(10.5) is None
