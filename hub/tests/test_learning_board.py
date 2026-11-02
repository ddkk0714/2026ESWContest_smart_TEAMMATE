"""반응 학습(learning/reaction.py)의 Pi 4 보드 경로 — LiveHub 연결(fatigue/label 큐 공유), 상태 저장·복구, 공통 모델 출발,
보드 지역시, 원본(9b0720b)과 같은 숫자."""
from __future__ import annotations

import io
import json
import os
import time

import pytest

from deskmate_hub.inference import SensorFrame, Signal, State
from deskmate_hub.inference.types import Scores
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.learning import ReactionLearner, latest_app_labels, load_learning_config
from deskmate_hub.live import LiveHub

CFG = {"enabled": True, "n_min": 4, "n_full": 8, "alpha_max": 0.8, "acc_scale": 0.2, "up_max": 0.25, "down_max": 0.25,
       "keep_floor": 0.85, "probe_band": 0.25, "probe_gap_s": 900, "probe_max": 6, "probe_expires_s": 120}


def frame(t, *, present=True, typing=True, posture=0.2):
    sig = {"posture": Signal(phi=0.1, delta=posture), "elapsed": Signal(delta=0.3)}
    if typing:
        sig["keystroke"] = Signal(phi=0.2, delta=0.2)
    return SensorFrame(now=t, present=present, pc_ratio=0.9, signals=sig)


def scores(c):
    return Scores(c_focus=0.1, c_fatigue=c, focus_weights={}, fatigue_weights={})


def hub(cfg, **kw):
    return LiveHub(SensorCache(), ingest_cfg=load_ingest_config(), out=io.StringIO(), learning_cfg=cfg, **kw)


def label_msg(ts, kss=8, event="kss", prompt_ts=None):
    data = {"event": event, "session_id": "A-1", "profile_id": "A", "ts": ts, "prompt_ts": ts - 8 if prompt_ts is None else prompt_ts}
    if kss is not None:
        data["kss"] = kss
    return json.dumps({"schema_version": "1.0", "ts": ts, "node": "display-fatigue", "seq": 1, "data": data}).encode()


# ── LiveHub 연결 ───────────────────────────────────────────────────────────
def test_disabled_keeps_fsm_untouched():
    h = hub({"enabled": False})
    assert h.learner is None and h.engine.score_adjust is None


def test_live_hub_passes_app_answer_to_learner():
    """fatigue/label 은 피로 점수(fatigue/live.py)와 같은 큐(cache.fatigue_labels)로 들어오고 반응 학습도 같은 기록을 쓴다."""
    h = hub(dict(CFG))
    h.tick_once(1000.0)
    assert route_mqtt_message(h.cache, "deskmate/fatigue/label", label_msg(1005.0, 8), 1006.0) == "fatigue_label"
    h.tick_once(1010.0)
    h.tick_once(1020.0)
    assert h.learner.counts["labels_kss"] == 1 and h.learner.counts["pos"] == 1
    assert h.tracker.self_kss == (8.0, 1005.0)
    env = h.tick_once(1030.0)
    assert env["data"]["sensor_summary"]["learning"]["n_labels"] == 1


def test_resent_old_answers_do_not_label_the_current_tick():
    """앱은 끊긴 동안의 기록을 다시 보낸다 — 한 tick 에 여럿이 오면 가장 새 것 하나만, label_max_age_s 보다 옛 것은 버린다."""
    h = hub(dict(CFG))
    h.tick_once(5000.0)
    for ts in (1000.0, 2000.0, 3000.0):                         # 전부 900 s 보다 옛 응답
        route_mqtt_message(h.cache, "deskmate/fatigue/label", label_msg(ts, 9), 5001.0)
    h.tick_once(5010.0)
    assert h.learner.counts["labels_kss"] == 0
    for ts in (4800.0, 4990.0):
        route_mqtt_message(h.cache, "deskmate/fatigue/label", label_msg(ts, 3 if ts > 4900 else 9), 5011.0)
    h.tick_once(5020.0)
    assert h.learner.counts["labels_kss"] == 1 and h.learner.counts["neg"] == 1   # 4990 의 KSS 3 만


