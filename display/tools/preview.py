"""web/ 를 그대로 띄워보는 로컬 미리보기 서버. 표준 라이브러리만 쓴다.

  python tools/preview.py                 # http://localhost:8080
  python tools/preview.py --port 9090

브라우저에서 확인:
  http://localhost:8080/?demo=1                       브로커 없이 가짜 데이터
  http://localhost:8080/?broker=ws://<허브IP>:9001     실제 브로커

이건 개발 편의용이다. ATLAS 보드에서는 web-app 이 플랫폼에 의해 서빙되므로
이 파일은 배포에 포함되지 않는다.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 고칠 때마다 강제 새로고침하지 않아도 되게
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"[preview] {fmt % args}")


def main():
    p = argparse.ArgumentParser(description="DESKMATE display 로컬 미리보기")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args()

    if not (WEB_ROOT / "index.html").exists():
        raise SystemExit(f"web/ 를 못 찾음: {WEB_ROOT}")

    handler = functools.partial(Handler, directory=str(WEB_ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((a.host, a.port), handler) as httpd:
        print(f"[preview] {WEB_ROOT}")
        print(f"[preview] http://localhost:{a.port}/?demo=1   (Ctrl+C 로 종료)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[preview] 종료")


if __name__ == "__main__":
    main()
