"""11-03 결선 대본(8분) — 빠른 시연에서 같은 자세에 사람마다 다른 기기가 나가는가(demo_wake.py, control.yaml demo_wake:).

A(태환, 도움 순서 알림 › 조명 › 선풍기): 뒤로 젖히기 → 음악, 졸기(엎드림) → 음악+조명, 거북목 → 바른 자세 안내, 자리 비움 → 전부 끄기.
B(민경, 조명 › 선풍기 › 알림): 뒤로 젖히기 → 조명, 다시 젖히기 → 조명, 졸기 → 조명+선풍기, 턱 괴기 → 안내, 자리 비움 → 끄기.
B 가 앉을 때 A 의 기기를 되살리지 않는다. 보드처럼 개인화 정책은 끄고(DESKMATE_PERSONALIZE=off = 기록만) 돌린다.
"""
from __future__ import annotations

import io

import pytest

from deskmate_hub.control import MockPlugAdapter, load_control_config
from deskmate_hub.demo_wake import DemoWake
from deskmate_hub.ingest import SensorCache
from deskmate_hub.ingest.cache import Sample
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import load_personalize_config

from test_ingest_mapping import env, mm
from test_posture_actions import CFG as CONTROL_CFG
from test_tof_service import svc

A = {"profile_id": "pA", "light_pref": "mid", "sound_pref": "low", "wind_pref": "off", "focus_help": ["sound", "light", "wind"]}
B = {"profile_id": "pB", "light_pref": "mid", "sound_pref": "low", "wind_pref": "off", "focus_help": ["light", "wind", "sound"]}
REAL = load_control_config()


def _warm(w: DemoWake, rise: float) -> DemoWake:
    """방 온도를 관측: 25.0 °C 에서 rise 만큼 오른 상태로 둔다."""
    w.observe_temp(25.0, 0.0)
    w.observe_temp(25.0 + rise, 60.0)
    return w


def test_rule_picks_by_help_order_and_depth():
    w = DemoWake(REAL["demo_wake"])
    assert w.decide("recline", A["focus_help"])["helps"] == ["sound"]
    assert w.decide("recline", B["focus_help"])["helps"] == ["light"]
    assert w.decide("face_down", B["focus_help"])["helps"] == ["light", "sound"]                # 온도 관측 없음 → 선풍기 안 고름
    assert w.decide("forward_head", A["focus_help"])["kind"] == "notice"
    assert w.decide("upright", A["focus_help"]) is None
    assert w.decide("recline", None)["helps"] == ["light"]                 # 프로필 없음 → default_order
    assert w.decide("recline", ["break", "voice"]) is None                 # 기기 없는 도움만 → 원래 경로
    assert DemoWake({}).decide("recline", A["focus_help"]) is None         # 설정 없으면 꺼짐


def test_fan_only_when_room_temperature_rises():
    """11-05 사용자 "24도 말고 관측하다가 온도 상승하면 그때" — 절대 온도가 아니라 최근 10분 최저 대비 상승(0.5 °C)."""
    assert _warm(DemoWake(REAL["demo_wake"]), 0.6).decide("face_down", B["focus_help"])["helps"] == ["light", "wind"]
    assert _warm(DemoWake(REAL["demo_wake"]), 0.2).decide("face_down", B["focus_help"])["helps"] == ["light", "sound"]
    # 원인 자세 + 환경(온도 상승): 자세 몫(도움 순서)에 선풍기를 더한다 — 사용자 "자세 + 환경이면 조명과 선풍기 둘 다"
    assert _warm(DemoWake(REAL["demo_wake"]), 0.6).decide("face_down", A["focus_help"])["helps"] == ["sound", "light", "wind"]
    assert _warm(DemoWake(REAL["demo_wake"]), 0.6).decide("recline", B["focus_help"])["helps"] == ["light", "wind"]
    assert _warm(DemoWake(REAL["demo_wake"]), 0.6).decide("recline", A["focus_help"])["helps"] == ["sound", "wind"]
    p = _warm(DemoWake(REAL["demo_wake"]), 0.6).decide("forward_head", A["focus_help"])
    assert p["kind"] == "notice" and p["helps"] == ["wind"] and p["causes"] == ["posture", "environment"]
    assert _warm(DemoWake(REAL["demo_wake"]), 0.6).decide("upright", A["focus_help"])["helps"] == ["wind"]   # 더위만
    assert _warm(DemoWake(REAL["demo_wake"]), 0.2).decide("upright", A["focus_help"]) is None
    hot = DemoWake(REAL["demo_wake"])                                       # 처음부터 30 °C 여도 오르지 않았으면 안 고른다
    hot.observe_temp(30.0, 0.0); hot.observe_temp(30.0, 60.0)
    assert hot.decide("face_down", B["focus_help"])["helps"] == ["light", "sound"]
    old = DemoWake(REAL["demo_wake"])                                       # 10분 넘은 낮은 값은 잊는다
    old.observe_temp(24.0, 0.0); old.observe_temp(25.0, 700.0)
    assert old.temp_rise() == 0.0
    w = _warm(DemoWake(REAL["demo_wake"]), 0.6)
    assert all(s["ignore_cooldown"] for s in w.decide("face_down", B["focus_help"])["specs"])
    assert w.snapshot()["temp_rise_c"] == 0.6


