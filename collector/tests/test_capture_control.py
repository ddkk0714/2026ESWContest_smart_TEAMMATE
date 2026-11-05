import json
import time
from unittest.mock import patch

import pytest

from collector.capture import InputCapture
from collector.control import ControlState
from collector.config import Config
from collector.__main__ import capture_payload


class Listener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.alive = False
    def start(self): self.alive = True
    def stop(self): self.alive = False
    def join(self, timeout=None): pass
    def is_alive(self): return self.alive


@pytest.fixture
def capture():
    with patch('collector.capture.keyboard.Listener', Listener), patch('collector.capture.mouse.Listener', Listener):
        cap = InputCapture()
        cap.start()
        yield cap
        cap.stop()


def test_switch_really_stops_keyboard_and_preserves_mouse(capture):
    kb, mouse = capture._kb, capture._ms
    capture.set_keyboard_enabled(False)
    assert not kb.is_alive() and not capture.keyboard_running()
    assert capture._ms is mouse and mouse.is_alive()
    capture.set_keyboard_enabled(True)
    assert capture._kb is not kb and capture.keyboard_running()
    assert capture._ms is mouse


def test_disabled_callbacks_and_old_generation_cannot_leak(capture):
    old = capture._kb
    capture._on_press('synthetic')
    capture.set_keyboard_enabled(False)
    capture._on_press('synthetic')
    assert capture.snapshot() == []
    capture.set_keyboard_enabled(True)
    old.callbacks['on_press']('synthetic')
    old.callbacks['on_release']('synthetic')
    assert capture.snapshot() == [] and capture._down_at == {}
    capture._kb.callbacks['on_press']('synthetic')
    assert len(capture.snapshot()) == 1


def test_disabled_payload_is_not_idle_or_zero_typing(capture):
    capture._on_move(0, 0)
    capture.set_keyboard_enabled(False)
    p = capture_payload(capture, Config(), time.monotonic())
    assert p['valid'] is False and p['capture_status'] == 'user_disabled'
    assert p['keyboard_capture_enabled'] is False
    assert p['mouse_active'] and p['input_active']
    for name in ('typing_active', 'idle_ratio', 'dwell_mean_ms', 'event_count'):
        assert name not in p


def test_reenable_does_not_republish_previous_keys(capture):
    capture._on_press('synthetic')
    capture._on_release('synthetic')
    capture.set_keyboard_enabled(False)
    capture.set_keyboard_enabled(True)
    p = capture_payload(capture, Config(), time.monotonic())
    assert p['keyboard_capture_enabled'] is True
    assert p['event_count'] == 0


def test_state_tracks_listener_and_acknowledgement(tmp_path, capture):
    c = ControlState(tmp_path)
    try:
        (tmp_path/'request.json').write_text(json.dumps({'enabled':False,'request_id':'synthetic'}))
        capture.set_keyboard_enabled(c.requested())
        c.update(capture, publish_enabled=False)
        s = json.loads((tmp_path/'state.json').read_text())
        assert s['keyboard_enabled'] is False and s['mouse_enabled'] is True
        assert s['request_id'] == 'synthetic' and not s['mqtt_publish_enabled']
    finally: c.close()


def test_corrupted_request_fails_closed(tmp_path):
    c = ControlState(tmp_path)
    try:
        (tmp_path/'request.json').write_text('{broken')
        assert c.requested() is False
    finally: c.close()


def test_second_collector_is_rejected(tmp_path):
    c = ControlState(tmp_path)
    try:
        with pytest.raises(RuntimeError, match='already_running'):
            ControlState(tmp_path)
    finally: c.close()
