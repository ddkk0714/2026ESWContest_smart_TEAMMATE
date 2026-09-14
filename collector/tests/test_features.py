"""합성 이벤트로 특징 추출 검증 — 실제 키 캡처 없이 로직만 테스트."""
import os
import sys

# 저장소 루트를 path 에 추가해 `collector` 패키지를 import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from collector.capture import InputCapture, KeyEvent, KeyKind, MouseEvent  # noqa: E402
from collector.features import extract  # noqa: E402


def _typing(start=0.0, n=20, flight=0.15, dwell=0.09, kind=KeyKind.CHAR):
    evs, t = [], start
    for _ in range(n):
        evs.append(KeyEvent(t, kind, down=True))
        evs.append(KeyEvent(t + dwell, kind, down=False))
        t += flight
    return evs


def _mouse(start=0.0, n=10, step=0.2, kind="move"):
    return [MouseEvent(start + i * step, kind) for i in range(n)]


def test_empty_is_idle():
    f = extract([], window_s=60, now_t=100.0, mouse_events=[])
    assert f.typing_active is False and f.input_active is False
    assert f.idle_ratio == 1.0


def test_steady_typing():
    f = extract(_typing(41.0, 30, 0.15, 0.09), window_s=60, now_t=60.0)
    assert f.typing_active is True and f.keydown_count == 30
    assert 80 <= f.dwell.mean <= 100
    assert 140 <= f.flight.mean <= 160
    assert f.flight_cv < 0.1


def test_irregular_typing_has_higher_cv():
    evs, t = [], 41.0
    for fl in [0.1, 0.4, 0.12, 0.5, 0.09, 0.6, 0.11, 0.45] * 3:
        evs.append(KeyEvent(t, KeyKind.CHAR, down=True))
        evs.append(KeyEvent(t + 0.08, KeyKind.CHAR, down=False))
        t += fl
    f_irr = extract(evs, window_s=60, now_t=60.0)
    f_steady = extract(_typing(41.0, 24, 0.15, 0.08), window_s=60, now_t=60.0)
    assert f_irr.flight_cv > f_steady.flight_cv


def test_correction_rate():
    evs = _typing(41.0, 10, 0.15, 0.08, KeyKind.CHAR) + _typing(43.0, 5, 0.15, 0.08, KeyKind.BACKSPACE)
    f = extract(evs, window_s=60, now_t=60.0)
    assert abs(f.correction_rate - (5 / 15)) < 0.01


def test_idle_ratio():
    f = extract(_typing(55.0, 5, 0.15, 0.08), window_s=60, now_t=60.0)
    assert f.idle_ratio > 0.8


def test_mouse_work_without_typing():
    mevs = _mouse(41.0, 30, 0.5, "move") + [MouseEvent(42.0, "click")]
    f = extract([], window_s=60, now_t=60.0, mouse_events=mevs)
    assert f.typing_active is False and f.mouse_active is True and f.input_active is True
    assert f.mouse_event_rate > 0


def test_autorepeat_hold_counts_as_one():
    cap = InputCapture()
    for _ in range(50):
        cap._on_press("arrow")
    cap._on_release("arrow")
    assert len([e for e in cap.snapshot() if e.down]) == 1
    cap._on_press("arrow")
    assert len([e for e in cap.snapshot() if e.down]) == 2


def test_payload_matches_mqtt_contract():
    """docs/mqtt-topics.md 의 deskmate/sensor/keystroke 필드명을 지킨다."""
    f = extract(_typing(41.0, 20), window_s=60, now_t=60.0)
    p = f.to_payload(node="pc-collector")
    for key in ("node", "window_s", "dwell_mean_ms", "dwell_std_ms",
                "flight_mean_ms", "flight_std_ms", "idle_ratio", "correction_rate"):
        assert key in p, f"규약 필드 누락: {key}"
    assert p["node"] == "pc-collector" and p["window_s"] == 60


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
