"""hub 라이브 피로 점수 — fatigue/label 수신, 학습과 같은 창 특징, 1분 주기, 동의가 있을 때만 프로필 보정."""
from __future__ import annotations

import io
import json

import pytest

from deskmate_hub.fatigue import FEATURES, FatigueModel, window_features
from deskmate_hub.fatigue.live import FatigueRuntime, load_fatigue_config, load_fatigue_model
from deskmate_hub.fatigue.model import FORMAT
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.mqtt_lines import MqttLineSource
from deskmate_hub.live import LiveHub

START = {"event": "session_start", "session_id": "A-1-1000", "profile_id": "A-1", "ts": 1000.0, "kss": 3,
         "sleep_hours_last_night": 5.0, "profile": {"chronotype": "evening", "usual_sleep_h": 7.0}}
VIEW_KEYS = {"kss", "kss_personal", "window_s", "n_ticks", "model_id", "valid", "ts"}


def _tick(ts, flight=150.0, c_fatigue=0.3):
    return {"ts": ts, "c_fatigue": c_fatigue, "fsm_state": "FOCUS_PC", "sensor_summary": {
        "present": True, "co2_ppm": 800.0,
        "tof": {"posture": "upright", "drowsy": False, "motion_score": 0.4},
        "keystroke": {"valid": True, "window_s": 60, "event_count": 120, "flight_mean_ms": flight},
    }}


def _model(**weights):
    n = len(FEATURES)
    coef = [0.0] * n
    for name, w in weights.items():
        coef[FEATURES.index(name)] = w
    return FatigueModel({"format": FORMAT, "features": list(FEATURES), "mu": [0.0] * n, "sd": [1.0] * n,
                         "coef": coef, "intercept": 4.0})


def _runtime(model=None, *, allowed=False, path=None):
    flag = {"on": allowed}
    rt = FatigueRuntime({"period_sec": 60, "min_tick_ratio": 0.8, "personal": {"prior_n": 3, "path": path}},
                        model or _model(kss_start=0.5, ks_flight_mean=0.02), model_id="m1", tick_period=10.0,
                        personal_allowed=lambda: flag["on"])
    return rt, flag


def test_fatigue_label_routes_from_native_mqtt_line():
    cache = SensorCache()
    body = {"schema_version": "1.0", "ts": 1.0, "node": "display-fatigue", "data": dict(START)}
    assert MqttLineSource(cache).feed_line("MQTT\tdeskmate/fatigue/label\t" + json.dumps(body) + "\n") == "fatigue_label"
    (label, received), = cache.pop_fatigue_label_events()
    assert label["event"] == "session_start" and received is not None
    assert cache.pop_fatigue_label_events() == []


def test_live_score_uses_the_training_window_features():
    rt, _ = _runtime()
    assert rt.on_label(dict(START)) == "session_start"
    ticks = [_tick(1000.0 + 10 * i, flight=150.0 if i < 60 else 210.0) for i in range(121)]
    for t in ticks:
        view = rt.observe(t)
    assert view["ts"] == 2200.0 and view["valid"] and view["n_ticks"] == 30
    expected = rt.model.predict(window_features(ticks, 2200.0, START))
    assert view["kss"] == round(expected, 2)
    assert view["kss"] == pytest.approx(4.0 + 0.5 * 3 + 0.02 * 60.0)    # 시작 KSS + 기준(150) 대비 +60 ms
    assert set(view) == VIEW_KEYS and view["model_id"] == "m1" and view["kss_personal"] is None


def test_score_is_recomputed_once_per_period_and_short_windows_are_invalid():
    rt, _ = _runtime()
    stamps = [rt.observe(_tick(1000.0 + 10 * i))["ts"] for i in range(13)]
    assert stamps == [1000.0] * 6 + [1060.0] * 6 + [1120.0]
    assert rt.view["n_ticks"] == 13 and not rt.view["valid"]             # 5분 창의 30개 중 13개


def test_without_app_session_the_hub_session_start_is_the_baseline():
    rt, _ = _runtime()
    ticks = [_tick(500.0 + 10 * i, flight=150.0 if i < 60 else 180.0) for i in range(91)]
    rt.set_hub_session(500.0)
    for t in ticks:
        view = rt.observe(t)
    feats = window_features(ticks, 1400.0, {"ts": 500.0})
    assert feats["kss_start"] is None and feats["ks_flight_mean"] == pytest.approx(30.0)
    assert view["kss"] == round(rt.model.predict(feats), 2)


