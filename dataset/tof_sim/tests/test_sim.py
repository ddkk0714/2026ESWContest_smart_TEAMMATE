"""시뮬레이터 구성 요소. 숫자의 '정답'이 아니라 구조(방향·IK·센서 모델·형식)를 붙잡는다."""
import json
import shutil

import mujoco
import numpy as np
import pytest

from tofsim.l8 import L8, L8Params, Mount
from tofsim.motion import StepSpec, timeline
from tofsim.pose import _site, sample_pose
from tofsim.scene import Room, build_scene


@pytest.fixture(scope="module")
def sc():
    return build_scene(r=Room(wall=1.8))


def test_empty_seat_sees_wall_and_chair(sc):
    sc.set_q({})
    d, st = L8(Mount(z=sc.room.desk_h + 0.15, pitch=10)).measure(sc, False, np.random.default_rng(0))
    assert d.shape == (8, 8) and st.shape == (8, 8)
    assert (d[0] > 1500).all()          # 위쪽은 벽(1.8 m)
    assert np.isin(st, (5, 6, 255)).all()


def test_person_appears_top_center_and_closer(sc):
    sc.set_q(sample_pose(sc, "upright", np.random.default_rng(1)))
    l8 = L8(Mount(z=sc.room.desk_h + 0.15, pitch=10))
    d, _ = l8.measure(sc, True, np.random.default_rng(0))
    empty, _ = l8.measure(sc, False, np.random.default_rng(0))
    head_cols = d[1, 3:5]
    assert (head_cols < 900).all() and (empty[1, 3:5] > 1500).all()
    assert d[1, 0] > 1500  # 머리 옆 위쪽 구석은 여전히 벽


def test_left_hand_chin_rest_shows_on_sensor_right(sc):
    l8 = L8(Mount(z=sc.room.desk_h + 0.15, pitch=10), L8Params(dropout=0.0))
    cols = {}
    for side in ("left", "right"):
        q = sample_pose(sc, "chin_rest", np.random.default_rng(3), chin_side=side)
        sc.set_q(q)
        assert np.linalg.norm(_site(sc, "hand_L" if side == "left" else "hand_R") - _site(sc, "chin")) < 0.08
        d, _ = l8.measure(sc, True, np.random.default_rng(0))
        cols[side] = d[4:, :].astype(float)
    # 사람 왼쪽 = 센서가 볼 때 오른쪽: 왼손이면 오른쪽 절반이 상대적으로 더 가깝다
    diff_left = cols["left"][:, 4:].mean() - cols["left"][:, :4].mean()
    diff_right = cols["right"][:, 4:].mean() - cols["right"][:, :4].mean()
    assert diff_left < diff_right


def test_face_down_head_reaches_desk(sc):
    sample_pose(sc, "face_down", np.random.default_rng(0))
    assert _site(sc, "head_c")[2] < sc.room.desk_h + 0.25


def test_mixed_zone_reports_closest_target():
    """머리 가장자리처럼 사람(가까움)과 먼 벽이 섞인 칸은 평균이 아니라 가까운 쪽을 낸다."""
    scene = build_scene(r=Room(wall=2.5))
    scene.set_q(sample_pose(scene, "upright", np.random.default_rng(1)))
    l8 = L8(Mount(z=scene.room.desk_h + 0.15, pitch=10), L8Params(dropout=0.0, sigma0_mm=0, sigma1_mm=0))
    dist, sig, gid = l8.cast(scene, True)
    d, _ = l8.measure(scene, True, np.random.default_rng(0))
    k = l8.p.sub ** 2
    for z in range(64):
        rd = dist[z * k:(z + 1) * k]
        near, far = rd[(rd > 0) & (rd < 1.2)], rd[rd > 2.0]
        if len(near) >= 0.3 * k and len(far):  # 가장자리를 살짝 스친 칸은 신호가 약해 벽이 맞다
            assert d.ravel()[z] < 1300
            return
    pytest.skip("섞인 칸이 없는 배치")


def test_dark_far_target_disappears():
    scene = build_scene(r=Room(wall=3.9, wall_refl=0.03))
    scene.set_q({})
    d, st = L8(Mount(z=scene.room.desk_h + 0.15, pitch=10), L8Params(dropout=0.0)).measure(
        scene, False, np.random.default_rng(0))
    assert (st[0] == 255).all() and (d[0] == -1).all()


def test_timeline_labels_and_nods(sc):
    steps = [StepSpec("upright", 4), StepSpec("drowsy", 20), StepSpec("away", 3)]
    fr = timeline(sc, steps, 2.0, np.random.default_rng(5), fps=15)
    assert len(fr) == pytest.approx(15 * (6 + 22 + 5), abs=2)
    assert {f.prompt for f in fr if f.phase == "step"} == {"upright", "drowsy", "away"}
    assert not any(f.present for f in fr if f.prompt == "away")
    assert any(f.nodding for f in fr) and not any(f.nodding for f in fr if f.prompt == "upright")


def test_generate_session_roundtrip(tmp_path):
    import yaml

    from generate import generate_one
    from tofl.session import load_session

    cfg = tmp_path / "p.yaml"
    cfg.write_text(yaml.safe_dump({"protocol": {"transition_s": 1, "steps": [
        {"label": "away", "seconds": 2, "text": "x", "cmd": "g"},
        {"label": "upright", "seconds": 3, "text": "x", "cmd": "b"},
        {"label": "chin_rest", "seconds": 2, "text": "x", "sim": "left"}]}}), encoding="utf-8")
    with_rule = shutil.which("g++") is not None or shutil.which("wsl.exe") is not None
    folder = generate_one(9, tmp_path / "s", {}, 3, with_rule, str(cfg))
    meta, data = load_session(folder)
    assert meta["device"]["sensor"] == "SIM-L8" and meta["sim"]["mount"]["pitch"] >= 0
    assert data["dist"].shape == (len(data["t_host"]), 8, 8)
    assert set(data["prompt"][data["phase"] == "step"]) == {"away", "upright", "chin_rest"}
    events = [json.loads(x) for x in open(folder / "events.jsonl", encoding="utf-8")]
    assert [e["cmd"] for e in events if e["kind"] == "cmd"] == ["g", "b"]
    if with_rule:
        assert set(data["rule_posture"]) <= {"upright", "lean_forward", "recline", "face_down",
                                             "chin_rest", "drowsy", "away", "unknown"}
