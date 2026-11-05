"""Pi 5 화면을 SSH 로 받아 PC 브라우저에 띄운다(발표·원격 확인용).

보드의 Atlas WindowManager 가 화면 전체(시스템 팝업 포함)를 PNG 로 떠 준다
(D-Bus `com.atlas.WindowManager1.CaptureScreen <경로>`, 11-05 Pi 5 에서 한 장 약 0.1 s).
SSH 한 줄로 그걸 쉬지 않고 돌려 `F <바이트 수>` + PNG 를 차례로 흘려보내고, 여기서는 가장 최근 장만
브라우저에 민다(multipart/x-mixed-replace). 늦은 장은 버리므로 지연이 쌓이지 않는다.

앱 안에서 뜨는 방법은 Atlas Flutter 엔진에 `Image.toByteData` 가 없어 안 된다(11-05 확인).

    python tools/pi5_screen.py                       # 172.20.10.2, http://localhost:8099
    python tools/pi5_screen.py --host 172.16.34.198 --port 8100

표준 라이브러리만 쓴다. ssh 는 키로 들어갈 수 있어야 한다(비밀번호 입력 없이).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 보드(BusyBox sh)에서 도는 고리. 캡처가 실패하면(앱 전환 중 등) 1 초 쉬고 다시.
REMOTE_LOOP = (
    "f=/tmp/deskmate_screen.png; "
    "while :; do "
    "abusctl call com.atlas.WindowManager1 CaptureScreen $f >/dev/null 2>&1 "
    "|| { sleep 1; continue; }; "
    'echo "F $(wc -c < $f)"; cat $f; '
    "done"
)

PAGE = b"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>DESKMATE Pi 5 \xed\x99\x94\xeb\xa9\xb4</title>
<style>html,body{margin:0;height:100%;background:#111}
img{position:fixed;inset:0;margin:auto;max-width:100%;max-height:100%}</style>
</head><body><img src="/stream" alt=""></body></html>"""


class Latest:
    """가장 최근 장 하나와 번호. 새 장이 오면 기다리던 쪽을 깨운다."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.png: bytes | None = None
        self.seq = 0
        self.fps = 0.0

    def put(self, png: bytes) -> None:
        with self.cond:
            self.png = png
            self.seq += 1
            self.cond.notify_all()

    def wait_after(self, seq: int, timeout: float = 5.0) -> tuple[int, bytes | None]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq != seq, timeout=timeout)
            return self.seq, self.png


def read_frames(stream, latest: Latest) -> None:
    """`F <n>` 줄 + n 바이트 PNG 를 계속 읽는다."""
    count, since = 0, time.monotonic()
    while True:
        line = stream.readline()
        if not line:
            return
        if not line.startswith(b"F "):
            continue
        size = int(line[2:].strip() or 0)
        png = stream.read(size)
        if len(png) != size:
            return
        latest.put(png)
        count += 1
        now = time.monotonic()
        if now - since >= 2:
            latest.fps = count / (now - since)
            print(f"\r{latest.fps:4.1f} fps · {size // 1024} KB/장 ", end="", flush=True)
            count, since = 0, now


def run_ssh(args, latest: Latest) -> None:
    """SSH 가 끊기면(보드 재부팅·망 끊김) 2 초 뒤 다시 붙는다."""
    cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5"]
    if args.host_key_alias:
        cmd += ["-o", f"HostKeyAlias={args.host_key_alias}"]
    cmd += [f"{args.user}@{args.host}", REMOTE_LOOP]
    while True:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        read_frames(proc.stdout, latest)
        proc.kill()
        print("\nSSH 끊김 — 2 초 뒤 다시 연결", file=sys.stderr)
        time.sleep(2)


def handler(latest: Latest):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # 요청마다 찍지 않는다
            pass

        def do_GET(self):
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE)
            elif self.path == "/shot.png":
                _, png = latest.wait_after(-1, timeout=0)
                if png is None:
                    self._send(503, "text/plain", b"no frame yet")
                else:
                    self._send(200, "image/png", png)
            elif self.path == "/stream":
                self._stream()
            else:
                self._send(404, "text/plain", b"not found")

        def _send(self, code, kind, body):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = -1
            try:
                while True:
                    seq, png = latest.wait_after(seq)
                    if png is None:
                        continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/png\r\nContent-Length: "
                        + str(len(png)).encode()
                        + b"\r\n\r\n"
                        + png
                        + b"\r\n"
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="172.20.10.2", help="Pi 5 주소(핫스팟 기본 172.20.10.2)")
    p.add_argument("--user", default="root")
    p.add_argument(
        "--host-key-alias",
        default="172.16.34.197",
        help="known_hosts 에 Pi 5 키가 저장된 이름(주소가 바뀌어도 같은 키로 확인). 빈 값이면 쓰지 않는다",
    )
    p.add_argument("--port", type=int, default=8099, help="PC 에서 열 포트")
    args = p.parse_args()

    latest = Latest()
    threading.Thread(target=run_ssh, args=(args, latest), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(latest))
    print(f"Pi 5 화면: http://localhost:{args.port}  (끝내려면 Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
