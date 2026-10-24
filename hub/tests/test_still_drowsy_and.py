"""(실험) 'ToF 정지 ∧ mmWave 졸음 증거' — 펌웨어 DrowsyDetector 이식(tools/posture_sim/mmwave.py)과 hub 규칙
(ingest mmwave.still_drowsy_and, 기본 꺼짐)을 고정한다."""
from __future__ import annotations

import copy

import pytest

from deskmate_hub.inference import State
from deskmate_hub.ingest import Sample, SensorCache, SessionTracker, build_frame, load_ingest_config
from tools.posture_sim.mmwave import FwDrowsyDetector


def _fw_run(fw, t0, t1, *, move=5, hr=70, spike_every=None):
    """t0..t1 초를 1 초씩: 체동 1 Hz, 거리 2 s, 심박 3 s(펌웨어 갱신 주기)."""
    for t in range(t0, t1):
        fw.presence = 1
        level = 35 if spike_every and t % spike_every == 0 else move   # 30 < 35 ≤ 40: 정지는 깨고 심박은 안 버린다
        fw.on_body_move(level, float(t))
        if t % 2 == 0:
            fw.on_distance(55)
        if t % 3 == 0:
            fw.resp_rate, fw.resp_state = 14, 0
            fw.on_heart_rate(hr(t) if callable(hr) else hr, float(t))
        fw.tick(float(t))


def test_fw_still_alone_gives_drowsy_after_three_minutes():
    """심박이 그대로여도 정지만 180 s 면 DROWSY — 펌웨어 그대로(깨어 있는 정지와 못 가리는 이유)."""
    fw = FwDrowsyDetector(0.0)
    _fw_run(fw, 0, 170)
    assert fw.state == "AWAKE" and not fw.hr_down
    _fw_run(fw, 170, 200)
    assert fw.state == "DROWSY" and not fw.hr_down


def test_fw_heart_drop_is_evidence_and_shortens_the_hold():
    """5 분 각성 기준선(70) 뒤 심박이 62 로 떨어지면 30 s 유지 뒤 hr_down, 증거 1 개라 정지 60 s 면 DROWSY."""
    fw = FwDrowsyDetector(0.0)
    _fw_run(fw, 0, 300, spike_every=20)          # 움직이며 각성 기준선을 세운다(스파이크 → 정지 아님)
    assert fw.hr_base == pytest.approx(70.0, abs=0.5) and fw.state == "AWAKE"
    _fw_run(fw, 300, 600, hr=62, spike_every=20)  # 심박만 떨어짐, 계속 움직임 → 정지 아님
    assert fw.hr_down and fw.state == "AWAKE"
    # 체동 스파이크가 멎으면: 체동 blanking(60 s) 뒤에도 중앙값이 남아 있어 hr_down 유지, 정지 60 s 에 DROWSY
    _fw_run(fw, 600, 700, hr=62)
    assert fw.state == "DROWSY"


def test_fw_typing_spike_wakes_immediately():
    fw = FwDrowsyDetector(0.0)
    _fw_run(fw, 0, 200)
    assert fw.state == "DROWSY"
    _fw_run(fw, 200, 201, move=80)
    assert fw.state != "DROWSY"


def _cache(now, *, motion_mm, drowsy="AWAKE", hr_down=False):
    c = SensorCache()
    c.put("tof", Sample(received=now, ts=now, seq=None, data={
        "present": True, "posture": "upright", "posture_confidence": 0.9, "motion_score": 0.5, "nod_rate_hz": 0.0,
        "drowsy": False, "baseline_ready": True}))
    c.put("tof_mlp", Sample(received=now, ts=now, seq=None, data={
        "posture": "upright", "posture_confidence": 0.9, "present": True, "baseline_ready": True, "source": "gru",
        "tof_motion_mm": motion_mm}))
    c.put("mmwave", Sample(received=now, ts=now, seq=None, data={
        "present": True, "motion_state": "still", "motion_level": 5, "distance_cm": 60, "resp_bpm": 14, "resp_valid": True,
        "heart_bpm": 62, "heart_valid": True, "drowsy_state": drowsy, "valid": True, "hr_down": hr_down}))
    return c


