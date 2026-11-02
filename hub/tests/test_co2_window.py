"""CO₂ 창문 환기 타이밍 규칙(deskmate_hub/co2_window.py, life_sim VentRule 7070786 이식) — 카드 시점·FSM 존중·창문 가정·보드 묶음."""
from __future__ import annotations

import calendar
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from deskmate_hub.co2_window import CARD_KIND, Co2WindowRule, load_co2_window_config
from deskmate_hub.control import load_control_config
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.cache import Sample
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import InterventionPolicy, Situation, load_personalize_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "hub/deskmate_hub/config"
PCFG = load_personalize_config()
CFG = load_co2_window_config()
T0 = float(calendar.timegm((2026, 10, 25, 5, 0, 0)))      # 14:00 KST(가을, 정숙 시간 밖)
NIGHT = float(calendar.timegm((2026, 10, 25, 15, 0, 0)))  # 00:00 KST(정숙 23~7 시)
WINTER = float(calendar.timegm((2027, 1, 12, 5, 0, 0)))   # 1 월 14:00 KST
CLEAR = {"condition": "clear", "outdoor_temp_c": 14.0, "wind_ms": 2.0, "windy": False, "season": "autumn"}


def _rule(**over):
    return Co2WindowRule(dict(CFG, **over), personalize_cfg=PCFG)


def _run(rule, t0, t1, co2, *, step=10, posture="upright", state="FOCUS_BREAK", present=True, blocked=False,
         weather=CLEAR, pm25=None, indoor=24.0):
    """t0..t1 초(상대)를 step 초씩(보드 tick 10 s) 돌리고 카드가 처음 나온 (상대 시각, 카드) 를 돌려준다."""
    for t in range(t0, t1, step):
        card = rule.observe(T0 + t, co2, present=present, posture=posture, state=state, blocked=blocked,
                            weather=weather, pm25=pm25, indoor_temp=indoor)
        if card is not None:
            return t, card
    return None, None


def test_config_defaults_match_simulation_and_board_enabled():
    assert CFG["enabled"] is True
    for key, value in {"avg_s": 60, "on_ppm": 1000, "hold_s": 300, "high_ppm": 1400, "awake_s": 60,
                       "focus_defer_s": 600, "retry_s": 1200, "reject_retry_s": 2400}.items():
        assert CFG[key] == value, key
    assert _run(_rule(enabled=False), 0, 2000, 1500.0) == (None, None)


def test_card_after_five_minutes_high_while_awake_then_retry_after_no_answer():
    rule = _rule()
    t, card = _run(rule, 0, 1000, 1100.0)
    assert t == 300                                            # 평균 ≥ 1000 이 hold_s(300) 이어진 뒤
    assert card["minutes"] == 15 and "co2_rule" in card["why"] and "co2_avg=1100" in card["why"]
    assert "15 분" in card["explanation"]
    rule.on_feedback("timeout", T0 + 480)                      # 무응답(만료)
    assert _run(rule, 310, 1500, 1100.0) == (None, None)       # 카드 시각부터 retry_s(1200) 동안 다시 묻지 않는다
    assert _run(rule, 1500, 1800, 1100.0)[0] == 1500


def test_below_threshold_never_asks():
    assert _run(_rule(), 0, 3000, 950.0) == (None, None)


def test_no_card_while_drowsy_face_down_or_away():
    for kw in ({"posture": "drowsy"}, {"posture": "face_down"}, {"present": False}):
        assert _run(_rule(), 0, 2000, 1300.0, **kw) == (None, None), kw


def test_awake_must_last_awake_s_after_dozing():
    rule = _rule()
    assert _run(rule, 0, 400, 1200.0, posture="drowsy") == (None, None)
    t, _ = _run(rule, 400, 1000, 1200.0)                       # 깬 뒤 awake_s(60) 지나야
    assert t == 460


