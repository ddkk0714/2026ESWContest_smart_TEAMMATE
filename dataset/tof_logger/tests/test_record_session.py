"""가짜 L8 → 수집 → 세션 읽기 전 구간."""
import json

import numpy as np
import pytest

from tofl.fake import FakeSource, render, scene_to_sensor
from tofl.protocol import Schedule, Step
from tofl.recorder import record
from tofl.session import SessionWriter, export_npz, load_session

T0 = 1_760_000_000.0


def _run(tmp_path, steps, transition=1.0, drop_every=0, flip_rows=False, flip_cols=True):
    sch = Schedule(steps, transition)
    first = T0 + 0.5

    def posture_at(t):
        now = sch.at(t - first)
        return now.prompt if now else None

    src = FakeSource(posture_at, realtime=False, t0=T0, drop_every=drop_every,
                     flip_rows=flip_rows, flip_cols=flip_cols, max_frames=10_000)
    w = SessionWriter("tester", "fake", {"source": "fake"}, base=tmp_path, stamp="20261012_000000")
    st = record(src, w, schedule=sch, send=src.send)
    w.close(end_reason="done")
    return w.folder, src, st


def test_protocol_session_roundtrip(tmp_path):
    folder, src, st = _run(tmp_path, [Step("away", 2, "비켜", "g"), Step("upright", 3, "바르게", "b"),
                                      Step("chin_rest", 3, "턱")])
    meta, data = load_session(folder)
    n = len(data["t_host"])
    assert n == st.frames and 15 * 10 <= n <= 15 * 11  # 1+2+1+3+1+3 = 11 s, 끝 프레임 하나 오차
    assert meta["device"]["sensor"] == "FAKE-L8"
    assert meta["stats"]["seq_gaps"] == 0
    assert src.sent == ["g", "b"]  # 단계 시작마다 한 번씩
    events = [json.loads(x) for x in open(folder / "events.jsonl", encoding="utf-8")]
    assert [e["cmd"] for e in events if e["kind"] == "cmd"] == ["g", "b"]
    # 전환 구간은 정답 없음, 단계 구간은 그 라벨
    assert set(data["prompt"][data["phase"] == "transition"]) == {None}
    assert set(data["prompt"][data["phase"] == "step"]) == {"away", "upright", "chin_rest"}
    assert data["dist"].shape == (n, 8, 8)
    # 부팅 로그 줄은 버려졌고 hello 는 이벤트로 남았다
    assert any(e["kind"] == "tof_hello" for e in events)


def _nearest_zone(dist):
    with np.errstate(all="ignore"):
        m = np.nanmedian(dist, axis=0)
    return np.unravel_index(np.nanargmin(m), m.shape)


def test_orientation_restored_from_hello(tmp_path):
    # 센서가 위아래·좌우 모두 뒤집혀 장착된 경우. hello 의 flip 값으로 장면 방향을 되찾는다.
    folder, _, _ = _run(tmp_path, [Step("lean_left", 2, "왼쪽")], flip_rows=True, flip_cols=True)
    _, data = load_session(folder)
    sel = data["prompt"] == "lean_left"
    r, c = _nearest_zone(data["dist"][sel])
    assert r < 4 and c < 4  # 머리(가장 가까움)는 위쪽·왼쪽
    _, wrong = load_session(folder, flip_rows=False, flip_cols=False)
    r, c = _nearest_zone(wrong["dist"][sel])
    assert r >= 4 and c >= 4


def test_seq_gaps_counted(tmp_path):
    folder, _, _ = _run(tmp_path, [Step("upright", 3, "바르게")], drop_every=10)
    meta, data = load_session(folder)
    assert meta["stats"]["seq_gaps"] > 0
    assert meta["stats"]["missing_frames"] == meta["stats"]["seq_gaps"]
    assert np.all(np.diff(data["seq"]) >= 1)


def test_invalid_zones_become_nan(tmp_path):
    folder, _, _ = _run(tmp_path, [Step("upright", 2, "바르게")])
    _, data = load_session(folder)
    invalid = (data["raw_d"] < 0) | ~np.isin(data["status"], (5, 6, 9, 10))
    assert invalid.any()
    assert np.isnan(data["dist"][invalid]).all() and np.isfinite(data["dist"][~invalid]).all()
    _, strict = load_session(folder, accept_semi_valid=False)
    assert np.isnan(strict["dist"][data["status"] == 6]).all()


def test_export_npz(tmp_path):
    folder, _, _ = _run(tmp_path, [Step("upright", 2, "바르게")])
    out = export_npz(folder, tmp_path / "s.npz")
    z = np.load(out, allow_pickle=False)
    assert z["dist"].shape[1:] == (8, 8) and z["prompt"].dtype.kind == "U"
    assert json.loads(str(z["meta"]))["user"] == "tester"


def test_scene_to_sensor_is_its_own_inverse():
    d, _ = render("chin_rest", rng=np.random.default_rng(1))
    back = scene_to_sensor(scene_to_sensor(d, True, True), True, True)
    assert np.array_equal(back, d)


def test_writer_rejects_bad_user(tmp_path):
    with pytest.raises(ValueError):
        SessionWriter("../x", "fake", {}, base=tmp_path)
