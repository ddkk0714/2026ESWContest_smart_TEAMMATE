"""밴딧이 고른 arm 이 실제로 실행된다 (2026-10-26) — 기기 없는 arm 은 카드·speak·chime 으로 전달, 기본 램프는 정책 결정이 없을 때만.

10-26 보드: ACTION_POSTURE 에서 sound_cue·posture_card 를 골랐는데 AUTO 라 카드가 안 나가고 기본 램프가 나갔다(기록만 하는 개인화).
"""
from __future__ import annotations

import copy
import io

import pytest

from deskmate_hub.arm_notice import ArmDeliverer, DEFAULTS
from deskmate_hub.inference import GateMode, State, load_config
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import InterventionPolicy, Situation, load_personalize_config

from test_ingest_mapping import env, mm
from test_posture_actions import CFG as CONTROL_CFG, _drive_to_monitor
from test_tof_service import svc

STUDENT = {"light_pref": "high", "sound_pref": "low", "wind_pref": "off"}      # 수험생 온보딩: 밝은 조명 선호·소리 작게·바람 싫음


# ── 단위: 전달기 ─────────────────────────────────────────────────────────────────

def _deliverer(cfg=None, control=True):
    reqs, cmds = [], []
    d = ArmDeliverer(cfg, boot_id="b", publish_request=reqs.append, publish_control=cmds.append if control else None)
    return d, reqs, cmds


def test_auto_sound_arm_sends_info_card_and_chime():
    d, reqs, cmds = _deliverer()
    out = d.deliver(state="ACTION_POSTURE", kind="posture_suggest", prompt_code="fix_posture", gate="auto", arm="sound_cue",
                    label="알림음", channel="sound", explanation="졸음 3회 · 기본 정책", cause="posture", now=100.0, seq=5,
                    sound_pref="low", c_fatigue=0.7, expires_in_s=180)
    assert len(out) == 2 and len(reqs) == 1 and len(cmds) == 1
    c = reqs[0]["data"]
    assert c["kind"] == "posture_suggest" and c["auto"] is True and c["options"] == ["dismiss"] and c["request_id"] == "b-5-arm"
    assert c["arm"] == "sound_cue" and c["arm_label"] == "알림음" and c["channel"] == "sound" and c["chime"] is True and c["speak"] is False
    assert c["text"] == DEFAULTS["card_text"]["sound_cue"] and c["expires_in_s"] == 180 and c["explanation"] == "졸음 3회 · 기본 정책"
    assert cmds[0] == {"command_id": "b-5-chime", "target_id": "speaker", "operation": "chime",
                       "value": {"pattern": "double", "volume": "low"}, "origin_state": "ACTION_POSTURE", "cause": "posture",
                       "gate": "auto", "requires_confirmation": False, "expires_ts_ms": 280000}


def test_voice_arm_speaks_and_suggest_gate_adds_no_card():
    d, reqs, cmds = _deliverer()
    out = d.deliver(state="ACTION_POSTURE", kind="posture_suggest", prompt_code="fix_posture", gate="suggest", arm="posture_voice",
                    label="자세 음성 안내", channel="voice", explanation=None, cause="posture", now=100.0, seq=1, sound_pref=None)
    assert reqs == [] and len(cmds) == 1 and len(out) == 1          # 제안 카드는 live._maybe_request 가 이미 냈다
    assert cmds[0]["operation"] == "speak" and cmds[0]["value"] == {"text": DEFAULTS["voice_text"]["posture_voice"], "lang": "ko"}
    assert cmds[0]["gate"] == "suggest" and cmds[0]["command_id"] == "b-1-speak"


def test_sound_pref_off_sends_card_only_and_display_arm_has_no_sound():
    d, reqs, cmds = _deliverer()
    d.deliver(state="ACTION_POSTURE", kind="posture_suggest", prompt_code="fix_posture", gate="auto", arm="sound_cue",
              label="알림음", channel="sound", explanation=None, cause="posture", now=100.0, seq=1, sound_pref="off")
    assert len(reqs) == 1 and cmds == [] and reqs[0]["data"]["chime"] is False
    d.deliver(state="ACTION_BREAK", kind="break_suggest", prompt_code="take_a_break", gate="auto", arm="posture_card",
              label="자세 안내 카드", channel="display", explanation=None, cause="posture", now=110.0, seq=2, sound_pref="high")
    assert len(reqs) == 2 and cmds == [] and reqs[1]["data"]["text"] == "자세를 바로 해 볼까요?"
    d.on_feedback("dismiss", reqs[1]["data"]["request_id"], 111.0)
    assert d.counts == {"cards": 2, "speaks": 0, "chimes": 0, "dismissed": 1} and d.open_request_id is None


