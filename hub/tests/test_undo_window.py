"""종료된 자동 실행 episode도 설정된 짧은 창 안에서는 되돌린다."""
from __future__ import annotations

from deskmate_hub.control import ControlDispatcher
from deskmate_hub.inference import GateMode


def setup():
    sent = []
    cfg = {"result_timeout_sec": 15, "suggest_timeout_sec": 180, "cooldown_sec": 0,
           "undo_window_sec": 60, "irreversible_operations": [],
           "actions": {"environment": [{"target_id": "fan", "operation": "set_power",
                                        "value": "on", "undo_value": "off"}]}}
    return ControlDispatcher(cfg, publish_cmd=sent.append), sent


def finished_auto(dispatcher):
    episode = dispatcher.on_enter_action("ACTION_ENV", "environment", GateMode.AUTO, 100)
    dispatcher.on_result({"command_id": episode.commands[0].command_id, "status": "succeeded"}, 101)
    dispatcher.close_episode()
    return episode


def test_reject_undoes_closed_auto_episode_within_window_once():
    dispatcher, sent = setup()
    episode = finished_auto(dispatcher)
    assert episode.executed_ts == 100
    dispatcher.on_feedback("reject", 130)
    assert episode.outcome == "undone"
    assert len(sent) == 2 and sent[-1]["gate"] == "undo" and sent[-1]["value"] == "off"
    dispatcher.on_feedback("reject", 131)
    assert len(sent) == 2


def test_reject_after_window_does_not_undo():
    dispatcher, sent = setup()
    episode = finished_auto(dispatcher)
    dispatcher.on_feedback("reject", 190)
    assert episode.outcome == "executed" and len(sent) == 1


def test_suggest_reject_skips_current_instead_of_undoing_history():
    dispatcher, sent = setup()
    old = finished_auto(dispatcher)
    suggestion = dispatcher.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST, 110)
    dispatcher.on_feedback("reject", 120)
    assert suggestion.outcome == "rejected" and old.outcome == "executed"
    assert len(sent) == 1


def test_timeout_leaves_suggestion_for_dispatcher_timer():
    dispatcher, sent = setup()
    suggestion = dispatcher.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST, 100)
    dispatcher.on_feedback("timeout", 110)
    assert suggestion.awaiting_user and sent == []
    dispatcher.tick(281)
    assert suggestion.outcome == "expired"