def test_weather_excludes_rain_snow_warmer_outdoor_and_bad_pm_once_logged():
    logs = []
    rule = Co2WindowRule(CFG, personalize_cfg=PCFG, on_log=logs.append)
    assert _run(rule, 0, 2000, 1300.0, weather=dict(CLEAR, condition="rain")) == (None, None)
    assert len(logs) == 1 and "rain" in logs[0]                # 카드 없이 로그 한 번
    for kw in ({"weather": dict(CLEAR, condition="snow")},
               {"weather": dict(CLEAR, outdoor_temp_c=27.0), "indoor": 25.0},      # 바깥이 2 °C 이상 더움
               {"pm25": 40.0}, {"weather": dict(CLEAR, pm_bad=True)}):
        assert _run(_rule(), 0, 2000, 1300.0, **kw) == (None, None), kw
    _, card = _run(_rule(), 0, 2000, 1300.0, weather=dict(CLEAR, wind_ms=5.0, windy=True))
    assert "windy" in card["why"]                              # 바람은 이유만 덧붙인다
    _, card = _run(_rule(), 0, 2000, 1300.0, weather=None)     # 날씨 모름 → when_unknown: allow
    assert "weather_unknown" in card["why"]
    assert _run(_rule(weather=dict(CFG["weather"], when_unknown="skip")), 0, 2000, 1300.0, weather=None) == (None, None)


def test_fsm_action_or_pending_card_blocks_and_quiet_hours_respected():
    assert _run(_rule(), 0, 2000, 1300.0, blocked=True) == (None, None)
    rule = _rule()
    assert _run(rule, 0, 600, 1300.0, blocked=True) == (None, None)
    assert _run(rule, 600, 700, 1300.0)[0] == 600              # 막힘이 풀리면 바로
    rule = _rule()
    for t in range(0, 2000, 10):
        assert rule.observe(NIGHT + t, 1300.0, present=True, posture="upright", state="FOCUS_BREAK",
                            weather=CLEAR, indoor_temp=24.0) is None
    assert rule.last_skip == "quiet_hours"
    rule = _rule(respect_quiet_hours=False)
    assert any(rule.observe(NIGHT + t, 1300.0, present=True, posture="upright", state="FOCUS_BREAK",
                            weather=CLEAR, indoor_temp=24.0) for t in range(0, 2000, 10))


def test_focus_waits_up_to_ten_minutes_unless_urgent():
    assert _run(_rule(), 0, 2000, 1200.0, state="FOCUS_PC")[0] == 900      # hold 300 + focus_defer_s 600
    t, card = _run(_rule(), 0, 2000, 1500.0, state="FOCUS_PC")
    assert t == 300 and "urgent" in card["why"]                             # ≥ high_ppm(1400) 이면 바로
    rule = _rule()
    assert _run(rule, 0, 500, 1200.0, state="FOCUS_PC") == (None, None)
    t, card = _run(rule, 500, 2000, 1200.0, state="FOCUS_BREAK")            # 몰입이 끝나면 바로
    assert t == 500 and "after_focus_wait" in card["why"]


def test_reject_waits_forty_minutes():
    rule = _rule()
    t, _ = _run(rule, 0, 1000, 1100.0)
    rule.on_feedback("reject", T0 + t + 30)
    assert _run(rule, t + 10, t + 30 + 2400, 1100.0) == (None, None)
    assert _run(rule, t + 30 + 2400, t + 3000, 1100.0)[0] == t + 30 + 2400


def test_accept_assumes_window_open_for_minutes_then_measures_again():
    rule = _rule()
    t, card = _run(rule, 0, 1000, 1300.0)
    rule.on_feedback("accept", T0 + t)
    open_s = card["minutes"] * 60
    assert _run(rule, t + 10, t + open_s, 1300.0) == (None, None)          # 열려 있는 동안 카드 없음
    assert rule.last_skip == "window_open"
    t2, _ = _run(rule, t + open_s, t + open_s + 3000, 1300.0)
    assert t2 == max(t + 1200, t + open_s + 300)                            # 닫힌 뒤 hold 를 처음부터 · retry_s 도 지켜야