# ── 정책: 수험생 prior + 졸음 → wake_light 가 거의 항상 이긴다 ──────────────────────────────

def _sit(drowsy=True) -> Situation:
    return Situation(ts=1_000_000.0, hour_local=15.5, weekday=2, month=10, posture_dist={"face_down": 1.0}, posture_label="face_down",
                     posture_hold_min=2.0, slump_hold_min=2.0, present=True, drowsy=drowsy, drowsy_count_30min=1,
                     co2_ppm=800, lux=300, temp_c=24.0, humidity_pct=50, pc_ratio=0.1, typing_active=False,
                     c_fatigue=0.80, c_focus=0.3, session_elapsed_min=30)


def test_student_prior_with_low_exploration_picks_wake_light_under_drowsiness():
    """10-26 측정(200 시드, 수험생 prior·졸음): explore 1.0 → wake_light 58 / sound_cue 55 / card 45 / voice 42,
    0.2 → wake_light 83 / sound_cue 50 / card 37 / voice 30, 0.05 → 147 / 30 / 17 / 6, greedy → wake_light 확정(0.55 > 0.35 > 0.27).
    시연 프로파일 0.2 에서 wake_light 가 1위(다수)는 되지만 과반은 아니다 — 확정이 필요하면 greedy 또는 0.05."""
    from collections import Counter
    from deskmate_hub.personalize.linalg import PurePRNG

    cfg = load_personalize_config()
    cfg["cold_start"] = dict(cfg["cold_start"], explore_scale_global=0.2)        # 시연 프로파일 값
    wins = Counter()
    for seed in range(120):
        p = InterventionPolicy(cfg, rng=PurePRNG(seed))
        p.apply_user_profile(STUDENT)
        d = p.decide(_sit(), cause="posture", gate="auto", session_id=f"s{seed}")
        assert d.source == "bandit"
        wins[d.arm] += 1
    assert wins.most_common(1)[0][0] == "wake_light" and wins["wake_light"] >= 36, dict(wins)    # 1위, 30 % 이상
    greedy = copy.deepcopy(cfg)
    greedy["bandit"]["algorithm"] = "greedy"
    p = InterventionPolicy(greedy)
    p.apply_user_profile(STUDENT)
    d = p.decide(_sit(), cause="posture", gate="auto", session_id="g")
    assert d.arm == "wake_light" and d.scores["wake_light"] == pytest.approx(0.55, abs=0.01)     # 평균만 쓰면 확정
    assert d.scores["sound_cue"] == pytest.approx(0.35, abs=0.01) and d.scores["posture_card"] == pytest.approx(0.267, abs=0.01)
    # 온보딩이 없으면 평균으로도 sound_cue(0.55) 가 wake_light(0.45) 를 이긴다 — 시연은 온보딩을 먼저 보내야 한다
    p0 = InterventionPolicy(greedy)
    assert p0.decide(_sit(), cause="posture", gate="auto", session_id="n").arm == "sound_cue"


def test_default_exploration_lets_noise_override_prior():
    """보드 기본(explore 1.0)에서는 같은 상황에서도 sound_cue·posture_card 가 자주 뽑힌다 — 10-26 보드 관측의 재현."""
    from deskmate_hub.personalize.linalg import PurePRNG
    cfg = load_personalize_config()
    others = 0
    for seed in range(40):
        p = InterventionPolicy(cfg, rng=PurePRNG(seed))
        p.apply_user_profile(STUDENT)
        others += p.decide(_sit(), cause="posture", gate="auto", session_id=f"s{seed}").arm != "wake_light"
    assert others >= 8, others


# ── LiveHub: 기기 없는 arm 은 카드(+소리)로, 램프는 안 켜진다 / 기기 arm 은 램프 ────────────────────

