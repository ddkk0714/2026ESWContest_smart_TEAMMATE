"""펌웨어 줄 형식과 프로토콜 일정."""
import pytest

from tofl.lines import parse_line
from tofl.protocol import LABELS, RULE_CLASS, Schedule, Step, load_config

# firmware/esp32_sensor_node/test/test_tof_line/test_tof_line.cpp 의 kGoldenLine 과 같은 문자열.
GOLDEN = ('{"t":"tof","seq":7,"ms":1234,"w":2,"h":2,"d":[612,-1,1500,700],'
          '"st":[5,255,9,6],"rule":{"present":true,"posture":"upright","conf":0.83,'
          '"baseline":true,"head_mm":612,"torso_mm":null,"drop_mm":4,"chin":0.00,'
          '"motion":0.05,"nods":1}}\n')


def test_parses_firmware_golden_line():
    obj = parse_line(GOLDEN.encode())
    assert obj["seq"] == 7 and obj["d"] == [612, -1, 1500, 700] and obj["st"][1] == 255
    assert obj["rule"]["posture"] == "upright" and obj["rule"]["torso_mm"] is None


@pytest.mark.parametrize("text", [
    "ets Jun  8 2016 00:22:57",                            # 부팅 로그
    '{"t":"mmwave","ms":1}',                               # 다른 센서 줄
    GOLDEN[:60],                                           # 잘린 줄
    GOLDEN.replace('"d":[612,-1,1500,700]', '"d":[612]'),  # 배열 길이 불일치
    GOLDEN.replace(',"rule":{', ',"x":{'),                 # rule 없음
    "",
])
def test_rejects_garbage(text):
    assert parse_line(text) is None


def test_hello_and_error_pass_through():
    assert parse_line('{"t":"tof_hello","ms":5,"sensor":"VL53L8CX","w":8,"h":8}')["sensor"] == "VL53L8CX"
    assert parse_line(b'{"t":"tof_err","ms":9,"where":"init","code":66}\r\n')["code"] == 66


def test_schedule_transition_then_step():
    sch = Schedule([Step("away", 5, "비켜", "g"), Step("upright", 10, "바르게", "b")], 3)
    assert sch.total_s == 21
    n = sch.at(1.0)
    assert n.phase == "transition" and n.prompt is None and n.index == 0
    n = sch.at(3.5)
    assert n.phase == "step" and n.prompt == "away" and n.since_s == pytest.approx(0.5)
    assert sch.at(9.0).phase == "transition" and sch.at(9.0).index == 1
    assert sch.at(12.0).prompt == "upright"
    assert sch.at(21.0) is None


def test_schedule_rejects_unknown_label_and_cmd():
    with pytest.raises(ValueError):
        Schedule([Step("slouch", 5, "x")], 3)
    with pytest.raises(ValueError):
        Schedule([Step("upright", 5, "x", "z")], 3)


def test_default_config_is_valid_and_covers_rule_classes():
    sch = Schedule.from_config(load_config())
    used = {s.label for s in sch.steps}
    # 규칙 enum 중 present 상태 자세는 전부 프로토콜에 있어야 규칙을 채점할 수 있다
    assert {v for v in RULE_CLASS.values() if v} <= used
    assert set(RULE_CLASS) == set(LABELS)
    assert sch.steps[0].cmd == "g" and sch.steps[1].cmd == "b"
    assert 5 * 60 < sch.total_s < 8 * 60