def test_latest_app_labels_keeps_contract_only():
    ev = [{"event": "kss", "kss": 7, "ts": 10.0}, {"event": "kss", "kss": 5, "ts": 20.0}, {"event": "kss", "kss": True, "ts": 30.0},
          {"event": "kss", "kss": 0, "ts": 40.0}, {"event": "prompt_missed", "prompt_ts": 15.0, "ts": 25.0},
          {"event": "prompt_missed", "prompt_ts": "x"}, {"event": "session_end", "kss": 9, "ts": 50.0}, "junk"]
    assert latest_app_labels(ev) == ((5.0, 20.0), 15.0)
    assert latest_app_labels([]) == (None, None)


def test_checkin_card_answer_goes_to_learner_not_fsm():
    h = hub(dict(CFG))
    h.learner.adjust(scores(0.4), frame(1000.0))
    res = type("R", (), {"state": State.FOCUS_PC})()
    h.learner.maybe_checkin(res, frame(1000.0), 1000.0, busy=False, request_id="b-1-checkin")
    h.cache.put_feedback({"request_id": "b-1-checkin", "verdict": "accept"}, received=1001.0)
    h.tick_once(1002.0)
    assert h.learner.counts["probe_yes"] == 1 and h.tracker.pending_feedback is None


# ── 상태 저장·복구 ──────────────────────────────────────────────────────────
def test_state_path_is_per_profile_and_survives_restart(tmp_path):
    cfg = dict(CFG, state_path=str(tmp_path / "profiles" / "{profile_id}" / "reaction.json"), profile_id="A")
    a = ReactionLearner(cfg)
    assert a.state_path == str(tmp_path / "profiles" / "A" / "reaction.json")
    x = a.features(frame(1000.0), 0.3, 0.1)
    for i in range(5):
        a._label(x, 1, 0.3, 1000.0 + i, "kss")
    assert os.path.exists(a.state_path) and not os.path.exists(a.state_path + ".tmp")
    b = ReactionLearner(cfg)                                   # 서비스 재시작
    assert b.n_labels == 5 and b.model.w == a.model.w and b.started_from == "state"


@pytest.mark.parametrize("body", ["{not json", "[1, 2]", json.dumps({"model": {"n": 26, "w": [0.1], "g2": [1.0]}}),
                                  json.dumps({"model": {"n": 26, "w": [0.0] * 27, "g2": [1.0] * 27}, "hist": [[1, 2]]})])
def test_corrupt_state_starts_fresh_with_a_log_line(tmp_path, body):
    path = tmp_path / "reaction.json"
    path.write_text(body, encoding="utf-8")
    logs: list[str] = []
    lr = ReactionLearner(dict(CFG, state_path=str(path)), on_log=logs.append)
    assert lr.n_labels == 0 and lr.model.w == [0.0] * 27 and not lr.hist and lr.started_from == "fsm"
    assert len(logs) == 1 and "FSM 에서 다시 시작" in logs[0]
    lr._label(lr.features(frame(1000.0), 0.3, 0.1), 1, 0.3, 1000.0, "kss")    # 다음 라벨에서 새 상태로 덮어쓴다
    assert json.loads(path.read_text(encoding="utf-8"))["n_labels"] == 1


def test_save_failure_is_logged_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    logs: list[str] = []
    lr = ReactionLearner(dict(CFG, state_path=str(blocker / "sub" / "reaction.json")), on_log=logs.append)
    lr._label(lr.features(frame(1000.0), 0.3, 0.1), 1, 0.3, 1000.0, "kss")
    assert lr.n_labels == 1 and any("저장 실패" in m for m in logs)


# ── 공통 모델 출발 ──────────────────────────────────────────────────────────
def _common_file(path, names, w=0.5):
    n = len(names)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": {"n": n, "w": [w] * (n + 1), "g2": [1.0] * (n + 1)}, "n_labels": 0, "hist": [],
                                "features": names, "common": True, "common_from": 3}), encoding="utf-8")