@pytest.fixture
def sent(monkeypatch):
    log: list[dict] = []
    orig = MockPlugAdapter.handle_command

    def record(self, payload):
        log.append(payload)
        return orig(self, payload)

    monkeypatch.setattr(MockPlugAdapter, "handle_command", record)
    return log


def _hub(tmp_path):
    cache, reqs = SensorCache(), []
    cfg = dict(CONTROL_CFG, demo_wake=REAL["demo_wake"], absent_off=REAL["absent_off"], arm_notice=REAL.get("arm_notice"))
    pcfg = dict(load_personalize_config(), enabled=False, log_dir=str(tmp_path))
    hub = LiveHub(cache, control_cfg=cfg, publish=lambda e: None, publish_request=reqs.append, out=io.StringIO(),
                  profile="demo-b", personalize_cfg=pcfg, personalize_log_path=str(tmp_path / "p.jsonl"),
                  personalize_policy_path=str(tmp_path / "policy.json"))
    cache.put("demo_mode", Sample(received=0.0, ts=0.0, seq=None, data={"speed": "fast"}))
    return hub, cache, reqs


class Script:
    def __init__(self, hub, cache, reqs, sent):
        self.hub, self.cache, self.reqs, self.sent, self.t = hub, cache, reqs, sent, 0.0
        self.trace: list[tuple[float, str, str]] = []
        self.temp = 25.0                                                    # 방 온도(°C) — B 졸기 전에 올린다

    def person(self, profile):
        self.cache.put("user_profile", Sample(received=self.t, ts=self.t, seq=1, data=dict(profile)))

    def hold(self, posture: str, seconds: float, *, until=None):
        """posture 를 seconds 동안(또는 until(새 명령, 새 카드)이 참일 때까지). 이 구간에 나간 (기기, 동작, 값) 과 카드 arm."""
        n_cmd, n_req, end = len(self.sent), len(self.reqs), self.t + seconds
        away = posture == "away"
        while self.t < end:
            self.cache.put("mmwave", mm(self.t, present=not away, level=5, dist=120 if away else 55))
            self.cache.put("env", env(self.t, co2=700, temp=self.temp))
            self.cache.put("tof_service", svc(self.t, posture=posture, conf=1.0, present=not away))
            self.hub.tick_once(self.t)
            self.trace.append((self.t, posture, self.hub.engine.state.value))
            self.t += self.hub.period
            cmds = [(c["target_id"], c["operation"], c["value"]) for c in self.sent[n_cmd:]
                    if c["target_id"] != "speaker"]
            cards = [r["data"].get("arm") for r in self.reqs[n_req:] if (r.get("data") or {}).get("arm")]
            if until is not None and until(cmds, cards):
                break
        return cmds, cards


def _devices(cmds):
    return sorted({t for t, op, _ in cmds})