def _cfg(**sd):
    cfg = copy.deepcopy(load_ingest_config())
    cfg["mmwave"]["still_drowsy_and"] = dict(cfg["mmwave"]["still_drowsy_and"], **sd)
    return cfg


def test_rule_is_off_by_default():
    cfg = load_ingest_config()
    assert cfg["mmwave"]["still_drowsy_and"]["enabled"] is False
    tr = SessionTracker()
    for t in (0.0, 70.0):
        f = build_frame(_cache(t, motion_mm=2.0, drowsy="DROWSY", hr_down=True).snapshot(), t, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f.signals["posture"].delta == 0.0                  # 바른 자세 표 값 그대로


def test_presence_base_freezes_baseline_while_still_and_detects_drop():
    """hr_down_presence_base: 깨어 움직일 때(mmWave 체동 > 10) 심박 72 로 기준선, 가만히 있으면 기준선을 얼리고
    심박이 66 으로 내려가면(6 bpm 하락) 창 120 s 중앙값이 바뀐 뒤 30 s 유지에 증거."""
    cfg = _cfg(enabled=True, mode="hr_down_presence_base", tof_still="none")
    tr = SessionTracker()

    def frame_at(t, *, level, hr):
        c = _cache(t, motion_mm=3.0)
        c.mmwave.data.update({"motion_level": level, "heart_bpm": hr, "heart_valid": True})
        return build_frame(c.snapshot(), t, cfg, tr, fsm_state=State.FOCUS_PC)

    for t in range(0, 400, 10):                       # 깨어 움직임 40 tick → 기준선 72
        frame_at(float(t), level=25, hr=72)
    assert tr.sd_debug["hr_base"] == 72 and not tr.sd_debug["evidence"]
    fired = None
    for t in range(400, 800, 10):                     # 가만히 + 66
        f = frame_at(float(t), level=5, hr=66)
        assert tr.sd_debug["hr_base"] == 72           # 얼어 있다
        if tr.sd_debug["evidence"] and fired is None:
            fired = t
    assert fired is not None and 400 + 60 <= fired <= 400 + 120 + 30
    assert f.signals["posture"].delta == 1.0


@pytest.mark.parametrize("mode,kw,fires", [
    ("hr_down", {"hr_down": True}, True),
    ("hr_down", {"drowsy": "DROWSY"}, False),                 # 정지만으로 난 DROWSY 는 hr_down 이 아니다
    ("drowsy_state", {"drowsy": "DROWSY"}, True),
])
def test_rule_needs_tof_still_for_tof_still_s_and_the_evidence(mode, kw, fires):
    cfg = _cfg(enabled=True, mode=mode, tof_motion_max_mm=8, tof_still_s=60)
    tr = SessionTracker()
    f = build_frame(_cache(0.0, motion_mm=3.0, **kw).snapshot(), 0.0, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f.signals["posture"].delta == 0.0                  # 정지 60 s 전
    f = build_frame(_cache(70.0, motion_mm=3.0, **kw).snapshot(), 70.0, cfg, tr, fsm_state=State.FOCUS_PC)
    assert (f.signals["posture"].delta == 1.0) is fires
    assert tr.sd_debug["tof_still"] and tr.sd_debug["active"] is fires
    # ToF 가 움직이면 정지 시각이 지워져 다시 60 s 를 기다린다
    f = build_frame(_cache(80.0, motion_mm=30.0, **kw).snapshot(), 80.0, cfg, tr, fsm_state=State.FOCUS_PC)
    assert f.signals["posture"].delta == 0.0 and tr.sd_still_since is None
