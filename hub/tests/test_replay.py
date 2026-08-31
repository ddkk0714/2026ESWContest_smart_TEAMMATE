"""로그 리플레이 하네스 테스트."""
from __future__ import annotations

import io

import pytest

from deskmate_hub.__main__ import main
from deskmate_hub.demo import demo_frames
from deskmate_hub.replay import frame_from_dict, iter_frames, replay


def test_frame_from_dict_defaults():
    f = frame_from_dict({"now": 5})
    assert f.now == 5.0 and f.present is True and f.pc_ratio == 0.0
    assert f.signals == {} and f.touch is False and f.break_accepted is None


def test_frame_from_dict_full():
    f = frame_from_dict({
        "now": 1, "present": False, "pc_ratio": 0.4,
        "signals": {"posture": {"delta": 0.9, "phi": 0.2, "available": True}},
        "touch": True, "break_accepted": False,
    })
    assert f.pc_ratio == 0.4 and f.present is False
    assert f.signals["posture"].delta == 0.9
    assert f.touch is True and f.break_accepted is False


def test_iter_frames_skips_comments_and_blanks():
    lines = ['# 주석', '', '{"now": 0, "touch": true}', '   ', '{"now": 30}']
    frames = list(iter_frames(lines))
    assert len(frames) == 2
    assert frames[0].touch is True


def test_iter_frames_reports_bad_json():
    with pytest.raises(ValueError, match="line 2"):
        list(iter_frames(['{"now": 0}', '{not json}']))


def test_replay_demo_full_path():
    res = replay(demo_frames())
    # 전체 경로가 피로 확정 → 개입 → 회복 → 종료까지 도달
    for state in ("FATIGUE", "CAUSE_ANALYSIS", "ACTION_BREAK", "MONITOR", "RECOVERY", "END"):
        assert state in res.visited, f"{state} 미방문"
    assert res.ticks == len(demo_frames())
    assert res.transitions >= 10


def test_replay_trace_records_only_changes():
    res = replay(demo_frames())
    # 트레이스 항목 수 == 전이 수, 각 항목은 from != to
    assert len(res.trace) == res.transitions
    assert all(e["from"] != e["to"] for e in res.trace)


def test_main_demo_ok(capsys):
    assert main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "DESKMATE FSM replay" in out and "END" in out


def test_main_demo_quiet(capsys):
    assert main(["--demo", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "전이 트레이스" not in out


def test_main_replay_from_file(tmp_path, capsys):
    log = tmp_path / "s.jsonl"
    log.write_text('{"now": 0, "touch": true}\n{"now": 30}\n', encoding="utf-8")
    assert main(["--replay", str(log)]) == 0
    assert "frames: 2" in capsys.readouterr().out


def test_main_missing_file_returns_error(capsys):
    assert main(["--replay", "/nonexistent/log.jsonl"]) == 1


def test_main_requires_source():
    with pytest.raises(SystemExit):        # argparse p.error → exit 2
        main([])
