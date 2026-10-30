"""화면 카드는 한 번에 하나 — 우선순위 FSM ACTION 카드 > CO₂ vent_suggest > 반응 학습 checkin_suggest (2026-10-25).

실제 규칙·모델을 돌리지 않고 LiveHub 의 연결만 본다: 환기 규칙과 확인 카드를 '낼 수 있음' 으로 바꿔 두고, 어떤 카드가 떠 있을 때
다른 카드가 나가는지 확인한다. 프로필 id 는 identity.active_profile_id 한 곳에서 온다.
"""
from __future__ import annotations

import io

from deskmate_hub.identity import active_profile_id
from deskmate_hub.ingest import SensorCache
from deskmate_hub.learning import load_learning_config
from deskmate_hub.live import LiveHub
from deskmate_hub.personalize import load_personalize_config


def _hub():
    reqs = []
    hub = LiveHub(SensorCache(), out=io.StringIO(), profile="", control_cfg={"enabled": False}, publish_request=reqs.append)
    assert hub.learner is not None and hub.co2_window.enabled            # Pi 4 기본: 둘 다 켜짐
    assert hub.learning_cfg["probe"] is True                              # 10-25: 확인 카드 켬
    # 환기 규칙·확인 카드를 '조건만 맞으면 낸다' 로 바꾼다(실제 CO₂·모델 판단은 각자의 테스트가 본다)
    hub.co2_window.observe_view = lambda view, now, cfg, *, present, state, blocked: (
        None if blocked else {"minutes": 10, "why": ["co2_high"], "explanation": "test", "co2_avg": 1500})
    learner = hub.learner

    def fake_checkin(result, frame, now, *, busy, request_id):
        if busy or learner.probe is not None:
            return None
        learner.probe = {"t": now, "x": [], "c0": 0.5, "request_id": request_id, "expires": 120}
        return {"request_id": request_id, "kind": "checkin_suggest", "prompt_code": "checkin_tired",
                "options": ["accept", "reject"], "expires_in_s": 120, "evidence": ["learning:checkin"],
                "cause": "learning", "c_fatigue": 0.5, "p_model": 0.5}
    learner.maybe_checkin = fake_checkin
    return hub, reqs


def kinds(reqs):
    return [r["data"]["kind"] for r in reqs]


def test_vent_card_beats_checkin_and_only_one_card_at_a_time():
    hub, reqs = _hub()
    hub.tick_once(0.0)
    assert kinds(reqs) == ["vent_suggest"] and hub.pending_request["data"]["kind"] == "vent_suggest"
    assert hub.learner.probe is None                                      # 환기 카드가 떠 있으면 확인 카드는 안 낸다
    hub.tick_once(10.0)
    assert kinds(reqs) == ["vent_suggest"]                                # 카드가 떠 있는 동안 또 내지 않는다


def test_checkin_card_blocks_vent_card_until_it_closes():
    hub, reqs = _hub()
    hub.co2_window.observe_view = lambda *a, **k: None                    # 처음엔 환기 조건 없음
    hub.tick_once(0.0)
    assert kinds(reqs) == ["checkin_suggest"] and hub.learner.probe is not None and hub.pending_request is None
    hub.co2_window.observe_view = lambda view, now, cfg, *, present, state, blocked: (
        None if blocked else {"minutes": 10, "why": ["co2_high"], "explanation": "test", "co2_avg": 1500})
    hub.tick_once(10.0)
    assert kinds(reqs) == ["checkin_suggest"]                             # 확인 카드가 떠 있으면 환기 카드도 안 낸다
    hub.learner.probe = None                                              # 확인 카드가 닫히면(응답·만료) 환기 카드가 나간다
    hub.tick_once(20.0)
    assert kinds(reqs) == ["checkin_suggest", "vent_suggest"]


def test_open_card_blocks_new_vent_and_checkin_until_answer_or_expiry():
    """PC 리허설 10-25 카드 겹침: FSM 자동 진입이 pending_request 를 비워도 앱의 카드는 남는다 — 그 카드가 응답·만료되기 전엔
    vent_suggest·checkin_suggest 를 새로 내지 않고 다음 기회를 기다린다. expires_in_s(환기 180 s)가 지나면 다시 낸다."""
    hub, reqs = _hub()
    hub.tick_once(0.0)
    assert kinds(reqs) == ["vent_suggest"] and reqs[0]["data"]["expires_in_s"] == 180
    hub.pending_request = None                                            # FSM 자동 진입(_maybe_request 의 gate ≠ suggest)과 같은 효과
    hub.tick_once(70.0)
    assert kinds(reqs) == ["vent_suggest"] and hub.learner.probe is None  # 70 s 뒤: 카드가 아직 떠 있다 → 확인·환기 모두 안 낸다
    hub.tick_once(170.0)
    assert kinds(reqs) == ["vent_suggest"]
    hub.tick_once(190.0)                                                  # 만료(180 s) 뒤에는 낸다
    assert kinds(reqs) == ["vent_suggest", "vent_suggest"]
    # 응답이 오면 바로 닫힌다(환기 조건은 그 사이 사라졌다고 둔다 — 새 카드가 바로 나오지 않게)
    hub.co2_window.observe_view = lambda *a, **k: None
    hub.cache.put_feedback({"verdict": "reject", "request_id": hub.pending_request["data"]["request_id"]})
    hub.tick_once(200.0)
    # 거절로 자리가 비자 같은 tick 에 확인 카드가 들어온다(= 환기 카드가 닫혔다는 뜻). 환기 카드 기록은 지워졌다
    assert hub.pending_request is None and kinds(reqs) == ["vent_suggest", "vent_suggest", "checkin_suggest"]
    assert hub._open_card is not None and hub._open_card[0] == "checkin_suggest" and hub.learner.probe is not None


def test_fsm_env_card_blocks_vent_until_it_expires():
    hub, reqs = _hub()
    hub.pending_request = {"ts": 0.0, "data": {"request_id": "fsm-env", "kind": "env_suggest", "cause": "environment",
                                             "expires_in_s": 180}}
    hub._mark_card(0.0, 180, "env_suggest")
    hub.tick_once(10.0)
    assert kinds(reqs) == [] and hub.learner.probe is None
    hub.tick_once(190.0)                                                  # 허브 만료 → 다음 tick 부터 환기 카드 가능
    assert kinds(reqs) == ["vent_suggest"]


def test_fsm_action_card_blocks_both():
    hub, reqs = _hub()
    hub.pending_request = {"ts": -100.0, "data": {"request_id": "fsm-1", "kind": "posture_suggest", "cause": "posture"}}
    hub.tick_once(0.0)
    assert kinds(reqs) == [] and hub.learner.probe is None and hub.pending_request["data"]["kind"] == "posture_suggest"


def test_profile_id_comes_from_one_place():
    assert active_profile_id() == "default" and active_profile_id({}, None, {"profile_id": ""}) == "default"
    assert active_profile_id({"profile_id": "kim"}, {"profile_id": "lee"}) == "kim"
    assert active_profile_id({"profile_id": " a/b "}) == "ab"
    hub, _ = _hub()
    assert hub.profile_id == active_profile_id(load_learning_config(), load_personalize_config()) == "default"
    assert hub.learner.c["profile_id"] == hub.profile_id
    assert hub.learner._profile_path("logs/profiles/{profile_id}/reaction.json") == "logs/profiles/default/reaction.json"
