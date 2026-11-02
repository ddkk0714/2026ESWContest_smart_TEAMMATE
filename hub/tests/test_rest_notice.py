"""REST 진입 알림(rest_notice.py, 2026-10-26) — FSM 이 어떤 길로든 REST 에 들어가면 안내 카드 + 음성 한 번.

경로 셋: 휴식 제안 수락(ACTION_BREAK → REST, rest_timer.start) · 개입 실패 뒤 강제 휴식(ESCALATE → REST, force_rest) ·
졸음 재시도(시연 asleep_retry: rest, 엎드린 채 ESCALATE → REST). 각 경로에 rest_notice 1 + speak 1(sound_pref off 면 카드만),
쿨다운 안 재진입은 알리지 않고, REST 가 길어지면 한 번 더. 시연 테스트(엎드림·헛켜짐)는 그대로 통과해야 한다.
"""
from __future__ import annotations

import io

import pytest

from deskmate_hub.inference import GateMode, State, load_config
from deskmate_hub.ingest import SensorCache
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import load_personalize_config
from deskmate_hub.rest_notice import DEFAULTS, KIND, RestNotifier

from test_ingest_mapping import env, mm
from test_posture_actions import CFG as CONTROL_CFG
from test_tof_service import svc


class _Sig:
    def __init__(self, delta, available=True):
        self.delta, self.available = delta, available


class _Frame:
    def __init__(self, posture_delta=0.0, available=True):
        self._p = _Sig(posture_delta, available)

    def get(self, key):
        return self._p


class _Result:
    def __init__(self, state, actions=(), cause=None):
        self.state, self.actions, self.cause = state, list(actions), cause


def _notifier(cfg=None, *, fsm=None, variant="default", control=True):
    reqs, cmds = [], []
    fsm_cfg = fsm or {"timers": {"rest_min_sec": 300}, "thresholds": {"still_asleep_delta": 0.9}}
    n = RestNotifier(cfg, fsm_cfg=fsm_cfg, boot_id="b", publish_request=reqs.append,
                     publish_control=cmds.append if control else None, variant=variant)
    return n, reqs, cmds


# ── 단위: 이유·문구·쿨다운·리마인더·소리 끔 ─────────────────────────────────────────

def test_accepted_path_sends_notice_and_speak_once():
    n, reqs, cmds = _notifier()
    out = n.on_tick(_Result(State.REST, ["rest_timer.start"], "cognitive"), State.ACTION_BREAK, _Frame(0.1), 100.0, seq=7, sound_pref="mid")
    assert len(reqs) == 1 and len(cmds) == 1 and len(out) == 2
    d = reqs[0]["data"]
    assert d["kind"] == KIND and d["prompt_code"] == "rest_now" and d["options"] == ["dismiss"] and d["reason"] == "accepted"
    assert d["minutes"] == 5 and d["rest_min_sec"] == 300 and d["speak"] is True and d["request_id"] == "b-7-rest"
    assert "5분" in d["text"] and d["expires_in_s"] == 60
    c = cmds[0]
    assert (c["target_id"], c["operation"], c["gate"], c["origin_state"]) == ("speaker", "speak", "notice", "REST")
    assert c["value"] == {"text": d["text"], "lang": "ko"} and c["requires_confirmation"] is False and c["command_id"] == "b-7-speak"
    # REST 유지 tick 에는 더 내지 않는다
    assert n.on_tick(_Result(State.REST, ["maintain_env"]), State.REST, _Frame(0.1), 110.0, seq=8, sound_pref="mid") == []
    assert len(reqs) == 1 and len(cmds) == 1
    assert n.report() == {"count": 1, "spoken": 1, "reminders": 0, "suppressed": 0, "dismissed": 0, "last_reason": "accepted", "last_ts": 100.0}


