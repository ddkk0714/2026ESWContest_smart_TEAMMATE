"""최종 보드 통신 확정 전 사용하는 HTTP 기반 디스플레이 미리보기 어댑터."""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .preview_protocol import PreviewStateStore, validate_test_frame


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
            if self.path not in {"/api/feedback", "/api/test-frame"}:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 16_384:
                    raise ValueError("invalid content length")
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
                if self.path == "/api/feedback":
                    verdict = body.get("verdict")
                    if verdict not in {"accept", "reject", "correct"}:
                        raise ValueError("invalid verdict")
                    store.add_feedback(body)
                else:
                    validate_test_frame(body)
                    store.add_test_frame(body)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
                error = "invalid_feedback" if self.path == "/api/feedback" else "invalid_test_frame"
                self._json(HTTPStatus.BAD_REQUEST, {"error": error})
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