def test_common_model_is_copied_as_the_first_state(tmp_path):
    names = ReactionLearner.feature_names()
    common = tmp_path / "learning" / "learner_common_v1.json"
    _common_file(common, names)
    state = tmp_path / "profiles" / "default" / "reaction.json"
    cfg = dict(CFG, state_path=str(tmp_path / "profiles" / "{profile_id}" / "reaction.json"), common_path=str(common))
    logs: list[str] = []
    lr = ReactionLearner(cfg, on_log=logs.append)
    assert lr.common and lr.started_from == "common" and lr.model.w == [0.5] * (len(names) + 1)
    assert lr.alpha() == pytest.approx(float(cfg.get("common_alpha", 0.3)))      # 라벨 전에도 common_alpha 만큼
    assert any("공통 모델 출발" in m for m in logs)
    assert json.loads(state.read_text(encoding="utf-8"))["common"] is True       # 이 사람의 첫 상태로 복사됨
    common.unlink()                                                              # 재시작: 이제 제 상태 파일에서 이어 간다
    again = ReactionLearner(cfg)
    assert again.started_from == "state" and again.common and again.model.w == lr.model.w


def test_missing_common_model_starts_from_fsm(tmp_path):
    logs: list[str] = []
    lr = ReactionLearner(dict(CFG, state_path=str(tmp_path / "s.json"), common_path=str(tmp_path / "none.json")),
                         on_log=logs.append)
    assert not lr.common and lr.alpha() == 0.0 and lr.started_from == "fsm"
    assert any("공통 모델 파일 없음" in m for m in logs)
    assert not (tmp_path / "s.json").exists()


def test_common_model_with_other_feature_order_is_ignored(tmp_path):
    """개수는 같고 순서만 다른 공통 모델 — 틀린 가중치에 common_alpha 가 붙지 않게 무시하고 FSM 에서 출발(로그 한 줄)."""
    names = ReactionLearner.feature_names()
    common = tmp_path / "c.json"
    _common_file(common, list(reversed(names)))
    logs: list[str] = []
    lr = ReactionLearner(dict(CFG, state_path=str(tmp_path / "s.json"), common_path=str(common)), on_log=logs.append)
    assert not lr.common and lr.model.w == [0.0] * (len(names) + 1) and lr.alpha() == 0.0
    assert len(logs) == 1 and "특징 이름·순서" in logs[0] and "#0" in logs[0]


# ── 보드 지역시·설정 ────────────────────────────────────────────────────────
def test_hour_features_use_configured_utc_offset():
    lr = ReactionLearner(dict(CFG, local_utc_offset_hours=9))
    names = lr.feature_names()
    t = 1_792_908_000.0                                         # 2026-10-25 UTC 06:00 → KST 15:00
    x = lr.features(frame(t), 0.3, 0.1)
    import math
    h = 15.0 / 24.0 * 2 * math.pi
    assert x[names.index("hour_sin")] == pytest.approx(math.sin(h)) and x[names.index("hour_cos")] == pytest.approx(math.cos(h))


def test_board_config_is_enabled_with_the_fence():
    """보드 배포판 learning.yaml: 켬, 울타리(FSM 이 바깥 틀) 안에서만 — 낮추는 폭이 올리는 폭보다 좁고 강한 증거는 못 낮춘다."""
    c = load_learning_config()
    assert c["enabled"] is True and c["residual"] is True
    assert float(c["down_max"]) == 0.05 and float(c["up_max"]) == 0.25 and float(c["keep_floor"]) == 0.85
    assert float(c["pos_weight"]) == 2.0 and float(c["effect_weight"]) == 0.0
    assert "{profile_id}" in c["state_path"] and c["state_path"].startswith("logs/")
    assert c["common_path"].startswith("logs/")


def test_board_reads_learning_json_without_yaml(monkeypatch):
    """보드 payload 의 config/learning.json(build_payload.py 가 만듦)을 PyYAML 없이 읽는다."""
    import pkgutil
    real = pkgutil.get_data
    monkeypatch.setattr(pkgutil, "get_data", lambda pkg, res: b'{"enabled": true, "down_max": 0.05}'
                        if res == "config/learning.json" else real(pkg, res))
    assert load_learning_config() == {"enabled": True, "down_max": 0.05}