def test_session_end_returns_to_hub_context():
    rt, _ = _runtime()
    rt.set_hub_session(900.0)
    rt.on_label(dict(START))
    assert rt._context()["session_id"] == "A-1-1000"
    assert rt.on_label({"event": "session_end", "session_id": "A-1-1000", "profile_id": "A-1", "ts": 2000.0}) \
        == "session_end"
    assert rt._context() == {"ts": 900.0}


def test_personal_bias_only_with_consent(tmp_path):
    path = tmp_path / "personalization" / "fatigue_bias.json"
    rt, flag = _runtime(allowed=False, path=str(path))
    rt.on_label(dict(START))
    for i in range(61):
        rt.observe(_tick(1000.0 + 10 * i))
    kss = {"event": "kss", "session_id": "A-1-1000", "profile_id": "A-1", "ts": 1610.0, "prompt_ts": 1600.0, "kss": 8}
    rt.on_label(dict(kss))
    assert rt.biases == {} and not path.exists()                          # 동의 없으면 배우지도 저장하지도 않는다

    flag["on"] = True
    rt.on_label(dict(kss, ts=1611.0))
    rt.on_label(dict(kss, ts=1611.0))                                     # QoS 1 재전송 중복
    common = rt.model.predict(window_features(list(rt.recent), 1600.0, START))
    assert rt.biases["A-1"].n == 1 and rt.biases["A-1"].offset == pytest.approx((8 - common) / 4)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model_id"] == "m1" and saved["profiles"]["A-1"]["n"] == 1
    view = None
    for i in range(61, 67):
        view = rt.observe(_tick(1000.0 + 10 * i))
    assert view["kss_personal"] == round(rt.biases["A-1"].apply(rt.model.predict(
        window_features(list(rt.recent), view["ts"], START))), 2)

    other, _ = _runtime(allowed=True, path=str(path))
    assert other.biases["A-1"].n == 1                                     # 재시작해도 이어진다
    other.model_id = "m2"
    other.load_personal()
    assert other.biases == {}                                             # 다른 모델의 보정값은 버린다
    rt.reset_personal()
    assert rt.biases == {} and not path.exists()


def test_live_hub_publishes_fatigue_summary_and_takes_labels(tmp_path):
    cache = SensorCache()
    hub = LiveHub(cache, control_cfg={"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")},
                  fatigue_cfg={"enabled": True, "period_sec": 60,
                               "personal": {"require_consent": True, "path": str(tmp_path / "bias.json")}},
                  fatigue_model=_model(fsm_c_fatigue=2.0), out=io.StringIO())
    cache.put_fatigue_label(dict(START), received=100.0)
    envelope = hub.tick_once(100.0)
    fatigue = envelope["data"]["sensor_summary"]["fatigue"]
    assert set(fatigue) == VIEW_KEYS and fatigue["n_ticks"] == 1 and not fatigue["valid"]
    assert fatigue["kss"] == round(4.0 + 2.0 * envelope["data"]["c_fatigue"], 2)
    assert hub.fatigue.session["session_id"] == "A-1-1000"
    assert hub._fatigue_personal_allowed() is False                       # privacy.yaml 기본은 꺼짐 → 보정 없음


def test_live_hub_without_model_runs_without_fatigue(tmp_path, monkeypatch):
    monkeypatch.delenv("DESKMATE_FATIGUE_MODEL", raising=False)
    cache = SensorCache()
    out = io.StringIO()
    hub = LiveHub(cache, control_cfg={"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")},
                  fatigue_cfg={"enabled": True, "model": "config/no_such_fatigue.json"}, out=out)
    cache.put_fatigue_label(dict(START))
    envelope = hub.tick_once(100.0)
    assert hub.fatigue is None and "fatigue" not in envelope["data"]["sensor_summary"]
    assert cache.pop_fatigue_label_events() == []                         # 모델이 없어도 쌓아 두지 않는다
    assert "모델 JSON 없음" in out.getvalue()


def test_model_file_from_environment_and_default_config(tmp_path):
    spec = {"format": FORMAT, "features": list(FEATURES), "mu": [0.0] * len(FEATURES),
            "sd": [1.0] * len(FEATURES), "coef": [0.0] * len(FEATURES), "intercept": 5.0}
    path = tmp_path / "fatigue_linear.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    model, model_id = load_fatigue_model({"model": "config/x.json"}, env={"DESKMATE_FATIGUE_MODEL": str(path)})
    assert model.predict({}) == 5.0 and model_id.startswith("fatigue_linear-") and len(model_id) == 23
    path.write_text("{broken", encoding="utf-8")
    assert load_fatigue_model({}, env={"DESKMATE_FATIGUE_MODEL": str(path)}) == (None, None)
    cfg = load_fatigue_config()
    assert cfg["enabled"] is True and cfg["period_sec"] == 60 and cfg["personal"]["require_consent"] is True