def test_forced_paths_pick_asleep_or_escalated_reason():
    n, reqs, _ = _notifier()
    n.on_tick(_Result(State.REST, ["log_intervention_fail", "force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 100.0, seq=1, sound_pref=None)
    assert reqs[-1]["data"]["reason"] == "asleep" and "졸음" in reqs[-1]["data"]["text"]
    n2, reqs2, _ = _notifier()
    n2.on_tick(_Result(State.REST, ["force_rest"], "environment"), State.ESCALATE, _Frame(0.1), 100.0, seq=1, sound_pref=None)
    assert reqs2[-1]["data"]["reason"] == "escalated" and "피로" in reqs2[-1]["data"]["text"]
    # still_asleep_delta 가 없는 기본 fsm 에서도 cause posture 면 asleep
    n3, reqs3, _ = _notifier(fsm={"timers": {"rest_min_sec": 60}, "thresholds": {}})
    n3.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(0.5), 100.0, seq=1, sound_pref=None)
    assert reqs3[-1]["data"]["reason"] == "asleep" and reqs3[-1]["data"]["minutes"] == 1


def test_sound_off_sends_card_only_and_study_variant_text():
    n, reqs, cmds = _notifier(variant="study")
    n.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 100.0, seq=1, sound_pref="off")
    assert len(reqs) == 1 and cmds == [] and reqs[0]["data"]["speak"] is False and reqs[0]["data"]["variant"] == "study"
    assert reqs[0]["data"]["text"] == DEFAULTS["messages"]["asleep"]["study"].replace("{minutes}", "5")
    # speak: false 설정도 카드만
    n2, reqs2, cmds2 = _notifier({"speak": False})
    n2.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 100.0, seq=1, sound_pref="high")
    assert len(reqs2) == 1 and cmds2 == []


def test_cooldown_blocks_quick_reentry_and_reminder_fires_once():
    n, reqs, cmds = _notifier({"cooldown_sec": 120, "reminder_after_sec": 300})
    n.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 100.0, seq=1, sound_pref=None)
    n.on_tick(_Result(State.RECOVERY), State.REST, _Frame(0.0), 170.0, seq=2, sound_pref=None)
    n.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 180.0, seq=3, sound_pref=None)  # 80 s 뒤 재진입
    assert len(reqs) == 1 and n.counts["suppressed"] == 1
    n.on_tick(_Result(State.RECOVERY), State.REST, _Frame(0.0), 190.0, seq=4, sound_pref=None)
    n.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 300.0, seq=5, sound_pref=None)  # 쿨다운 지남
    assert len(reqs) == 2 and reqs[1]["data"]["reason"] == "asleep"
    for t in (310.0, 400.0, 590.0):
        assert n.on_tick(_Result(State.REST, ["maintain_env"]), State.REST, _Frame(1.0), t, seq=6, sound_pref=None) == []
    out = n.on_tick(_Result(State.REST, ["maintain_env"]), State.REST, _Frame(1.0), 600.0, seq=7, sound_pref=None)      # 300 s 뒤 한 번 더
    assert len(out) == 2 and reqs[-1]["data"]["reason"] == "reminder" and len(reqs) == 3 and len(cmds) == 3
    assert n.on_tick(_Result(State.REST, ["maintain_env"]), State.REST, _Frame(1.0), 900.0, seq=8, sound_pref=None) == []
    assert n.report()["reminders"] == 1
    n.on_feedback("dismiss", reqs[-1]["data"]["request_id"], 601.0)
    assert n.counts["dismissed"] == 1 and n.open_request_id is None


def test_disabled_sends_nothing():
    n, reqs, cmds = _notifier({"enabled": False})
    assert n.on_tick(_Result(State.REST, ["force_rest"], "posture"), State.ESCALATE, _Frame(1.0), 100.0, seq=1, sound_pref=None) == []
    assert reqs == [] and cmds == []


# ── LiveHub: 세 경로 + mock 스피커 + 리포트 + 피드백 ─────────────────────────────────

def _hub(tmp_path, profile=""):
    cache, reqs, states = SensorCache(), [], []
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub = LiveHub(cache, fsm_cfg=None if profile else load_config(), control_cfg=CONTROL_CFG, publish=states.append,
                  publish_request=reqs.append, out=io.StringIO(), profile=profile,
                  personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"))
    return hub, cache, reqs, states


def _settle_focus(hub, cache):
    period, t = hub.period, 0.0
    while t <= float(hub.fsm_cfg["timers"]["baseline_sec"]) + 2 * period:
        cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700)); hub.tick_once(t); t += period
    return t


