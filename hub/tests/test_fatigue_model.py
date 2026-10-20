"""공용 피로 모델 — 5분 창 특징과 기기 쪽 예측·프로필 보정."""
import math

import pytest

from deskmate_hub.fatigue import FEATURES, FatigueModel, PersonalBias, window_features
from deskmate_hub.fatigue.features import sensor_features
from deskmate_hub.fatigue.model import FORMAT

START = {"ts": 1000.0, "kss": 3, "sleep_hours_last_night": 5.0,
         "profile": {"chronotype": "evening", "usual_sleep_h": 7.0}}


def _tick(ts, posture="upright", flight=150.0, co2=800.0, ks_valid=True):
    return {"ts": ts, "c_fatigue": 0.3, "sensor_summary": {
        "present": True, "co2_ppm": co2,
        "tof": {"posture": posture, "drowsy": False, "motion_score": 0.4},
        "mmwave": {"motion_level": 20, "resp_bpm": 15, "resp_valid": True, "heart_bpm": 0, "heart_valid": False},
        "keystroke": {"valid": ks_valid, "window_s": 60, "event_count": 120, "dwell_mean_ms": 90,
                      "flight_mean_ms": flight, "flight_std_ms": flight / 2, "idle_ratio": 0.2, "correction_rate": 0.05},
    }}


def test_posture_groups_switches_and_missing_values():
    ticks = [_tick(1000 + 10 * i, "upright" if i < 15 else "head_down") for i in range(30)]
    ticks[0]["sensor_summary"]["tof"]["posture"] = "unknown"   # 판정 없음은 분모에서 빠진다
    f = sensor_features(ticks)
    assert f["pos_upright"] == pytest.approx(14 / 29)
    assert f["pos_head_down"] == pytest.approx(15 / 29)
    assert f["pos_switch_per_min"] == pytest.approx(1 / 5)
    assert f["ks_flight_cv"] == pytest.approx(0.5)
    assert f["mm_heart_bpm"] is None                            # valid=false 값은 쓰지 않는다
    assert sensor_features([])["env_co2"] is None


def test_relative_features_need_the_session_baseline():
    ticks = [_tick(1000 + 10 * i, flight=150.0 if i < 60 else 200.0, co2=800 + i) for i in range(120)]
    early = window_features(ticks, 1500.0, START)
    assert early["ks_flight_mean"] is None                      # 시작 10분 전엔 비교 기준 없음
    late = window_features(ticks, 2190.0, START)
    assert late["ks_flight_mean"] == pytest.approx(50.0)        # 기준(150) 대비 +50
    assert late["env_co2"] > 800                                # 환경은 절대값 그대로
    assert late["env_co2_slope"] == pytest.approx(6.0)          # 10 s 마다 +1 ppm
    assert late["elapsed_min"] == pytest.approx(19.833, abs=1e-3)
    assert late["sleep_debt"] == 2.0 and late["chronotype"] == 1.0 and late["kss_start"] == 3
    assert set(late) == set(FEATURES)


def _spec(**over):
    n = len(FEATURES)
    spec = {"format": FORMAT, "features": list(FEATURES), "mu": [0.0] * n, "sd": [1.0] * n,
            "coef": [0.0] * n, "intercept": 4.0}
    spec.update(over)
    return spec


def test_model_ignores_missing_features_and_clips():
    coef = [0.0] * len(FEATURES)
    coef[FEATURES.index("kss_start")] = 1.0
    m = FatigueModel(_spec(coef=coef))
    assert m.predict({}) == 4.0
    assert m.predict({"kss_start": 2.0, "env_co2": math.nan}) == 6.0
    assert m.predict({"kss_start": 50.0}) == 9.0
    assert FatigueModel.score(5.0) == 0.5
    with pytest.raises(ValueError):
        FatigueModel(_spec(format="other"))
    with pytest.raises(ValueError):
        FatigueModel(_spec(sd=[0.0] * len(FEATURES)))


def test_personal_bias_shrinks_toward_common_model():
    b = PersonalBias(prior_n=3)
    assert b.apply(5.0) == 5.0
    b.update(7.0, 5.0)                                          # 한 번 +2 빗나감 → 0.5 만 반영
    assert b.offset == pytest.approx(0.5)
    for _ in range(9):
        b.update(7.0, 5.0)
    assert b.offset == pytest.approx(20 / 13)
    assert PersonalBias.from_json(b.to_json()).offset == b.offset