def test_window_minutes_reuse_personalize_ventilation_plan():
    rule = _rule()
    policy = InterventionPolicy(PCFG)
    for now, w, indoor in ((T0, CLEAR, 24.0), (WINTER, dict(CLEAR, season="winter", outdoor_temp_c=-3.0), 22.0),
                           (T0, dict(CLEAR, wind_ms=6.0, windy=True), 24.0), (WINTER, None, 22.0)):
        lt = rule._local(now)
        sit = Situation(ts=now, hour_local=lt.tm_hour, weekday=lt.tm_wday, month=lt.tm_mon, temp_c=indoor, weather=w)
        assert rule.minutes(now, indoor, w)[0] == policy.ventilate_minutes(sit)
    assert rule.minutes(WINTER, 22.0, None)[0] == 5                         # 겨울은 짧게(시뮬: 겨울 5 분)
    assert Co2WindowRule(CFG, personalize_cfg={}).minutes(T0, 24.0, None) == (15, [])   # personalize 없으면 fallback


# ── LiveHub 통합 ───────────────────────────────────────────

def _hub(tmp_path, *, control=False):
    requests = []
    control_cfg = dict(load_control_config(), adapter="mock", esm_log_path=str(tmp_path / "esm.jsonl")) if control else \
        {"enabled": False, "esm_log_path": str(tmp_path / "esm.jsonl")}
    hub = LiveHub(SensorCache(), publish=lambda e: None, publish_request=requests.append, control_cfg=control_cfg,
                  personalize_cfg=dict(PCFG, enabled=False, record=False), profile="", out=io.StringIO())
    return hub, requests


def _feed(hub, now, *, co2=1200.0, present=True, weather=None):
    msgs = {"mmwave/node": {"present": present, "motion_level": 40, "drowsy_state": "AWAKE", "resp_bpm": 15,
                            "resp_valid": True, "valid": True, "distance_cm": 60},
            "env/node": {"co2_ppm": co2, "co2_valid": True, "temp_c": 24, "temp_valid": True,
                         "humidity_pct": 45, "humidity_valid": True, "lux": 400, "lux_valid": True}}
    for topic, data in msgs.items():
        payload = json.dumps({"schema_version": "1.0", "ts": now, "data": data}).encode()
        assert route_mqtt_message(hub.cache, "deskmate/sensor/" + topic, payload, now)
    if weather is not None:
        hub.cache.weather = Sample(received=now, ts=now, seq=None, data=weather)
    return hub.tick_once(now)


def _until_card(hub, requests, start, *, limit_s=1200, **kw):
    now, seen = start, len(requests)
    while now < start + limit_s:
        _feed(hub, now, **kw)
        if any(r["data"]["kind"] == CARD_KIND for r in requests[seen:]):
            return now
        now += hub.period
    return None


def _feedback(hub, request, verdict, now):
    route_mqtt_message(hub.cache, "deskmate/feedback/user", json.dumps({
        "schema_version": "1.0", "data": {"request_id": request["data"]["request_id"], "verdict": verdict}}).encode(), now)


def test_live_hub_publishes_vent_card_and_handles_feedback_outside_fsm_and_control(tmp_path):
    hub, requests = _hub(tmp_path, control=True)
    calls = []
    original = hub.control.on_feedback
    hub.control.on_feedback = lambda *a, **k: (calls.append(a), original(*a, **k))
    t = _until_card(hub, requests, T0)
    assert t is not None and T0 + 300 <= t <= T0 + 420
    data = requests[-1]["data"]
    assert data["kind"] == "vent_suggest" and data["prompt_code"] == "ventilate_window"
    assert data["minutes"] == data["ventilate_minutes"] == 15 and data["options"] == ["accept", "reject"]
    assert data["expires_in_s"] == 180 and data["arm"] == "ventilate_suggest" and "co2_rule" in data["why"]
    assert "창문" in data["explanation"] and hub.pending_request is requests[-1]
    assert hub.control.episode is None and not hub.control.history          # 기기 명령 없음(화면 카드만)
    _feedback(hub, requests[-1], "reject", t + 5)
    _feed(hub, t + 10)
    assert hub.pending_request is None and calls == []                      # 거절이 제어(되돌리기)로 가지 않는다
    assert hub.co2_window.counts["reject"] == 1
    esm = [json.loads(line) for line in (tmp_path / "esm.jsonl").read_text(encoding="utf-8").splitlines()]
    assert esm[-1]["kind"] == "vent_suggest" and esm[-1]["verdict"] == "reject"
    assert _until_card(hub, requests, t + 20, limit_s=2300) is None         # 거절 뒤 40 분 쉼