def test_live_break_accept_enters_rest_with_notice_and_mock_speaker(tmp_path):
    hub, cache, reqs, states = _hub(tmp_path)
    t = _settle_focus(hub, cache)
    # 휴식 제안 카드가 떠 있는 ACTION_BREAK 로 두고 수락 → REST(rest_timer.start)
    hub.engine.state = State.ACTION_BREAK
    hub.engine._state_since = t
    hub.engine._last_cause = "cognitive"
    hub.pending_request = {"ts": t, "data": {"request_id": "q1", "kind": "break_suggest", "cause": "cognitive", "expires_in_s": 180}}
    cache.put_feedback({"verdict": "accept", "request_id": "q1"})
    cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700))
    envelope = hub.tick_once(t)
    assert hub.engine.state is State.REST
    notices = [r for r in reqs if r["data"]["kind"] == KIND]
    assert len(notices) == 1 and notices[0]["data"]["reason"] == "accepted" and notices[0]["data"]["minutes"] == 5
    assert hub.mock_plug.state.get("speaker", {}).get("speak", {}).get("text") == notices[0]["data"]["text"]
    assert envelope["data"]["sensor_summary"]["rest_notice"]["counts"]["notices"] == 1
    assert hub.report_envelope(t)["data"]["rest_notices"]["count"] == 1 and hub.report_envelope(t)["data"]["rest_notices"]["last_reason"] == "accepted"
    # 닫기 응답은 ESM·제어로 안 가고 알림기에만
    cache.put_feedback({"verdict": "dismiss", "request_id": notices[0]["data"]["request_id"]})
    cache.put("mmwave", mm(t + 10, level=40)); cache.put("env", env(t + 10, co2=700))
    hub.tick_once(t + 10)
    assert hub.rest_notifier.counts["dismissed"] == 1 and "알 수 없는 verdict" not in hub.out.getvalue()
    assert len([r for r in reqs if r["data"]["kind"] == KIND]) == 1                 # REST 유지 중 반복 없음


def test_live_forced_rest_after_failed_interventions_notifies_escalated(tmp_path):
    hub, cache, reqs, _ = _hub(tmp_path)
    t = _settle_focus(hub, cache)
    hub.engine.state = State.ESCALATE
    hub.engine._state_since = t
    hub.engine._remaining_options = []
    hub.engine._last_cause = "environment"
    cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700))
    hub.tick_once(t)
    assert hub.engine.state is State.REST
    notices = [r for r in reqs if r["data"]["kind"] == KIND]
    assert len(notices) == 1 and notices[0]["data"]["reason"] == "escalated" and notices[0]["data"]["cause"] == "environment"


@pytest.mark.parametrize("profile", ["demo-b", "demo-b-study"])
def test_live_demo_asleep_retry_rest_notifies_with_profile_wording(tmp_path, profile):
    hub, cache, reqs, _ = _hub(tmp_path, profile)
    assert hub.control_cfg["rest_notice"]["cooldown_sec"] == 30 and hub.control_cfg["rest_notice"]["reminder_after_sec"] == 120
    t = _settle_focus(hub, cache)
    hub.engine.state = State.ESCALATE                                   # 엎드린 채 격상 → asleep_retry: rest → 바로 강제 휴식
    hub.engine._state_since = t
    hub.engine._last_cause = "posture"
    cache.put("mmwave", mm(t, level=5)); cache.put("env", env(t, co2=700)); cache.put("tof_service", svc(t, posture="face_down", conf=1.0))
    hub.tick_once(t)
    assert hub.engine.state is State.REST
    notices = [r for r in reqs if r["data"]["kind"] == KIND]
    assert len(notices) == 1
    d = notices[0]["data"]
    assert d["reason"] == "asleep" and d["minutes"] == 1 and d["rest_min_sec"] == 60
    assert d["variant"] == ("study" if profile == "demo-b-study" else "default")
    assert ("기지개" in d["text"]) == (profile == "demo-b-study")
    assert hub.mock_plug.state["speaker"]["speak"]["text"] == d["text"]
    # 시연은 휴식이 60 s 라 리마인더(120 s)보다 먼저 끝난다 — 계속 엎드려 있으면 REST→RECOVERY→ESCALATE→REST 로 다시 들어오고,
    # 재진입은 쿨다운 30 s 뒤라 한 번 더 알린다(헛반복 없음). 리마인더 자체는 단위 테스트가 본다.
    for dt in range(10, 130, 10):
        cache.put("mmwave", mm(t + dt, level=5)); cache.put("env", env(t + dt, co2=700)); cache.put("tof_service", svc(t + dt, posture="face_down", conf=1.0))
        hub.tick_once(t + dt)
    kinds = [r["data"]["reason"] for r in reqs if r["data"]["kind"] == KIND]
    assert kinds[0] == "asleep" and "reminder" not in kinds and 1 <= len(kinds) <= 2, kinds


def test_live_sound_pref_off_sends_card_only(tmp_path):
    hub, cache, reqs, _ = _hub(tmp_path)
    t = _settle_focus(hub, cache)
    hub.personalize_recorder.user_profile = {"sound_pref": "off"}
    hub.engine.state = State.ESCALATE
    hub.engine._state_since = t
    hub.engine._remaining_options = []
    hub.engine._last_cause = "cognitive"
    cache.put("mmwave", mm(t, level=40)); cache.put("env", env(t, co2=700))
    hub.tick_once(t)
    notices = [r for r in reqs if r["data"]["kind"] == KIND]
    assert len(notices) == 1 and notices[0]["data"]["speak"] is False and "speaker" not in hub.mock_plug.state
