"""반응학습 v11 의 보드 코드 부분 — 되돌리기 라벨 무게·졸림 라벨 무게(pos_weight).
시뮬(tools/life_sim·posture_sim) 쪽 시험은 feat/reaction-ai 에 남긴다(9b0720b). 이 파일은 hub/deskmate_hub 만 본다."""
from __future__ import annotations

from deskmate_hub.inference import SensorFrame, Signal, State
from deskmate_hub.inference.types import Scores
from deskmate_hub.learning import ReactionLearner


def test_undo_label_can_be_left_to_the_output_policy():
    f = SensorFrame(now=1000.0, present=True, pc_ratio=0.9, signals={"posture": Signal(phi=0.1, delta=0.5)})
    for w, n in ((1.0, 1), (0.0, 0)):
        lr = ReactionLearner({"enabled": True, "undo_weight": w})
        lr.adjust(Scores(c_focus=0.1, c_fatigue=0.8, focus_weights={}, fatigue_weights={}), f)
        res = type("R", (), {"state": State.ACTION_POSTURE, "gate": type("G", (), {"value": "auto"})()})()
        lr.after_tick(res, State.CAUSE_ANALYSIS, f, 1000.0)
        lr.on_feedback("reject", None, 1030.0)                  # 자동 실행 뒤 되돌리기
        assert lr.n_labels == n


def test_pos_weight_scales_drowsy_labels_only():
    f = SensorFrame(now=1000.0, present=True, pc_ratio=0.9, signals={"posture": Signal(phi=0.1, delta=0.5)})
    a, b = ReactionLearner({"enabled": True}), ReactionLearner({"enabled": True, "pos_weight": 3.0})
    x = a.features(f, 0.4, 0.1)
    a._label(x, 1, 0.4, 1000.0, "kss")
    b._label(x, 1, 0.4, 1000.0, "kss")
    assert abs(b.model.w[-1]) > abs(a.model.w[-1])            # 졸림 라벨이 더 세게 움직인다
    a2, b2 = ReactionLearner({"enabled": True}), ReactionLearner({"enabled": True, "pos_weight": 3.0})
    a2._label(x, 0, 0.4, 1000.0, "kss")
    b2._label(x, 0, 0.4, 1000.0, "kss")
    assert a2.model.w == b2.model.w                            # 괜찮음 라벨은 그대로
