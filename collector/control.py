"""Per-user local capture switch; files contain flags/status, never key events."""
import json
import os
from pathlib import Path
import time


def control_directory():
    if os.name == 'nt':
        return Path(os.environ['LOCALAPPDATA']) / 'DESKMATE' / 'capture-control'
    return Path.home() / '.local' / 'state' / 'deskmate' / 'capture-control'


class ControlState:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else control_directory()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.directory / 'collector.lock').open('a+b')
        self.lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            if os.fstat(self.lock.fileno()).st_size == 0:
                self.lock.write(b'0')
                self.lock.flush()
            self.lock.seek(0)
            try:
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.lock.close()
                raise RuntimeError('collector_already_running') from None
        else:
            import fcntl
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.lock.close()
                raise RuntimeError('collector_already_running') from None
        self.request_id = None

    def requested(self):
        try:
            d = json.loads((self.directory / 'request.json').read_text())
            if type(d.get('enabled')) is not bool or not isinstance(d.get('request_id'), str):
                raise ValueError()
            self.request_id = d['request_id']
            return d['enabled']
        except FileNotFoundError:
            return True
        except (ValueError, OSError, TypeError):
            return False  # Invalid control data never enables keyboard capture.

    def update(self, capture, *, running=True, publish_enabled=True):
        state = {'pid': os.getpid(), 'updated_at': time.time(), 'running': running,
                 'keyboard_enabled': (None if running and capture.keyboard_enabled and not capture.keyboard_running()
                                      else bool(running and capture.keyboard_running())),
                 'mouse_enabled': bool(running and capture.mouse_running()),
                 'request_id': self.request_id, 'mqtt_publish_enabled': publish_enabled}
        temporary = self.directory / 'state.new'
        with temporary.open('w') as f:
            json.dump(state, f)
        temporary.chmod(0o600)
        temporary.replace(self.directory / 'state.json')

    def close(self):
        self.lock.close()
