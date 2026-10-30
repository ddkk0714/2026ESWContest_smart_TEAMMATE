"""사용자 반응으로 배우는 피로 판단(learning/reaction.py) — FSM 으로 시작하고, 반응 라벨로 배운 모델이 울타리 안에서 넘겨받는다."""
from __future__ import annotations

import pytest

from deskmate_hub.inference import FSMEngine, SensorFrame, Signal, State
from deskmate_hub.inference.types import Scores
from deskmate_hub.learning import OnlineLogistic, ReactionLearner

CFG = {"enabled": True, "n_min": 4, "n_full": 8, "alpha_max": 0.8, "acc_scale": 0.2, "up_max": 0.25, "down_max": 0.25,
       "keep_floor": 0.85, "probe_band": 0.25, "probe_gap_s": 900, "probe_max": 6, "probe_expires_s": 120}


def frame(t, *, present=True, typing=True, posture=0.2):
    sig = {"posture": Signal(phi=0.1, delta=posture), "elapsed": Signal(delta=0.3)}
    if typing:
        sig["keystroke"] = Signal(phi=0.2, delta=0.2)
    return SensorFrame(now=t, present=present, pc_ratio=0.9, signals=sig)


def scores(c):
    return Scores(c_focus=0.1, c_fatigue=c, focus_weights={}, fatigue_weights={})


def test_online_logistic_learns_a_separable_rule():
    m = OnlineLogistic(2)
    for _ in range(300):
        m.update([1.0, 0.0], 1.0)
        m.update([0.0, 1.0], 0.0)
    assert m.predict([1.0, 0.0]) > 0.9 and m.predict([0.0, 1.0]) < 0.1


def test_cold_start_is_pure_fsm():
    lr = ReactionLearner(CFG)
    out = lr.adjust(scores(0.42), frame(1000.0))
    assert lr.alpha() == 0.0 and out.c_fatigue == 0.42


def test_alpha_grows_with_labels_and_fence_clips_the_change():
    lr = ReactionLearner(CFG)
    x = lr.features(frame(1000.0), 0.3, 0.1)
    for i in range(12):                        # 모델은 맞히고 FSM(c_fsm 0.3 → '피로 아님')은 틀리는 라벨
        lr._label(x, 1, 0.3, 1000.0 + i, "probe")
    assert lr.alpha() > 0.5
    out = lr.adjust(scores(0.3), frame(2000.0))
    assert 0.3 < out.c_fatigue <= 0.3 + CFG["up_max"] + 1e-9      # 올리되 울타리 폭까지만


def test_strong_fsm_evidence_is_never_lowered():
    lr = ReactionLearner(CFG)
    x = lr.features(frame(1000.0), 0.9, 0.1)
    for i in range(12):
        lr._label(x, 0, 0.9, 1000.0 + i, "probe")    # 모델은 "피로 아님"을 배운다
    out = lr.adjust(scores(0.9), frame(2000.0))
    assert out.c_fatigue == 0.9                    # keep_floor 0.85 이상이면 낮추지 않는다


def test_checkin_answer_becomes_a_label_and_not_fsm_feedback():
    lr = ReactionLearner(CFG)
    lr.adjust(scores(0.4), frame(1000.0))
    res = type("R", (), {"state": State.FOCUS_PC})()
    req = lr.maybe_checkin(res, frame(1000.0), 1000.0, busy=False, request_id="b-1-checkin")
    assert req is not None and req["kind"] == "checkin_suggest" and lr.owns("b-1-checkin")
    lr.on_feedback("accept", "b-1-checkin", 1030.0)
    assert lr.n_labels == 1 and lr.counts["probe_yes"] == 1 and not lr.owns("b-1-checkin")
    assert lr.maybe_checkin(res, frame(1100.0), 1100.0, busy=False, request_id="b-2-checkin") is None   # 간격


