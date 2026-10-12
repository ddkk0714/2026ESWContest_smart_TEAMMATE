"""펌웨어 TofPosture.cpp 를 PC 에서 돌리는 재판정기. 컴파일러가 없으면 건너뛴다."""
import shutil

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(shutil.which("g++") is None and shutil.which("wsl.exe") is None,
                                reason="g++/WSL 없음")


def test_empty_scene_is_away_and_config_override_is_accepted():
    from tofsim.rule import RuleReplay

    rr = RuleReplay()
    d = np.full((40, 8, 8), 1600, np.int16)
    st = np.full((40, 8, 8), 5, np.uint8)
    out = rr.run(np.arange(40) * 67, d, st, config={"lean_forward_mm": 60}, cmds={0: "g"})
    assert (out["posture"] == "away").all() and (out["present"] == 0).all()


def test_unknown_config_key_fails():
    from tofsim.rule import RuleReplay

    with pytest.raises(RuntimeError):
        RuleReplay().run(np.arange(2), np.zeros((2, 8, 8), np.int16), np.zeros((2, 8, 8), np.uint8),
                         config={"no_such_field": 1})
