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


def _mm(now, *, bpm, level):
    from deskmate_hub.ingest import Sample
    return Sample(received=now, ts=now, seq=None, data={
        "present": True, "motion_state": "still" if level <= 10 else "active", "motion_level": level, "distance_cm": 55,
        "resp_bpm": 15, "resp_valid": True, "heart_bpm": bpm, "heart_valid": True, "drowsy_state": "AWAKE", "valid": True})


def test_hr_drop_against_presence_baseline_reaches_frame_and_learner():
    from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config
    cfg = load_ingest_config()
    cache, tr = SensorCache(), SessionTracker()
    t = 1000.0
    for i in range(30):                                   # 깨어 움직일 때 심박 72 → 기준선
        cache.put("mmwave", _mm(t, bpm=72, level=40))
        f = build_frame(cache.snapshot(), t, cfg, tr, fsm_state=State.FOCUS_PC)
        t += 10.0
    assert f.mm_motion == 40.0
    for i in range(20):                                   # 가만히, 심박 64 → 기준선은 얼고 지금은 내려감
        cache.put("mmwave", _mm(t, bpm=64, level=2))
        f = build_frame(cache.snapshot(), t, cfg, tr, fsm_state=State.FOCUS_PC)
        t += 10.0
    assert f.hr_drop_bpm == 8.0
    lr = ReactionLearner({"enabled": True})
    x = lr.features(f, 0.4, 0.1)
    names = lr.feature_names()
    assert x[names.index("hr_drop")] == 0.8 and x[names.index("hr_avail")] == 1.0
    assert x[names.index("mm_motion")] == 0.02 and x[names.index("mm_avail")] == 1.0
    assert build_frame(SensorCache().snapshot(), t, cfg, SessionTracker(), fsm_state=State.IDLE).hr_drop_bpm is None