def _hub_only(tmp_path, only: tuple[str, ...], *, sound_pref=None, profile="demo-b"):
    cache, reqs, states = SensorCache(), [], []
    pcfg = dict(load_personalize_config(), log_dir=str(tmp_path))
    pcfg["bandit"] = dict(pcfg["bandit"], algorithm="greedy")
    pcfg["arms"] = [a for a in pcfg["arms"] if a["id"] in only or a.get("rule_only")]
    hub = LiveHub(cache, control_cfg=CONTROL_CFG, publish=states.append, publish_request=reqs.append, out=io.StringIO(),
                  profile=profile, personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"))
    if sound_pref is not None:
        hub.personalize_recorder.user_profile = {"sound_pref": sound_pref}
    return hub, cache, reqs, states


@pytest.mark.parametrize("arm,channel,op", [("sound_cue", "sound", "chime"), ("posture_voice", "voice", "speak"), ("posture_card", "display", None)])
def test_live_auto_deviceless_arm_is_delivered_without_lamp(tmp_path, arm, channel, op):
    hub, cache, reqs, states = _hub_only(tmp_path, (arm,), sound_pref="mid")
    seen, accepted = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen and accepted == 0, seen[-8:]
    assert "desk_lamp" not in hub.mock_plug.state                                      # 기본 램프는 안 나간다
    cards = [r for r in reqs if r["data"].get("auto") and r["data"]["kind"] == "posture_suggest"]
    assert len(cards) == 1 and cards[0]["data"]["arm"] == arm and cards[0]["data"]["channel"] == channel
    speaker = hub.mock_plug.state.get("speaker")
    if op is None:
        assert speaker is None
    else:
        assert speaker is not None and op in speaker and speaker[op] is not None
    assert hub.control.episode is None and not any(e.state == "ACTION_POSTURE" for e in hub.control.history)
    assert hub.arm_deliverer.counts["cards"] == 1


def test_live_sound_pref_off_delivers_card_only(tmp_path):
    hub, cache, reqs, _ = _hub_only(tmp_path, ("sound_cue",), sound_pref="off")
    seen, _ = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen
    assert "speaker" not in hub.mock_plug.state and "desk_lamp" not in hub.mock_plug.state
    assert [r["data"]["arm"] for r in reqs if r["data"].get("auto")] == ["sound_cue"]


def test_live_device_arm_still_runs_lamp_and_policy_off_uses_default(tmp_path):
    hub, cache, reqs, _ = _hub_only(tmp_path, ("wake_light",))
    seen, _ = _drive_to_monitor(hub, cache, co2=2000)
    assert State.ACTION_POSTURE in seen and hub.mock_plug.state.get("desk_lamp") == {"adjust_brightness": {"percent": 60, "min_raw": 153}, "brightness": 176}
    assert not [r for r in reqs if r["data"].get("auto")]
    # 정책을 끄면 예전처럼 control.yaml 기본 램프
    cache2, reqs2, states2 = SensorCache(), [], []
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub2 = LiveHub(cache2, control_cfg=CONTROL_CFG, publish=states2.append, publish_request=reqs2.append, out=io.StringIO(),
                   profile="demo-b", personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p2.jsonl"))
    seen2, _ = _drive_to_monitor(hub2, cache2, co2=2000)
    assert State.ACTION_POSTURE in seen2 and hub2.mock_plug.state.get("desk_lamp", {}).get("brightness") == 176


def test_demo_profiles_use_greedy_and_board_keeps_thompson():
    from deskmate_hub.profile import apply_section
    for name in ("demo-a", "demo-b", "demo-b-study"):
        p = apply_section(load_personalize_config(), "personalize", name)
        assert p["bandit"]["algorithm"] == "greedy" and p["cold_start"]["explore_scale_global"] == 1.0
    assert load_personalize_config()["bandit"]["algorithm"] == "thompson"


COLLEGE = {"light_pref": "mid", "sound_pref": "mid", "wind_pref": "mid"}   # 대학생(가정: 중립 선호 — devview 프리셋이 다르면 여기 맞출 것)


@pytest.mark.parametrize("persona,profile,expected", [("수험생", STUDENT, "wake_light"), ("대학생", COLLEGE, "sound_cue")])
def test_demo_greedy_persona_under_drowsiness_picks_distinct_device(persona, profile, expected):
    """시연(greedy)에서 온보딩 prior 가 기기를 결정한다 — 수험생(밝은 조명·소리 작게)은 wake_light, 중립 선호 대학생은 sound_cue(알림음)."""
    from deskmate_hub.profile import apply_section
    cfg = apply_section(load_personalize_config(), "personalize", "demo-b")
    p = InterventionPolicy(cfg)
    p.apply_user_profile(profile)
    d = p.decide(_sit(), cause="posture", gate="auto", session_id=persona)
    assert d.source == "bandit" and d.arm == expected, (persona, d.arm, d.scores)
    # 같은 입력이면 같은 답(결정적) — 새 정책 인스턴스로 다시(같은 인스턴스는 방금 실행한 arm 이 cooldown_sec 300 동안 후보에서 빠진다)
    q = InterventionPolicy(cfg)
    q.apply_user_profile(profile)
    assert q.decide(_sit(), cause="posture", gate="auto", session_id=persona + "2").arm == expected
    # 깨어 있으면 둘 다 자세 카드
    assert p.decide(_sit(False), cause="posture", gate="auto", session_id=persona + "3").arm == "posture_card"
