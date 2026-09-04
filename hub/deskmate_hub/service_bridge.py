"""Line-framed bridge used by the Atlas native HTTP service."""
from __future__ import annotations

import json
import sys
import threading
from typing import Any

from .demo import run_demo_loop
from .preview_protocol import PreviewStateStore, validate_test_frame


class BridgeStateStore(PreviewStateStore):
    def __init__(self) -> None:
        super().__init__()
        self._output_lock = threading.Lock()

    def write_message(self, message: str) -> None:
        with self._output_lock:
            sys.stdout.write(message + "\n")
            sys.stdout.flush()

    def publish(self, payload: dict[str, Any]) -> None:
        super().publish(payload)
        self.write_message(
            "STATE\t" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )


def _read_commands(store: BridgeStateStore) -> None:
    for raw_line in sys.stdin.buffer:
        request_id = "0"
        try:
            line = raw_line.decode("utf-8")
            kind, request_id, path, raw_body = line.rstrip("\n").split("\t", 3)
            if kind != "POST":
                raise ValueError("invalid command")
            body = json.loads(raw_body)
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            if path == "/api/feedback":
                if body.get("verdict") not in {"accept", "reject", "correct"}:
                    raise ValueError("invalid verdict")
                store.add_feedback(body)
            elif path == "/api/test-frame":
                validate_test_frame(body)
                store.add_test_frame(body)
            else:
                raise ValueError("invalid path")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
            store.write_message(f"ACK\t{request_id}\t400")
        else:
            store.write_message(f"ACK\t{request_id}\t202")


def run_bridge(interval: float = 1.0) -> None:
    store = BridgeStateStore()
    reader = threading.Thread(target=_read_commands, args=(store,), daemon=True)
    reader.start()
    run_demo_loop(store, interval=interval, cycles=0)


if __name__ == "__main__":
    run_bridge()
