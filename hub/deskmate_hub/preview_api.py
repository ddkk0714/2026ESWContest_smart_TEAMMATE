"""최종 보드 통신 확정 전 사용하는 HTTP 기반 디스플레이 미리보기 어댑터."""
from __future__ import annotations

import json
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class PreviewStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._feedback: deque[dict[str, Any]] = deque()

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._state = payload

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state

    def add_feedback(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._feedback.append(payload)

    def pop_feedback(self) -> dict[str, Any] | None:
        with self._lock:
            return self._feedback.popleft() if self._feedback else None


def make_server(host: str, port: int, store: PreviewStateStore) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/health":
                self._json(HTTPStatus.OK, {"status": "ok", "state_ready": store.latest() is not None})
                return
            if self.path == "/api/state":
                state = store.latest()
                if state is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "state_not_ready"})
                else:
                    self._json(HTTPStatus.OK, state)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/api/feedback":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 4096:
                    raise ValueError("invalid content length")
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                verdict = body.get("verdict")
                if verdict not in {"accept", "reject", "correct"}:
                    raise ValueError("invalid verdict")
                store.add_feedback(body)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_feedback"})
                return
            self._json(HTTPStatus.ACCEPTED, {"accepted": True})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer((host, port), Handler)