@pytest.mark.parametrize("effect_weight,labels", [(None, 0), (1.0, 1)])
def test_intervention_recovery_label_is_off_unless_weighted(effect_weight, labels):
    """개입 뒤 C_fsm 이 떨어졌나로 정하는 라벨은 FSM 점수로 만든 정답이라 기본은 안 쓴다(effect_weight 0)."""
    lr = ReactionLearner(CFG if effect_weight is None else dict(CFG, effect_weight=effect_weight))
    lr.adjust(scores(0.8), frame(1000.0, typing=False, posture=1.0))
    res = type("R", (), {"state": State.ACTION_POSTURE, "gate": type("G", (), {"value": "auto"})()})()
    lr.after_tick(res, State.CAUSE_ANALYSIS, frame(1000.0, typing=False), 1000.0)
    assert len(lr.pending) == 1
    lr.adjust(scores(0.3), frame(1060.0))           # 깨어나 C_fsm 이 떨어짐
    res2 = type("R", (), {"state": State.MONITOR, "gate": type("G", (), {"value": "none"})()})()
    lr.after_tick(res2, State.ACTION_POSTURE, frame(1060.0), 1060.0)
    assert lr.n_labels == labels and lr.counts["pos"] == labels and not lr.pending


def test_state_persists_across_sessions(tmp_path):
    path = str(tmp_path / "learner.json")
    a = ReactionLearner(CFG, state_path=path)
    x = a.features(frame(1000.0), 0.3, 0.1)
    for i in range(5):
        a._label(x, 1, 0.3, 1000.0 + i, "probe")
    b = ReactionLearner(CFG, state_path=path)
    assert b.n_labels == 5 and b.model.w == a.model.w


def test_state_with_other_feature_order_is_not_loaded(tmp_path):
    """개수는 같고 순서가 다른 상태 파일(옛 버전·공통모델 형식 차이)의 가중치는 쓰지 않는다 — 틀린 가중치에 common_alpha 가 붙는 것을 막는다."""
    import json
    path = str(tmp_path / "learner.json")
    names = ReactionLearner.feature_names()
    n = len(names)
    json.dump({"model": {"n": n, "w": [0.5] * (n + 1), "g2": [1.0] * (n + 1)}, "n_labels": 0, "hist": [],
               "features": list(reversed(names)), "common": True}, open(path, "w", encoding="utf-8"))
    lr = ReactionLearner(CFG, state_path=path)
    assert lr.model.w != [0.5] * (n + 1)
    json.dump({"model": {"n": n, "w": [0.5] * (n + 1), "g2": [1.0] * (n + 1)}, "n_labels": 0, "hist": [],
               "features": names, "common": True}, open(path, "w", encoding="utf-8"))
    assert ReactionLearner(CFG, state_path=path).model.w == [0.5] * (n + 1)


def test_engine_uses_the_adjusted_score():
    e = FSMEngine()
    e.score_adjust = lambda s, f: Scores(c_focus=s.c_focus, c_fatigue=0.99, focus_weights=s.focus_weights,
                                         fatigue_weights=s.fatigue_weights)
    r = e.tick(frame(0.0))
    assert r.scores.c_fatigue == 0.99


def test_residual_model_starts_at_the_fsm_score():
    """잔차 학습: 가중치 0 이면 모델 = FSM 점수라 첫 라벨부터 FSM 만큼은 맞힌다. residual false 면 0.5 에서 시작."""
    lr = ReactionLearner(CFG)
    x = lr.features(frame(1000.0), 0.42, 0.1)
    assert lr.model.predict(x, lr._offset(0.42)) == pytest.approx(0.42)
    plain = ReactionLearner(dict(CFG, residual=False))
    assert plain.model.predict(x, plain._offset(0.42)) == pytest.approx(0.5)
    lr._label(x, 0, 0.3, 1000.0, "probe")                 # FSM 0.3 → '피로 아님' = 맞음 → 모델도 맞음
    assert list(lr.hist) == [(0, 0, 0)]


