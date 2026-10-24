"""posture_model smoothing: board — Pi 4 자세 서비스(hub/atlas-tof device_posture.cpp)의 융합·EMA 확정을 시뮬이 그대로 따르는지
고정한다. torch 없이 확정 단계(_BoardEma)·융합(fuse_mean_log)·설정 분기(make_gru)만 본다."""
from __future__ import annotations

import numpy as np
import pytest

from tools.posture_sim import posture_model as pm


def test_mean_log_fusion_keeps_argmax_and_is_less_peaked():
    a, b = np.array([0.7, 0.2, 0.1]), np.array([0.5, 0.4, 0.1])
    s, m = pm.fuse_log_softmax([a, b]), pm.fuse_mean_log([a, b])
    assert s.argmax() == m.argmax() == 0
    assert m.sum() == pytest.approx(1.0) and m.max() < s.max()      # 평균이라 합보다 덜 뾰족하다
    assert pm.fuse_mean_log([a]) == pytest.approx(a, abs=1e-5)       # 센서 하나면 그 확률 그대로


def test_board_ema_switches_after_seven_frames_at_one_second_and_ten_hz():
    """α = 1/(1 s × 10 Hz) = 0.1. 0.9 짜리 A 에서 0.9 짜리 B 로 바뀌면 A = 0.1 + 0.8·0.9ⁿ 이 0.5 아래로 내려가는 n = 7 번째 프레임에 바뀐다."""
    ema = pm._BoardEma(["A", "B"], alpha=0.1)
    pa, pb = np.array([0.9, 0.1]), np.array([0.1, 0.9])
    assert ema.push(pa) == ("A", pytest.approx(0.9), "A")            # 첫 프레임은 EMA 없이 그 확률로 시작
    for _ in range(20):
        ema.push(pa)
    got = [ema.push(pb)[0] for _ in range(8)]
    assert got.index("B") == 6                                         # 7 번째 프레임(0.7 s)
    assert ema.push(pb)[2] == "B"                                      # raw = 순간 최대값


def test_board_ema_ignores_a_short_blip_and_reset_starts_fresh():
    ema = pm._BoardEma(["A", "B"], alpha=0.1)
    for _ in range(30):
        ema.push(np.array([0.8, 0.2]))
    for _ in range(3):                                                 # 0.3 s 짜리 깜빡임은 확정을 바꾸지 않는다
        assert ema.push(np.array([0.05, 0.95]))[0] == "A"
    ema.reset()
    assert ema.push(np.array([0.3, 0.7]))[0] == "B"                    # 리셋 뒤 첫 프레임 그대로


def test_make_gru_board_drops_vote_warmup_and_periodic_reset(monkeypatch):
    seen = []
    monkeypatch.setattr(pm, "GruPosture", lambda path, **kw: seen.append(kw) or kw)
    mcfg = {"vote_frames": 10, "warmup_frames": 45, "reset_on_away_s": 60, "reset_every_s": 900, "smoothing": "vote", "smooth_s": 1.0}
    assert pm.make_gru("m.pt", mcfg, fps=10.0) == {"vote_frames": 10, "warmup_frames": 45, "reset_on_away_s": 60.0, "reset_every_s": 900}
    kw = pm.make_gru("m.pt", dict(mcfg, smoothing="board"), fps=10.0)
    assert kw == {"smoothing": "board", "smooth_s": 1.0, "rate_hz": 10.0, "warmup_frames": 0,
                  "reset_on_away_s": None, "reset_every_s": None}
    assert pm.make_gru("m.pt", dict(mcfg, smoothing="board", board_reset_every_s=900), fps=10.0)["reset_every_s"] == 900
