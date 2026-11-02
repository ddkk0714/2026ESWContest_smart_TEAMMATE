"""도움 순서(focus_help) 순위가 첫 기준을 바꾸고, 거절이 쌓이면 그 기준을 뒤집는다 (2026-11-01).

앱은 "먼저 받고 싶은 도움을 위로" 순서를 묻는데, 예전엔 항목마다 똑같이 +0.2 라 순서가 결과를 못 바꿨다.
이제 personalize.yaml onboarding.focus_help_rank_weights 배율로 1순위가 더 크게 앞선다. 그 뒤는 사용자의 반응(거절 −1)이
같은 사후 평균을 끌어내려, 첫 설정과 다르게 행동하면 정책이 따라간다.
"""
from __future__ import annotations

import copy

import pytest

from deskmate_hub.personalize.config import load_personalize_config
from deskmate_hub.personalize.features import FEATURE_INDEX, Situation
from deskmate_hub.personalize.onboarding import profile_adjustments
from deskmate_hub.personalize.policy import InterventionPolicy

BIAS = FEATURE_INDEX["bias"]
# 11-01 보드에 들어온 "소연" 프로필 그대로(앱 기본 밝기 0.45·볼륨 0.3·선풍기 끔 → mid·low·off), 순서만 바꿔 본다
SOYEON = {"light_pref": "mid", "sound_pref": "low", "wind_pref": "off", "sleep_hours": 8.0,
          "bedtime": "23:30", "wake_time": "07:30", "placement": "left"}


def _greedy():
    cfg = copy.deepcopy(load_personalize_config())
    cfg["bandit"]["algorithm"] = "greedy"           # 시연 프로파일(demo-b)과 같은 평균 선택
    return cfg


def _sit(ts=1_000_000.0) -> Situation:
    return Situation(ts=ts, hour_local=15.5, weekday=2, month=10, posture_dist={"face_down": 1.0}, posture_label="face_down",
                     posture_hold_min=2.0, slump_hold_min=2.0, present=True, drowsy=True, drowsy_count_30min=1,
                     co2_ppm=800, lux=300, temp_c=24.0, humidity_pct=50, pc_ratio=0.1, typing_active=False,
                     c_fatigue=0.80, c_focus=0.3, session_elapsed_min=30)


def _policy(order):
    p = InterventionPolicy(_greedy())
    p.apply_user_profile(dict(SOYEON, focus_help=list(order)))
    return p


def test_rank_weights_scale_focus_help_prior():
    cfg = load_personalize_config()
    ocfg = cfg["onboarding"]
    w = ocfg["focus_help_rank_weights"]
    base = ocfg["mapping"]["focus_help"]["sound"]["prior"]["sound_cue"]["accept"]["bias"]
    for rank in range(3):
        order = ["light", "wind"]
        order.insert(rank, "sound")
        deltas, _, _ = profile_adjustments({"focus_help": order}, ocfg)
        assert deltas["sound_cue"]["accept"]["bias"] == pytest.approx(w[rank] * base)
    # 표가 없으면 옛 동작(순위 무관 같은 가산)
    old = dict(ocfg)
    old.pop("focus_help_rank_weights")
    d1, _, _ = profile_adjustments({"focus_help": ["sound", "light"]}, old)
    d2, _, _ = profile_adjustments({"focus_help": ["light", "sound"]}, old)
    assert d1 == d2


def test_only_first_rank_gets_a_note():
    ocfg = load_personalize_config()["onboarding"]
    _, _, notes = profile_adjustments({"focus_help": ["sound", "light", "wind"]}, ocfg)
    assert notes == ["도움 1순위 알림 → 알림음 먼저"]


def test_order_changes_drowsy_choice_for_same_profile():
    """같은 소연 프로필(조명 보통·소리 작게)에서 순서만 바꾸면 졸음 개입이 바뀐다. 예전엔 두 순서 모두 wake_light 였다."""
    sound_first = _policy(["sound", "light", "wind"]).decide(_sit(), cause="posture", gate="auto", session_id="a")
    light_first = _policy(["light", "sound", "wind"]).decide(_sit(), cause="posture", gate="auto", session_id="b")
    assert sound_first.arm == "sound_cue", sound_first.scores
    assert light_first.arm == "wake_light", light_first.scores


def test_repeated_rejection_overrides_first_rank():
    """알림을 1순위로 골랐어도 알림음을 계속 거절하면 다른 방법(무드등)으로 넘어간다 — 세션마다 한 번씩 거절."""
    p = _policy(["sound", "light", "wind"])
    arms = []
    for i in range(10):
        d = p.decide(_sit(ts=1_000_000.0 + 3600 * i), cause="posture", gate="suggest", session_id=f"s{i}")
        arms.append(d.arm)
        if d.arm != "sound_cue":
            break
        p.feedback(d, "reject")
    assert arms[0] == "sound_cue" and arms[-1] != "sound_cue", arms
    assert len(arms) - 1 <= 3, arms                 # 거절 세 번 안에 바뀐다(시연 중 확인 가능한 횟수)


def test_personalize_off_env_switch(tmp_path, monkeypatch):
    """11-02 "지금은 개인화 하지 말아봐" — DESKMATE_PERSONALIZE=off 면 정책·반응 학습 없이 기록만."""
    import io

    from deskmate_hub.ingest import SensorCache
    from deskmate_hub.live import LiveHub
    from deskmate_hub.personalize import load_personalize_config

    monkeypatch.setenv("DESKMATE_PERSONALIZE", "off")
    pcfg = dict(load_personalize_config(), enabled=True, log_dir=str(tmp_path))
    out = io.StringIO()
    hub = LiveHub(SensorCache(), out=out, personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"))
    assert hub.personalize_recorder is not None and hub.personalize_recorder.policy is None
    assert hub.learner is None
    assert "DESKMATE_PERSONALIZE=off" in out.getvalue()
    monkeypatch.delenv("DESKMATE_PERSONALIZE")
    hub2 = LiveHub(SensorCache(), out=io.StringIO(), personalize_cfg=pcfg,
                   personalize_log_path=str(tmp_path / "p2.jsonl"))
    assert hub2.personalize_recorder.policy is not None


def test_personalize_switch_at_runtime(tmp_path, monkeypatch):
    """devview 버튼(feedback/user kind personalize_switch) — 재시작 없이 켜고 끈다."""
    import io

    from deskmate_hub.ingest import SensorCache
    from deskmate_hub.live import LiveHub
    from deskmate_hub.personalize import load_personalize_config

    monkeypatch.setenv("DESKMATE_PERSONALIZE", "off")
    pcfg = dict(load_personalize_config(), enabled=True, log_dir=str(tmp_path))
    cache = SensorCache()
    hub = LiveHub(cache, out=io.StringIO(), personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"))
    assert hub.personalize_recorder.policy is None
    cache.put_feedback({"kind": "personalize_switch", "on": True})
    hub.tick_once(1000.0)
    assert hub.personalize_recorder.policy is not None and not hub.personalize_off
    assert hub.engine.score_adjust is not None or not hub._learning_on_cfg
    cache.put_feedback({"kind": "personalize_switch", "on": False})
    hub.tick_once(1010.0)
    assert hub.personalize_recorder.policy is None and hub.learner is None and hub.engine.score_adjust is None