def test_learns_where_fsm_is_wrong_and_takes_over():
    """앱 응답이 'FSM 이 놓치는 졸림'을 계속 말하면 모델이 FSM 보다 잘 맞혀 α 가 오르고 점수를 올린다."""
    lr = ReactionLearner(dict(CFG, n_min=4, n_full=8))
    t = 1000.0
    for i in range(10):
        lr.adjust(scores(0.35), frame(t, typing=False, posture=0.9))   # 자세는 졸린데 FSM 점수는 낮다
        lr.on_self_report(8, t - 5.0, t)
        lr.adjust(scores(0.35), frame(t + 450, typing=True, posture=0.1))   # 깨어 타이핑 — FSM 도 낮고 실제로 안 졸림
        lr.on_self_report(3, t + 445.0, t + 450)
        t += 900.0
    acc_ai, acc_fsm = lr.accuracy()
    assert lr.counts["labels_kss"] == 20 and acc_ai > acc_fsm and lr.alpha() > 0.4
    out = lr.adjust(scores(0.35), frame(t, typing=False, posture=0.9))
    assert out.c_fatigue > 0.5                                         # 울타리(up_max 0.25) 안에서 올림
    calm = lr.adjust(scores(0.35), frame(t + 10, typing=True, posture=0.1))
    assert calm.c_fatigue < out.c_fatigue


def test_app_answer_is_used_once_and_suppresses_checkin():
    lr = ReactionLearner(CFG)
    lr.adjust(scores(0.4), frame(1000.0))
    lr.on_self_report(8, 995.0, 1000.0)
    lr.on_self_report(8, 995.0, 1010.0)                  # 같은 응답(cache 에 남아 있음) — 다시 쓰지 않는다
    assert lr.n_labels == 1 and lr.counts["labels_kss"] == 1 and lr.counts["pos"] == 1
    res = type("R", (), {"state": State.FOCUS_PC})()
    assert lr.maybe_checkin(res, frame(1500.0), 1500.0, busy=False, request_id="b-1-checkin") is None   # 앱이 방금 물음
    lr.on_self_report(5, 1895.0, 1900.0)
    assert lr.counts["neg"] == 1
    assert ReactionLearner(dict(CFG, kss_label=False)).counts["labels_kss"] == 0


def test_missed_prompt_at_desk_without_typing_is_a_drowsy_label():
    """졸 때는 앱 질문에 답을 못 한다 — 자리에 있고 타이핑 안 하는데 무응답이면 1(약하게). 자리 비움·타이핑 중이면 버린다."""
    lr = ReactionLearner(CFG)
    lr.adjust(scores(0.5), frame(1000.0, typing=False, posture=0.9))
    lr.on_prompt_missed(880.0, 1000.0, present=True, typing=False)
    lr.on_prompt_missed(880.0, 1010.0, present=True, typing=False)          # 같은 질문은 한 번
    assert lr.counts["labels_missed"] == 1 and lr.counts["pos"] == 1
    lr.on_prompt_missed(1780.0, 1900.0, present=False, typing=False)        # 자리 비움
    lr.on_prompt_missed(2680.0, 2800.0, present=True, typing=True)          # 일하느라 안 봄
    assert lr.counts["labels_missed"] == 1
    res = type("R", (), {"state": State.FOCUS_PC})()
    assert lr.maybe_checkin(res, frame(3000.0), 3000.0, busy=False, request_id="b-1-checkin") is None   # 앱이 방금 물음
    assert ReactionLearner(dict(CFG, missed_label=False)).counts["labels_missed"] == 0


def test_fence_lowers_less_than_it_raises():
    """비대칭 울타리(기본 down_max 0.10 < up_max 0.25): 모델이 FSM 점수를 낮추는 폭이 더 좁다."""
    from deskmate_hub.learning import load_learning_config
    base = load_learning_config()
    assert float(base["down_max"]) < float(base["up_max"])
    cfg = dict(CFG, up_max=base["up_max"], down_max=base["down_max"])
    lr = ReactionLearner(cfg)
    x = lr.features(frame(1000.0), 0.6, 0.1)
    for i in range(12):
        lr._label(x, 0, 0.6, 1000.0 + i, "probe")      # 모델은 '피로 아님'을 배운다(FSM 0.6 은 틀림)
    out = lr.adjust(scores(0.6), frame(2000.0))
    assert 0.6 - float(base["down_max"]) - 1e-9 <= out.c_fatigue < 0.6