def test_live_hub_accept_keeps_window_open_and_expiry_retries(tmp_path):
    hub, requests = _hub(tmp_path)
    t = _until_card(hub, requests, T0)
    _feedback(hub, requests[-1], "accept", t + 5)
    _feed(hub, t + 10)
    assert hub.pending_request is None and hub.co2_window.window_open_until == t + 10 + 15 * 60
    state = _feed(hub, t + 20)
    assert state["data"]["sensor_summary"]["co2_window"]["window_open_s"] > 0
    assert _until_card(hub, requests, t + 30, limit_s=15 * 60 - 30) is None
    # 만료(무응답) → retry_s 뒤 다시
    hub, requests = _hub(tmp_path)
    t = _until_card(hub, requests, T0)
    _feed(hub, t + 180)
    assert hub.pending_request is None and hub.co2_window.counts["timeout"] == 1
    t2 = _until_card(hub, requests, t + 190, limit_s=2000)
    assert t2 is not None and t2 >= t + 1200
    if hub.engine.state.value.startswith("FOCUS_") and hub.engine.state.value != "FOCUS_BREAK":
        assert t2 == t + 1200 + 600                                         # 몰입 중이면 retry 뒤에도 최대 10 분 더 기다린다


def test_live_hub_no_card_when_absent_raining_or_another_card_pending(tmp_path):
    hub, requests = _hub(tmp_path)
    assert _until_card(hub, requests, T0, present=False) is None
    hub, requests = _hub(tmp_path)
    rain = {"temp_c": 12.0, "humidity": 90, "wind_ms": 1.0, "condition": "rain", "season": "autumn", "fetched_at": T0}
    assert _until_card(hub, requests, T0, weather=rain) is None
    hub, requests = _hub(tmp_path)
    other = {"schema_version": "1.0", "ts": T0, "data": {"request_id": "x-1", "kind": "break_suggest", "expires_in_s": None}}
    hub.pending_request = other
    assert _until_card(hub, requests, T0) is None and hub.pending_request is other
    assert hub.co2_window.last_skip == "blocked"


def test_payload_bundles_co2_window_config_as_json_readable_without_yaml(tmp_path):
    executable = tmp_path / "hub.elf"
    executable.write_bytes(b"ELF fixture")
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    result = subprocess.run([sys.executable, str(ROOT / "hub/atlas/tools/build_payload.py"),
                             "--executable", str(executable), "--hub", str(ROOT / "hub/deskmate_hub"),
                             "--config", str(CONFIG_DIR / "fsm.yaml"), "--ingest-config", str(CONFIG_DIR / "ingest.yaml"),
                             "--stdlib", str(stdlib)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(executable) as archive:
        names = archive.namelist()
        assert "deskmate_hub/co2_window.py" in names
        assert json.loads(archive.read("deskmate_hub/config/co2_window.json")) == CFG
        assert "deskmate_hub/config/co2_window.yaml" not in names
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); sys.modules['yaml'] = None\n"
            "from deskmate_hub.co2_window import load_co2_window_config\n"
            "c = load_co2_window_config(); print(json.dumps([c['enabled'], c['on_ppm'], c['hold_s']]))")
    probe = subprocess.run([sys.executable, "-c", code, str(executable)], capture_output=True, text=True, cwd=str(tmp_path))
    assert probe.returncode == 0 and json.loads(probe.stdout) == [True, 1000, 300], probe.stderr[-2000:]