# ── 원본(9b0720b, 시뮬)과 같은 숫자 ───────────────────────────────────────────
# 9b0720b 의 reaction.py 로 아래 입력을 돌린 값(time.localtime = gmtime). 보드 이식이 학습·보정 수식을 바꾸지 않았는지 본다.
GOLD_C = [0.3, 0.32999999999999996, 0.36, 0.39, 0.42, 0.44999999999999996, 0.4827488895195756, 0.6044691629861878,
          0.5164825755364627, 0.7958674302316364, 0.5499999999999999, 0.8286002542393873, 0.6099999999999999,
          0.8523006811368513, 0.6699999999999999, 0.8723966841860948]
GOLD_W = [-1.3830125074268662, -1.3888426252479744, -1.4027812972108913, 0.3386026164114783, 0.139871891094437,
          -0.0057205307303261205, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.19772687625196586, 0.0, -0.0057205307303261205,
          -0.0057205307303261205, -0.021214185634186727, -0.03743564434608041, -0.006015829984667216,
          -0.010923457174793413, 0.022803565826783022, -0.0057205307303261205, 0.6813847815011029, 0.6819925129242145,
          0.20092811047344045, -0.0057205307303261205, -0.005669562824477021]


def test_same_numbers_as_original_sim_learner(monkeypatch):
    monkeypatch.setattr(time, "localtime", time.gmtime)
    lr = ReactionLearner({"enabled": True, "n_min": 4, "n_full": 8, "pos_weight": 2.0, "down_max": 0.05})
    out = []
    t = 1_792_900_000.0
    for i in range(16):
        sig = {"posture": Signal(phi=0.1 * (i % 3), delta=0.9 if i % 2 else 0.1), "elapsed": Signal(delta=0.02 * i)}
        if i % 2 == 0:
            sig["keystroke"] = Signal(phi=0.2, delta=0.15)
        f = SensorFrame(now=t, present=True, pc_ratio=0.5 + 0.02 * i, signals=sig,
                        hr_drop_bpm=(6.0 if i % 2 else None), mm_motion=3.0 * i)
        out.append(lr.adjust(scores(0.3 + 0.03 * i), f).c_fatigue)
        lr.on_self_report(8 if i % 2 else 3, t - 5.0, t)
        t += 450.0
    assert max(abs(a - b) for a, b in zip(out, GOLD_C)) < 1e-9
    assert max(abs(a - b) for a, b in zip(lr.model.w, GOLD_W)) < 1e-9 and len(lr.model.w) == len(GOLD_W)


def test_cold_start_enabled_hub_matches_fsm_only_hub():
    """보드 기본(켬)이라도 라벨이 쌓이기 전(α = 0)에는 FSM 만 쓴 hub 와 상태·점수가 똑같다 — 켜는 것만으로 FSM 동작이 바뀌지 않는다."""
    from deskmate_hub.ingest import Sample

    def run(cfg):
        cache = SensorCache()
        h = LiveHub(cache, ingest_cfg=load_ingest_config(), out=io.StringIO(), learning_cfg=cfg)
        out = []
        for i in range(90):
            t = 1000.0 + 10.0 * i
            cache.put("mmwave", Sample(received=t, ts=t, seq=None, data={
                "present": True, "motion_state": "still", "motion_level": 2 if i > 30 else 40, "distance_cm": 55,
                "resp_bpm": 15, "resp_valid": True, "heart_bpm": None, "heart_valid": False,
                "drowsy_state": "DROWSY" if i > 50 else "AWAKE", "valid": True}))
            env = h.tick_once(t)
            out.append((env["data"]["fsm_state"], env["data"]["c_fatigue"], env["data"]["gate"]))
        return out, h

    base, _ = run({"enabled": False})
    on, h = run(dict(load_learning_config(), state_path=None, common_path=None))
    assert h.learner is not None and h.learner.alpha() == 0.0
    assert on == base and len({s for s, _, _ in base}) > 1, sorted({s for s, _, _ in base})