def test_script_a_then_b_fits_8_minutes(tmp_path, sent):
    hub, cache, reqs = _hub(tmp_path)
    s = Script(hub, cache, reqs, sent)
    acted = lambda cmds, cards: bool(cmds or cards)                      # noqa: E731

    s.person(A)
    s.hold("upright", 30)                                                 # 바르게 앉기 → 집중
    cmds, _ = s.hold("recline", 60, until=acted)
    assert _devices(cmds) == ["music"] and cmds[0][2] == {"on": True}, cmds
    s.hold("upright", 30)                                                 # 깨기
    cmds, _ = s.hold("face_down", 60, until=acted)
    assert _devices(cmds) == ["desk_lamp", "music"], "\n".join(f"{t:.0f} {p} {st}" for t, p, st in s.trace)   # 두 번째는 음악 + 조명
    s.hold("upright", 30)
    cmds, cards = s.hold("forward_head", 60, until=acted)
    assert cmds == [] and cards == ["posture_card"], (cmds, cards)        # 거북목 → 바른 자세 안내
    s.hold("upright", 30)
    cmds, _ = s.hold("away", 40, until=lambda c, _: len(c) >= 3)
    assert _devices(cmds) == ["desk_lamp", "music", "vent_fan"] and all(
        v in ("off", {"on": False}) for _, _, v in cmds), cmds          # 자리 비움 → 전부 끄기
    a_end = s.t

    s.person(B)
    cmds, _ = s.hold("upright", 30)
    assert cmds == [], cmds                                               # A 의 기기를 되살리지 않는다
    cmds, _ = s.hold("recline", 60, until=acted)
    assert _devices(cmds) == ["desk_lamp"], cmds                          # B 는 조명 먼저
    s.hold("upright", 30)
    cmds, _ = s.hold("recline", 60, until=acted)
    assert _devices(cmds) == ["desk_lamp"], cmds                          # 다시 조명(쿨다운에 안 막힘)
    s.hold("upright", 30)
    s.temp = 25.8                                                         # 방이 더워짐(0.8 °C 상승) → 선풍기
    cmds, _ = s.hold("face_down", 60, until=acted)
    assert _devices(cmds) == ["desk_lamp", "vent_fan"], cmds              # 졸기 → 조명 + 선풍기
    s.hold("upright", 30)
    cmds, cards = s.hold("chin_rest", 60, until=acted)
    # 방이 아직 더우므로(10분 안 0.8 °C 상승) 원인 자세 + 환경 → 안내 카드 + 선풍기(이미 켜져 있으면 그대로)
    assert _devices(cmds) == ["vent_fan"] and cards == ["posture_card"], (cmds, cards)
    s.hold("upright", 30)
    cmds, _ = s.hold("away", 40, until=lambda c, _: len(c) >= 3)
    assert _devices(cmds) == ["desk_lamp", "music", "vent_fan"], cmds

    # 빠른 시연 판정 주기 5 s(fsm.yaml demo_fast.period_sec)·깨운 뒤 바르게 30 s 기준. 센서 tick 시간 — 사람 동작·멘트 시간은 별도
    print(f"[demo_script] A {a_end:.0f}s, 전체 {s.t:.0f}s")
    assert hub.period == 5.0
    assert a_end <= 240 and s.t <= 480, (a_end, s.t)


def test_normal_speed_keeps_old_path(tmp_path, sent):
    """빠른 시연이 아니면 규칙이 끼어들지 않는다 — 뒤로 젖히기는 확정되지 않는다(demo-b 0.48 < 0.60)."""
    hub, cache, reqs = _hub(tmp_path)
    cache.put("demo_mode", Sample(received=0.0, ts=0.0, seq=None, data={"speed": "normal"}))
    s = Script(hub, cache, reqs, sent)
    s.person(A)
    s.hold("upright", 30)
    cmds, _ = s.hold("recline", 180)
    assert cmds == [] and hub.demo_wake.last is None, cmds


