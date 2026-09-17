import json

from deskmate_hub.control.ilink_light import (
    brightness,
    color_temperature,
    plan_control_command,
    plan_phase_feedback,
    power,
    rgb,
    status_request,
    IlinkMqttController,
    TOPIC_STATE,
)


def test_known_ilink_frames_match_verified_protocol():
    assert power(True).hex(" ") == "55 aa 01 08 05 01 f1"
    assert power(False).hex(" ") == "55 aa 01 08 05 00 f2"
    assert brightness(255).hex(" ") == "55 aa 01 08 01 ff f7"
    assert rgb(255, 0, 0).hex(" ") == "55 aa 03 08 02 ff 00 00 f4"
    assert color_temperature(1).hex(" ") == "55 aa 01 08 09 01 ed"
    assert status_request().hex(" ") == "55 aa 01 08 15 06 dc"


def test_phase_feedback_is_yaml_driven_and_unknown_phase_is_safe():
    config = {"phase_feedback": {"fatigue": {"power": True, "brightness": 42, "rgb": [1, 2, 3]}}}
    frames = plan_phase_feedback({"data": {"phase": "fatigue"}}, config)
    assert frames == [power(True), brightness(42), rgb(1, 2, 3)]
    assert plan_phase_feedback({"data": {"phase": "unknown"}}, config) == []


def test_explicit_desk_lamp_commands_are_validated():
    assert plan_control_command({"data": {"target": "desk_lamp", "cmd": "set_brightness", "value": 25}}) == [power(True), brightness(25)]
    assert plan_control_command({"data": {"target": "vent_fan", "cmd": "set_brightness", "value": 25}}) == []


def test_controller_is_disabled_by_default(monkeypatch):
    controller = IlinkMqttController(
        {"phase_feedback": {"focus": {"power": True, "brightness": 10}}},
    )
    called = False

    def unexpected_run(coro):
        nonlocal called
        called = True
        coro.close()

    monkeypatch.setattr("deskmate_hub.control.ilink_light.asyncio.run", unexpected_run)
    controller.handle(TOPIC_STATE, json.dumps({"data": {"phase": "focus"}}).encode())
    assert called is False