def test_fast_absent_turns_devices_off_within_5s_but_ignores_one_tick(tmp_path, sent):
    """11-04 사용자 "자리비움 한번 뜨고 5초 안으로 주변기기 다 꺼지게" — 빠른 시연은 부재 5 s(fsm.yaml demo_fast.absent_off_after_sec).
    한 tick 만 튄 부재(11-04 13:35:10 MLP away)는 다음 tick 이 재실이면 끄지 않는다."""
    hub, cache, reqs = _hub(tmp_path)
    s = Script(hub, cache, reqs, sent)
    s.person(A)
    s.hold("upright", 40)
    cmds, _ = s.hold("away", 5)                                           # 한 tick 부재
    assert cmds == [], cmds
    s.hold("upright", 10)
    first_away = s.t
    cmds, _ = s.hold("away", 30, until=lambda c, _: len(c) >= 3)
    assert _devices(cmds) == ["desk_lamp", "music", "vent_fan"], cmds
    assert s.t - first_away <= 10, (first_away, s.t)                      # 첫 부재 tick + 5 s 뒤 tick 에 끔


def test_notice_card_closes_and_focus_returns_after_sitting_up(tmp_path, sent):
    """11-05 사용자 "5초로 바꿔": 거북목 안내 카드는 바른 자세로 돌아온 첫 판정(5 s)에 닫히고, 깨운 뒤 지켜보기도 5 s 라
    곧 집중으로 돌아간다. 닫기 = 같은 request_id 를 expires_in_s 0.1 로 다시 보내기(앱 알림판이 같은 id 를 바꿔 끼운다)."""
    hub, cache, reqs = _hub(tmp_path)
    s = Script(hub, cache, reqs, sent)
    s.person(A)
    s.hold("upright", 30)
    _, cards = s.hold("forward_head", 60, until=lambda c, k: bool(k))
    assert cards == ["posture_card"]
    card_id = [r for r in reqs if (r.get("data") or {}).get("arm") == "posture_card"][-1]["data"]["request_id"]
    up_at = s.t
    closed_at = focus_at = None
    while s.t < up_at + 60 and (closed_at is None or focus_at is None):
        s.hold("upright", hub.period)
        if closed_at is None and any((r.get("data") or {}).get("request_id") == card_id
                                     and r["data"]["expires_in_s"] < 1 for r in reqs):
            closed_at = s.t
        if focus_at is None and hub.engine.state.value.startswith("FOCUS_"):
            focus_at = s.t
    assert closed_at is not None and closed_at - up_at <= 5.0, (up_at, closed_at)
    print(f"[demo_script] 카드 닫힘 +{closed_at - up_at:.0f}s, 집중 복귀 +{focus_at - up_at:.0f}s")
    assert focus_at is not None and focus_at - up_at <= 20.0, (up_at, focus_at)


def test_posture_and_heat_both_act_even_if_cause_flickers(tmp_path, sent):
    """11-05 실기: 젖힘으로 졸음 확정 → 원인 판단 tick 에 자세가 upright 로 흔들리고 방이 더워 원인이 환경으로 갔다
    → 대본 규칙을 건너뛰어 선풍기 명령이 없었다. 확정 순간의 자세를 기억해 첫 개입은 규칙대로(B: 조명 + 오른 온도면 선풍기)."""
    hub, cache, reqs = _hub(tmp_path)
    s = Script(hub, cache, reqs, sent)
    s.person(B)
    s.temp = 25.0
    s.hold("upright", 30)
    s.temp = 31.0                                                        # 6 °C 상승 — 환경 점수도 크다
    while hub.engine.state.value != "FATIGUE" and s.t < 200:
        s.hold("recline", hub.period)
    assert hub.engine.state.value == "FATIGUE", s.trace[-5:]
    cmds, _ = s.hold("upright", 30, until=lambda c, k: bool(c))           # 원인 판단 tick 부터 바른 자세로 흔들림
    states = [st for _, _, st in s.trace[-8:]]
    assert _devices(cmds) == ["desk_lamp", "vent_fan"], (cmds, states)   # 젖힘(자세) = 조명 + 더위(환경) = 선풍기
