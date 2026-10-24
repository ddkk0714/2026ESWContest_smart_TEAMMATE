"""ToF 벽 잡음 측정 (2026-10-24) — 장치를 벽에 비춘 채 일정 시간 센서별 8×8 거리값이 얼마나 흔들리는지 잰다.

    python -I tof_wall_noise.py session --pi5 <Pi5 주소>                     # 사람이 하는 안내 진행(소리·Enter): 벽 45/50/60 cm 각 5분 → 맞춤
    python -I tof_wall_noise.py session --pi5 <Pi5 주소> --session-dir <같은 폴더> --steps empty,still,work   # 나중에 사람 단계
    python -I tof_wall_noise.py aim --pi5 <Pi5 주소> --distance-cm 60        # 조준·연결 확인: 3 s 중앙값 격자
    python -I tof_wall_noise.py record --pi5 <Pi5 주소> --label wall60 --distance-cm 60 --seconds 300   # 한 번만 녹화 → 분석
    python -I tof_wall_noise.py analyze noise_runs/session_.../wall60_...    # 다시 분석(끊긴 녹화 복구)
    python -I tof_wall_noise.py fit noise_runs/session_.../wall*             # 거리별 σ → 시뮬 L8Params
    python -I tof_wall_noise.py compare --wall <wall60 폴더> --still <still 폴더> [--empty <empty 폴더>]
    python -I tof_wall_noise.py selftest                                     # 하드웨어 없이 가짜 중계기로 전체 확인

입력: Pi 4 중계기 GET :8772/raw (센서별 마지막 8×8, 펌웨어 방향이 이미 적용된 장면 방향, -1 = 타깃 없음)와
자세 서비스 GET :8771/api/state (닿으면 1 s 마다 같이 남긴다 — 벽만 볼 때·가만히 앉았을 때 모델이 뭐라고 하는지).
`--pi5` 를 주면 PC → Pi 5 → Pi 4 ssh 터널을 스스로 열고 닫는다. 없으면 127.0.0.1:8772/8771 (직접 연 터널)을 쓴다.

녹화 폴더(run): meta.json · frames.jsonl · state.jsonl · report.json · summary.md. 센서값 원본은 frames.jsonl 에 프레임마다 다 남는다
(실측 기준 거리 0.35 MB/분 + 자세 0.11 MB/분 ≈ 0.46 MB/분, zip 하면 약 1/4).
- frames.jsonl 한 줄 = {"host_t", "port": "s0"|"s1", "raw": "{\"t\":\"tof\",\"ms\",\"seq\",\"w\",\"h\",\"d\":[64],\"st\":[64],\"flags\"}"}.
  tof_live.py 녹화와 같은 형식이라 `tof_live.py --ports frames.jsonl@s0,frames.jsonl@s1 --model x.onnx` 로 모델에 다시 흘릴 수 있다.
- 유효 칸 = 상태값 5·6·9·10 이고 거리 ≥ 0 (자세 서비스 posture_net.h 의 zoneValid 와 같다).

잡음 지표(칸마다 시간축으로 계산, 센서 요약은 유효율 90 % 이상 칸의 중앙값):
- std: 표준편차(전체 흔들림). robust_std: 1.4826·MAD(튀는 값에 둔감).
- diff_std: 연속 두 프레임 차의 RMS/√2 — 프레임마다 새로 생기는(흰) 잡음. 천천히 변하는 성분은 빠진다.
- slow_std: 1 s 이동평균의 표준편차. slow_ratio = 흰 잡음만일 때 기대값(diff_std/√N) 대비 배수, ≈1 이면 흰 잡음.
- nod_rms: (1 s 평균 − 5 s 평균)의 RMS ≈ 0.2~1 Hz 대역 = 끄덕임·몸 흔들림 대역. nod_ratio = 흰 잡음 기대값 대비 배수.
- lag1_r: 1 프레임 자기상관. flicker_per_min: 유효↔무효 바뀜 횟수/분. outlier_rate: 중앙값에서 max(50 mm, 5·robust_std) 넘게 튄 비율.
- common_ratio: 프레임마다 모든 칸이 같이 움직이는 성분(칸 평균의 흔들림)이 칸끼리 독립일 때 기대값의 몇 배인가.
표준 라이브러리만 쓴다(Python 3.8+).
"""
from __future__ import annotations

import argparse
import array
import glob
import json
import math
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

VALID = {5, 6, 9, 10}
MAX_MM = 4000.0                     # 모델 입력 채널 0 = dist / 4000
SIM_SIGMA0, SIM_SIGMA1, SIM_DROPOUT, SIM_WALL_REFL = 3.0, 4.0, 0.003, 0.70   # tof-sim v5 tofsim/l8.py·scene.py 기본값
GOOD_VALID = 0.9                    # 요약에 쓰는 칸: 유효율 90 % 이상
FILTERS = ("raw", "mean3", "median3", "median5", "ema0.5", "ema0.3")
FILTER_MAX_SAMPLES = 3000
NAMES = {0: "A(왼쪽)", 1: "B(오른쪽)"}
PI4_ADDR = "172.16.34.199"          # Pi 4 eth0 (Pi 5 와 랜선 직결)
MB_PER_MIN = 0.46                   # 실측 frames 576 B/줄 × 10 줄/s + /api/state ≈ 1.9 KB/s
TOOL_VERSION = "2026-10-24b"

# 안내 진행 순서(사용자 지정 10-24: 벽 45·50·60 cm 각 5분)
PLAN = [
    {"label": "wall45", "kind": "wall", "distance_cm": 45, "seconds": 300},
    {"label": "wall50", "kind": "wall", "distance_cm": 50, "seconds": 300},
    {"label": "wall60", "kind": "wall", "distance_cm": 60, "seconds": 300},
    {"label": "empty", "kind": "empty", "distance_cm": None, "seconds": 60},
    {"label": "still", "kind": "person", "distance_cm": None, "seconds": 180},
    {"label": "work", "kind": "person", "distance_cm": None, "seconds": 300},
]
WALL_STEPS = ["wall45", "wall50", "wall60"]      # session 기본(사용자 10-24: 우선 벽만). 사람 단계는 --with-person / --steps
PERSON_STEPS = ["empty", "still", "work"]
GUIDE = {
    "wall": "센서 커버 앞면 → 벽을 줄자로 {d} cm (바닥과 평행)에 맞춘다. 장치 정면이 벽을 똑바로, 시야엔 벽만. 지그 기울기는 그대로 둔다.",
    "empty": "장치를 평소 쓰는 자리(책상 위, 의자 쪽)로 옮긴다. 의자는 평소 위치, 사람은 시야 밖. "
             "사람 없는 배경을 잰다 — 이제부터 끝날 때까지 장치를 움직이지 않는다.",
    "still": "평소 자리에 앉아 바른 자세로 최대한 가만히. 눈 뜨고 화면을 본다. 손은 무릎이나 책상 위(타이핑 안 함). 졸지 않는다.",
    "work": "평소처럼 작업한다(타이핑·화면 보기). 졸지 않는다.",
}
SOUNDS = {  # (Hz, ms)
    "tick": [(800, 70)], "start": [(1200, 450)], "done": [(1000, 150), (1300, 150), (1600, 350)],
    "attention": [(900, 180), (900, 180)], "warn": [(420, 300), (320, 500)],
    "finish": [(1000, 150), (1300, 150), (1600, 150), (2000, 500)],
}


def beep(kind: str) -> None:
    """Windows 는 winsound 로 소리, 아니면 터미널 벨."""
    pat = SOUNDS.get(kind, [(1000, 200)])
    try:
        import winsound
        for f, d in pat:
            winsound.Beep(f, d)
    except Exception:
        try:
            sys.stdout.write("\a" * len(pat))
            sys.stdout.flush()
        except Exception:
            pass


def no_beep(kind: str) -> None:
    pass


def get_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def rnd(x, n=2):
    return None if x is None else round(x, n)


def is_valid(d, st) -> bool:
    return d is not None and d >= 0 and st in VALID


def sim_sigma(d_mm: float, sigma0=SIM_SIGMA0, sigma1=SIM_SIGMA1, refl=SIM_WALL_REFL) -> float:
    """시뮬(l8.py) 이 수직 벽 한 칸에 주는 σ: sqrt(σ0² + (σ1/√s)²), 신호 s ≈ 반사율/d²."""
    d = max(d_mm, 50.0) / 1000.0
    return math.sqrt(sigma0 ** 2 + sigma1 ** 2 * d * d / refl)


def mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


# ───────────────────────── ssh 터널 ─────────────────────────

def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Tunnel:
    """PC → Pi 5 → Pi 4 ssh 터널(:8772 중계기, :8771 자세 서비스)을 열고 닫는다. 창을 열어 둘 수 없는 Claude 셸용.

    보드 root 는 빈 비밀번호라 dropbear 가 묻지 않고 들인다(BatchMode 로 묻는 창이 뜨지 않게).
    -J 로는 -o 옵션이 점프 호스트에 안 넘어가므로 Pi 5 호스트 키는 먼저 따로 받아 둔다(accept-new).
    """

    def __init__(self, pi5: str, pi4: str = PI4_ADDR, user: str = "root"):
        self.pi5, self.pi4, self.user = pi5, pi4, user
        self.p = None
        self.urls = None

    def open(self):
        ssh = shutil.which("ssh")
        if not ssh:
            raise SystemExit("ssh 가 없다 — Windows 설정 → 선택적 기능 → OpenSSH 클라이언트")
        opts = ["-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        try:
            r = subprocess.run([ssh, *opts, f"{self.user}@{self.pi5}", "true"], capture_output=True, timeout=40)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"Pi 5({self.pi5}) ssh 가 40 s 안에 안 끝났다 — 주소·핫스팟·VPN 확인")
        if r.returncode != 0:
            raise SystemExit(f"Pi 5({self.pi5}) ssh 실패: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}\n"
                             "  → 주소·같은 핫스팟인지·VPN(WARP) 끄기 확인. 호스트 키 경고면 사람에게 보여 준다")
        raw_port, api_port = free_port(), free_port()
        cmd = [ssh, "-N", *opts, "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
               "-L", f"{raw_port}:127.0.0.1:8772", "-L", f"{api_port}:127.0.0.1:8771",
               "-J", f"{self.user}@{self.pi5}", f"{self.user}@{self.pi4}"]
        self.p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        t0 = time.time()
        while time.time() - t0 < 30:
            if self.p.poll() is not None:
                err = self.p.stderr.read().decode("utf-8", "replace").strip()
                raise SystemExit(f"Pi 4 터널 실패(Pi 5 {self.pi5} 거쳐 {self.pi4}): {err[-400:]}\n"
                                 "  → Pi 5 eth0 carrier·Pi 4 주소(ssh root@<Pi5> \"ip neigh show dev eth0\") 확인")
            try:
                socket.create_connection(("127.0.0.1", raw_port), 0.5).close()
                self.urls = (f"http://127.0.0.1:{raw_port}", f"http://127.0.0.1:{api_port}")
                return self.urls
            except OSError:
                time.sleep(0.3)
        self.close()
        raise SystemExit("터널이 30 s 안에 안 열렸다")

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def close(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(3)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.p = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()


# ───────────────────────── 녹화 ─────────────────────────

class RecordError(Exception):
    pass


def frame_line(s: dict, host_t: float) -> str:
    raw = {"t": "tof", "ms": int(s.get("ts_ms") or 0), "seq": int(s.get("frame_seq") or 0),
           "w": int(s.get("w") or 8), "h": int(s.get("h") or 8),
           "d": [int(v) for v in s["dist_mm"]],
           "st": [int(v) for v in (s.get("status") or [5] * len(s["dist_mm"]))],
           "flags": int(s.get("flags") or 0)}
    return json.dumps({"host_t": round(host_t, 4), "port": f"s{int(s['id'])}",
                       "raw": json.dumps(raw, separators=(",", ":"))}, separators=(",", ":"))


class FrameSink:
    """여러 폴러가 받은 /raw 에서 새 프레임만 골라 frames.jsonl 에 쓴다."""

    def __init__(self, ff):
        self.ff, self.lock = ff, threading.Lock()
        self.last_key, self.last_ts, self.latest, self.counts, self.last_new = {}, {}, {}, {}, {}
        self.polls = self.errors = 0
        self.lat = []

    def take(self, j, t, lat):
        with self.lock:
            self.polls += 1
            if len(self.lat) < 50000:
                self.lat.append(lat)
            for s in j.get("sensors", []):
                if not s.get("have") or not s.get("dist_mm"):
                    continue
                sid = int(s["id"])
                ts = int(s.get("ts_ms") or 0)
                key = (s.get("frame_seq"), ts)
                if self.last_key.get(sid) == key:
                    continue
                prev = self.last_ts.get(sid)
                # 폴러가 여럿이라 늦게 도착한 옛 프레임은 버린다(ESP32 재부팅으로 시각이 크게 줄면 받아들인다)
                if ts and prev and ts <= prev and prev - ts < 10000:
                    continue
                self.last_key[sid], self.last_ts[sid] = key, ts
                self.latest[sid] = s
                self.counts[sid] = self.counts.get(sid, 0) + 1
                self.last_new[sid] = t
                self.ff.write(frame_line(s, t) + "\n")

    def error(self):
        with self.lock:
            self.errors += 1


class RawPoller(threading.Thread):
    """중계기 /raw 폴러. 터널 왕복이 느려도 놓치지 않게 여러 개를 엇갈려 돌린다(중계기는 응답마다 연결을 닫는다)."""

    def __init__(self, url, sink, period, offset):
        super().__init__(daemon=True)
        self.url, self.sink, self.period, self.offset = url, sink, period, offset
        self.stop = threading.Event()

    def run(self):
        if self.stop.wait(self.offset):
            return
        while not self.stop.is_set():
            t = time.time()
            try:
                j = get_json(self.url + "/raw", 3.0)
                self.sink.take(j, t, time.time() - t)
            except Exception:
                self.sink.error()       # 뷰어·Pi 5 프리뷰가 같이 폴링하면 중계기 HTTP 가 가끔 끊긴다 — 건너뛴다
            self.stop.wait(max(0.0, self.period - (time.time() - t)))


class StatePoller(threading.Thread):
    """자세 서비스 /api/state 를 1 s 마다 state.jsonl 에 남긴다(중계기 폴링을 막지 않게 따로 돈다)."""

    def __init__(self, url: str, fp, period: float = 1.0):
        super().__init__(daemon=True)
        self.url, self.fp, self.period = url, fp, period
        self.stop = threading.Event()
        self.n = self.errors = 0
        self.last = None

    def run(self):
        while not self.stop.is_set():
            t = time.time()
            try:
                j = get_json(self.url + "/api/state", 2.5)
                self.fp.write(json.dumps({"host_t": round(t, 3), "state": j}, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.n += 1
                self.last = j
            except Exception:
                self.errors += 1
            self.stop.wait(max(0.0, self.period - (time.time() - t)))


def center_median(d, st, w=8):
    vals = [d[r * w + c] for r in range(2, 6) for c in range(2, 6) if is_valid(d[r * w + c], st[r * w + c])]
    return statistics.median(vals) if vals else None


def check_advancing(raw_url: str, timeout: float = 4.0):
    """중계기는 ESP32 가 멈춰도 마지막 프레임을 have=true 로 계속 준다 — 프레임 번호가 실제로 느는지 본다.
    (처음 /raw, 보이는 센서 id 집합, 그중 늘어난 id 집합)을 돌려준다."""
    first = get_json(raw_url + "/raw")
    k0 = {int(s["id"]): (s.get("frame_seq"), s.get("ts_ms")) for s in first.get("sensors", [])
          if s.get("have") and s.get("dist_mm")}
    moved = set()
    t0 = time.time()
    while k0 and time.time() - t0 < timeout and len(moved) < len(k0):
        time.sleep(0.15)
        try:
            j = get_json(raw_url + "/raw")
        except Exception:
            continue
        for s in j.get("sensors", []):
            sid = int(s["id"])
            if s.get("have") and sid in k0 and (s.get("frame_seq"), s.get("ts_ms")) != k0[sid]:
                moved.add(sid)
    return first, set(k0), moved


def countdown(sec: float, kind: str, beep_fn, say):
    msg = "앉은 자세로 준비하세요" if kind == "person" else "시야에서 물러나세요"
    for k in range(int(round(sec)), 0, -1):
        say(f"  {k} … {msg}")
        beep_fn("tick")
        time.sleep(0.93)


def record(raw_url: str, api_url: str, seconds: float, label: str, out: str, distance_cm=None,
           light: str = "", note: str = "", interval: float = 0.08, pollers: int = 3, delay: float = 0.0,
           beep_fn=no_beep, kind: str = "wall", quiet: bool = False) -> str:
    raw_url, api_url = raw_url.rstrip("/"), (api_url or "").rstrip("/")
    say = (lambda *a: None) if quiet else (lambda *a: print(*a, flush=True))
    try:
        first, keys, moved = check_advancing(raw_url)
    except Exception as e:
        raise RecordError(f"중계기 {raw_url}/raw 에 못 붙음: {e} — 터널(--pi5 또는 문서 §3-2)·보드 relay.sh status 확인")
    if not keys:
        raise RecordError("중계기는 응답하지만 ToF 격자가 없다(have=false) — ESP32 USB·부팅 30 s·relay.log 확인")
    if not moved:
        raise RecordError("프레임 번호가 안 는다 — 중계기가 마지막 프레임만 되풀이한다(ESP32 멈춤·USB 빠짐). ESP32 USB 를 확인한다")
    stuck = sorted(keys - moved)
    if stuck:
        say(f"주의: 센서 {stuck} 은 프레임이 안 는다(멈춤). 나머지 센서로 녹화한다.")
    if len(keys) < 2:
        say(f"주의: 센서 {len(keys)}개만 보인다(id {sorted(keys)}). 그대로 녹화한다.")
    service = None
    if api_url:
        try:
            st = get_json(api_url + "/api/state")
            service = {"model": st.get("model"), "online_sensors": st.get("online_sensors"),
                       "posture_at_start": st.get("posture")}
        except Exception as e:
            say(f"자세 서비스 응답 없음({e}) — 자세 기록 없이 거리만 녹화한다.")
            api_url = ""
    if delay > 0:
        countdown(delay, kind, beep_fn, say)
    started = time.time()
    run_dir = os.path.join(out, f"{label}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(started))}")
    os.makedirs(run_dir, exist_ok=True)
    meta = {"tool": "tof_wall_noise.py", "version": TOOL_VERSION, "label": label, "kind": kind, "distance_cm": distance_cm,
            "light": light, "note": note, "raw_url": raw_url, "api_url": api_url, "seconds_planned": seconds,
            "interval_s": interval, "pollers": pollers,
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
            "started_epoch": round(started, 3), "relay_dev": first.get("dev"),
            "sensors_at_start": [{k: s.get(k) for k in ("id", "flags", "mask", "w", "h")}
                                 for s in first.get("sensors", []) if s.get("have")],
            "sensors_stuck_at_start": stuck, "service": service}
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    say(f"녹화 {mmss(seconds)} (예상 {seconds / 60 * MB_PER_MIN:.1f} MB) → {run_dir}")
    beep_fn("start")
    with open(os.path.join(run_dir, "frames.jsonl"), "w", encoding="utf-8") as ff, \
            open(os.path.join(run_dir, "state.jsonl"), "w", encoding="utf-8") as sf:
        sink = FrameSink(ff)
        n = max(1, int(pollers))
        raws = [RawPoller(raw_url, sink, interval * n, k * interval) for k in range(n)]
        sp = StatePoller(api_url, sf) if api_url else None
        for p in raws + ([sp] if sp else []):
            p.start()
        next_report = started + 15
        warned = set()
        try:
            while True:
                now = time.time()
                el = now - started
                if el >= seconds:
                    break
                with sink.lock:
                    last_new = dict(sink.last_new)
                    counts = dict(sink.counts)
                    latest = dict(sink.latest)
                    errs = sink.errors
                for sid in moved:
                    if now - last_new.get(sid, started) > 3 and el > 3 and sid not in warned:
                        say(f"  ! 센서 {sid}: 프레임이 3 s 째 안 들어온다 — ESP32·USB·터널 확인")
                        beep_fn("warn")
                        warned.add(sid)
                    elif sid in warned and now - last_new.get(sid, started) <= 1:
                        say(f"  센서 {sid}: 다시 들어온다")
                        warned.discard(sid)
                if now >= next_report:
                    next_report += 15
                    parts = []
                    for sid in sorted(latest):
                        s = latest[sid]
                        c = center_median(s["dist_mm"], s.get("status") or [5] * 64)
                        parts.append(f"s{sid} {counts[sid]} fr ({counts[sid] / max(el, 1e-6):.1f}/s) 가운데 "
                                     + ("--" if c is None else f"{c / 10:.1f} cm"))
                    pos = sp.last.get("posture") if sp and sp.last else "-"
                    say(f"[{mmss(el)} / {mmss(seconds)}] " + " | ".join(parts) + f" | 폴링 오류 {errs} | 자세 {pos}")
                time.sleep(0.25)
        except KeyboardInterrupt:
            say("Ctrl+C — 지금까지 것으로 분석한다")
        finally:
            for p in raws + ([sp] if sp else []):
                p.stop.set()
            for p in raws + ([sp] if sp else []):
                p.join(timeout=4)
    lat = sorted(sink.lat)
    meta.update({"ended": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds_actual": round(time.time() - started, 2),
                 "polls": sink.polls, "poll_errors": sink.errors,
                 "poll_latency_ms": {"median": rnd(1000 * med(lat), 1) if lat else None,
                                     "p95": rnd(1000 * pct(lat, 0.95), 1) if lat else None},
                 "frames": {f"s{k}": v for k, v in sorted(sink.counts.items())},
                 "state_lines": sp.n if sp else 0, "state_errors": sp.errors if sp else 0})
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return run_dir


# ───────────────────────── 조준 ─────────────────────────

def aim(raw_url: str, seconds: float = 3.0, distance_cm=None, kind: str = "wall") -> int:
    """0 = OK, 1 = 프레임 없음·멈춤, 2 = 조준 문제(15 cm 이내 칸·가운데 무효)."""
    raw_url = raw_url.rstrip("/")
    acc, nfr, last = {}, {}, {}
    t0 = time.time()
    errs = 0
    while time.time() - t0 < seconds:
        try:
            j = get_json(raw_url + "/raw")
        except Exception:
            errs += 1
            time.sleep(0.1)
            continue
        for s in j.get("sensors", []):
            if not s.get("have") or not s.get("dist_mm"):
                continue
            sid = int(s["id"])
            key = (s.get("frame_seq"), s.get("ts_ms"))
            if last.get(sid) == key:
                continue
            last[sid] = key
            st = s.get("status") or [5] * len(s["dist_mm"])
            a = acc.setdefault(sid, [[] for _ in s["dist_mm"]])
            nfr[sid] = nfr.get(sid, 0) + 1
            for i, (d, k) in enumerate(zip(s["dist_mm"], st)):
                if is_valid(d, k):
                    a[i].append(d)
        time.sleep(0.08)
    if not acc:
        print(f"프레임 없음(요청 실패 {errs}) — 터널·중계기 확인")
        return 1
    rc = 0
    for sid in sorted(acc):
        a, n = acc[sid], nfr[sid]
        meds = [statistics.median(v) if len(v) * 2 >= n else None for v in a]
        print(f"\n=== 센서 {sid} {NAMES.get(sid, '')}  프레임 {n} ({n / seconds:.1f}/s)   중앙값 cm ('--' = 절반 넘게 무효, '*' = 15 cm 이내)")
        for r in range(8):
            print("  " + " ".join("   --" if m is None else f"{m / 10:4.0f}" + ("*" if m < 150 else " ") for m in meds[r * 8:r * 8 + 8]))
        msgs = []
        if n < max(3, seconds * 2):     # 5 Hz 면 3 s 에 ≈15. 1~2 개뿐이면 멈춘 마지막 프레임이다
            msgs.append(f"→ 프레임이 {seconds:g} s 에 {n}개뿐 — 센서가 멈췄거나 터널이 너무 느리다(정상 ≈5/s)")
            rc = max(rc, 1)
        inner = [meds[r * 8 + c] for r in range(1, 7) for c in range(1, 7)]
        cen = med([meds[r * 8 + c] for r in range(2, 6) for c in range(2, 6)])
        nvalid = sum(m is not None for m in meds)
        if cen is None:
            msgs.append("→ 가운데 4×4 가 무효 — 벽·사람이 너무 멀거나(>3 m) 렌즈가 가려졌다")
            rc = max(rc, 2)
        else:
            # 아래 두 줄은 확인만 한다: 지그가 위로 기울면 가운데가 줄자 거리보다 멀고 위 줄이 더 멀게 나온다(정상).
            # 잡음은 칸마다 따로 재므로 장면이 고정이기만 하면 된다.
            if distance_cm:
                err = cen / 10 - distance_cm
                msgs.append(f"가운데 {cen / 10:.1f} cm (설정 {distance_cm:g} cm 대비 {err:+.1f} cm)")
                if abs(err) > max(5.0, 0.1 * distance_cm):
                    msgs.append("확인: 설정 거리와 다르다 — 기울기 탓이면 괜찮고, 아니면 줄자로 다시 잰다")
            else:
                msgs.append(f"가운데 {cen / 10:.1f} cm")
            vals = [m for m in inner if m is not None]
            if vals and kind == "wall":
                spread = (max(vals) - min(vals)) / cen
                msgs.append(f"안쪽 6×6 거리 폭 {100 * spread:.0f} %")
                if spread > 0.25:
                    msgs.append("확인: 폭이 크다 — 벽이 기울어 보이는 것(정상)인지, 책상·모서리·사람이 끼었는지 격자로 본다")
            near = sum(1 for m in meds if m is not None and m < 150)
            if near:
                msgs.append(f"→ 15 cm 이내 칸 {near}개: 렌즈 커버 앞 이물질·실·필름 확인(10-19 실 한 가닥 사례)")
                rc = max(rc, 2)
        msgs.append(f"유효 칸 {nvalid}/64")
        print("  " + " · ".join(msgs))
    print({0: "\n조준 OK", 1: "\n프레임이 안 들어온다 — 고친 뒤 다시", 2: "\n조준을 고친 뒤 다시"}[rc])
    return rc


# ───────────────────────── 분석 ─────────────────────────

def load_run(run_dir: str):
    meta = {}
    p = os.path.join(run_dir, "meta.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            meta = json.load(f)
    frames = {}
    with open(os.path.join(run_dir, "frames.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                w = json.loads(line)
                raw = json.loads(w["raw"]) if isinstance(w.get("raw"), str) else w
                sid = int(str(w.get("port", "s0")).lstrip("s"))
                d = array.array("h", raw["d"])                          # 긴 녹화(몇 시간)도 메모리에 들어가게
                st = bytes(min(max(int(v), 0), 255) for v in (raw.get("st") or [5] * len(raw["d"])))
            except (ValueError, KeyError, OverflowError, TypeError):
                continue
            frames.setdefault(sid, []).append((int(raw.get("ms", 0)), float(w.get("host_t", 0)), int(raw.get("seq", 0)), d, st))
    states = []
    p = os.path.join(run_dir, "state.jsonl")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    states.append(json.loads(line))
                except ValueError:
                    pass
    return meta, frames, states


def moving_mean(r, w):
    """r 의 길이 w 창 평균(창이 다 들어가는 위치만)."""
    if len(r) < w:
        return []
    s = sum(r[:w])
    out = [s / w]
    for k in range(w, len(r)):
        s += r[k] - r[k - w]
        out.append(s / w)
    return out


def band_values(r, w1, w5):
    """(1 s 평균 − 5 s 평균), 1 s 창은 5 s 창 가운데. 길이 w5 이상인 구간만."""
    if len(r) < w5:
        return []
    m5 = moving_mean(r, w5)
    off = (w5 - w1) // 2
    m1 = moving_mean(r, w1)
    return [m1[p + off] - m5[p] for p in range(len(m5))]


def apply_filter(r, name):
    """온라인(인과) 필터. 첫 몇 표본은 데워지는 구간이라 버린다."""
    if name == "raw":
        return list(r)
    if name.startswith("mean") or name.startswith("median"):
        n = int(name[-1])
        f = statistics.fmean if name.startswith("mean") else statistics.median
        return [f(r[k - n + 1:k + 1]) for k in range(n - 1, len(r))]
    if name.startswith("ema"):
        a = float(name[3:])
        y, out = None, []
        for v in r:
            y = v if y is None else y + a * (v - y)
            out.append(y)
        return out[5:]
    raise ValueError(name)


def filter_lag_frames(name):
    if name == "raw":
        return 0.0
    if name.startswith("mean") or name.startswith("median"):
        return (int(name[-1]) - 1) / 2
    a = float(name[3:])
    return (1 - a) / a


def zone_stats(series, contig, w1, w5, dur_min):
    n = len(series)
    vals = [v for v in series if v is not None]
    nv = len(vals)
    flick = sum(1 for k in range(1, n) if (series[k] is None) != (series[k - 1] is None))
    z = {"valid_rate": nv / n if n else 0.0, "flicker_per_min": flick / dur_min if dur_min > 0 else 0.0}
    if nv < 10:
        return z
    m = statistics.median(vals)
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals)
    rstd = 1.4826 * statistics.median([abs(v - m) for v in vals])
    diffs = [series[k] - series[k - 1] for k in range(1, n)
             if contig[k] and series[k] is not None and series[k - 1] is not None]
    dstd = math.sqrt(sum(d * d for d in diffs) / len(diffs) / 2) if len(diffs) >= 5 else None
    pairs = [(series[k - 1] - mean, series[k] - mean) for k in range(1, n)
             if contig[k] and series[k] is not None and series[k - 1] is not None]
    den = math.sqrt(sum(a * a for a, _ in pairs) * sum(b * b for _, b in pairs)) if pairs else 0.0
    lag1 = sum(a * b for a, b in pairs) / den if den > 0 else None
    # 유효하고 seq 가 이어지는 구간들
    runs, cur = [], []
    for k in range(n):
        if series[k] is None or (k and not contig[k]):
            if len(cur) > 1:
                runs.append(cur)
            cur = [] if series[k] is None else [series[k]]
        else:
            cur.append(series[k])
    if len(cur) > 1:
        runs.append(cur)
    ma1 = [v for r in runs for v in moving_mean(r, w1)]
    nod = [v for r in runs for v in band_values(r, w1, w5)]
    slow = statistics.pstdev(ma1) if len(ma1) >= 3 else None
    nod_rms = math.sqrt(sum(v * v for v in nod) / len(nod)) if len(nod) >= 3 else None
    thr = max(50.0, 5 * rstd)
    z.update({"median": m, "mean": mean, "std": std, "robust_std": rstd, "diff_std": dstd, "lag1_r": lag1,
              "p05": pct(vals, 0.05), "p95": pct(vals, 0.95), "slow_std": slow, "nod_rms": nod_rms,
              "outlier_rate": sum(1 for v in vals if abs(v - m) > thr) / nv})
    if dstd:
        if slow is not None:
            z["slow_ratio"] = slow / (dstd / math.sqrt(w1))
        if nod_rms is not None and w5 > w1:
            z["nod_ratio"] = nod_rms / (dstd * math.sqrt(1 / w1 - 1 / w5))
    fil = {}
    fruns, used = [], 0                         # 필터 비교는 앞 10분(5 Hz 3000 표본)이면 충분 — 몇 시간 녹화도 빨리 끝나게
    for r in runs:
        if used >= FILTER_MAX_SAMPLES:
            break
        fruns.append(r)
        used += len(r)
    for name in FILTERS:
        ys = [(apply_filter(r, name)) for r in fruns]
        flat = [y for yy in ys for y in yy]
        if len(flat) < 10:
            continue
        fnod = [v for yy in ys for v in band_values(yy, w1, w5)]
        fil[name] = {"std": math.sqrt(sum((y - mean) ** 2 for y in flat) / len(flat)),   # raw 는 std 와 같은 기준
                     "nod_rms": math.sqrt(sum(v * v for v in fnod) / len(fnod)) if len(fnod) >= 3 else None}
    z["filters"] = fil
    return z


def analyze_sensor(fl, distance_mm=None):
    seen, uniq = set(), []
    for f in sorted(fl, key=lambda f: f[1]):
        if (f[0], f[2]) not in seen:
            seen.add((f[0], f[2]))
            uniq.append(f)
    fl = uniq
    n = len(fl)
    if n < 20:
        return {"frames": n, "note": "프레임이 너무 적다(20 미만)"}
    zc = len(fl[0][3])
    w = int(round(math.sqrt(zc)))
    # 시간은 ESP32 millis(폴링 지연과 무관). 폴러 여럿의 도착 순서가 조금 뒤바뀌는 것은 millis 로 다시 줄 세운다.
    # millis 가 크게 되감기면(ESP32 재부팅) PC 시각 순서를 쓴다.
    if all(f[0] > 0 for f in fl) and all(fl[k][0] - fl[k - 1][0] > -5000 for k in range(1, n)):
        fl.sort(key=lambda f: f[0])
        ts, tbase = [f[0] / 1000.0 for f in fl], "esp32_ms"
    else:
        ts, tbase = [f[1] for f in fl], "host_t"
    dur = max(ts[-1] - ts[0], 1e-6)
    contig = [False] + [((fl[k][2] - fl[k - 1][2]) & 0xFFFF) == 1 for k in range(1, n)]
    deltas = [(fl[k][2] - fl[k - 1][2]) & 0xFFFF for k in range(1, n)]
    lost = sum(d - 1 for d in deltas if 1 < d <= 1000)
    gaps = sum(1 for d in deltas if d > 1)
    hz_cap = (n - 1) / dur
    hz = (n - 1 + lost) / dur                       # 센서가 실제로 낸 속도(놓친 프레임 포함)
    w1 = max(2, int(round(hz * 1.0)))
    w5 = max(w1 + 2, int(round(hz * 5.0)))
    gap_ms = [(ts[k] - ts[k - 1]) * 1000 for k in range(1, n)]
    st_hist = {}
    for f in fl:
        for k in f[4]:
            st_hist[k] = st_hist.get(k, 0) + 1
    zones = []
    for i in range(zc):
        series = [f[3][i] if is_valid(f[3][i], f[4][i]) else None for f in fl]
        zones.append(zone_stats(series, contig, w1, w5, dur / 60))
    good = [i for i, z in enumerate(zones) if z["valid_rate"] >= GOOD_VALID and "std" in z]

    def gmed(key):
        return med([zones[i].get(key) for i in good])

    # 공통 성분: 프레임마다 좋은 칸들의 (값 − 칸 중앙값) 평균
    cm, cm_t = [], []
    for k, f in enumerate(fl):
        dev = [f[3][i] - zones[i]["median"] for i in good if is_valid(f[3][i], f[4][i])]
        if len(dev) >= max(4, len(good) // 2):
            cm.append(statistics.fmean(dev))
            cm_t.append(ts[k])
    common_std = statistics.pstdev(cm) if len(cm) >= 3 else None
    drift = drift_se = None
    if len(cm) >= 10:
        tm = statistics.fmean(cm_t)
        vm = statistics.fmean(cm)
        den = sum((t - tm) ** 2 for t in cm_t)
        if den > 0:
            b = sum((t - tm) * (v - vm) for t, v in zip(cm_t, cm)) / den
            res = [v - vm - b * (t - tm) for t, v in zip(cm_t, cm)]
            drift = 60 * b
            drift_se = 60 * math.sqrt(sum(r * r for r in res) / max(len(res) - 2, 1) / den)   # 기울기 표준오차
    std_med = gmed("std")
    common_ratio = (common_std / (std_med / math.sqrt(len(good)))) if (common_std and std_med and good) else None
    cen = med([zones[r * w + c].get("median") for r in range(2, 6) for c in range(2, 6)
               if zones[r * w + c]["valid_rate"] >= 0.5])
    sim = sim_sigma(cen) if cen else None
    fil = {}
    for name in FILTERS:
        fs = med([zones[i]["filters"].get(name, {}).get("std") for i in good])
        fn = med([zones[i]["filters"].get(name, {}).get("nod_rms") for i in good])
        if fs is not None:
            fil[name] = {"std_mm": rnd(fs), "nod_rms_mm": rnd(fn), "lag_ms": round(filter_lag_frames(name) * 1000 / hz)}
    worst = sorted(good, key=lambda i: -zones[i]["std"])[:5]
    return {
        "frames": n, "duration_s": rnd(dur, 1), "time_base": tbase, "hz": rnd(hz), "hz_captured": rnd(hz_cap),
        "seq_gaps": gaps, "lost_frames": lost, "lost_pct": rnd(100 * lost / (n + lost), 1),
        "frame_gap_ms": {"median": rnd(med(gap_ms), 1), "p95": rnd(pct(gap_ms, 0.95), 1), "max": rnd(max(gap_ms), 1)},
        "window_frames": {"w1": w1, "w5": w5},
        "good_zones": len(good), "valid_rate_median": rnd(med([z["valid_rate"] for z in zones]), 4),
        "valid_rate_min": rnd(min(z["valid_rate"] for z in zones), 4),
        "weak_zones": [i for i, z in enumerate(zones) if z["valid_rate"] < 0.98],
        "flicker_zones": [i for i, z in enumerate(zones) if z["flicker_per_min"] >= 1.0],
        "std_mm": rnd(std_med), "std_p90_mm": rnd(pct([zones[i]["std"] for i in good], 0.9)),
        "robust_std_mm": rnd(gmed("robust_std")), "diff_std_mm": rnd(gmed("diff_std")),
        "slow_std_mm": rnd(gmed("slow_std")), "slow_ratio": rnd(gmed("slow_ratio")),
        "nod_rms_mm": rnd(gmed("nod_rms")), "nod_ratio": rnd(gmed("nod_ratio")),
        "lag1_r": rnd(gmed("lag1_r"), 3), "outlier_rate": rnd(gmed("outlier_rate"), 4),
        "common_std_mm": rnd(common_std), "common_ratio": rnd(common_ratio), "drift_mm_per_min": rnd(drift),
        "drift_se_mm_per_min": rnd(drift_se),
        "center4x4_mm": rnd(cen, 1), "distance_error_mm": rnd(cen - distance_mm, 1) if (cen and distance_mm) else None,
        "sim_sigma_mm": rnd(sim), "std_vs_sim": rnd(std_med / sim) if (std_med and sim) else None,
        "model_input_std": rnd(std_med / MAX_MM, 5) if std_med else None,
        "worst_zones": [{"zone": i, "row": i // w, "col": i % w, "std_mm": rnd(zones[i]["std"]),
                         "median_mm": rnd(zones[i]["median"], 1)} for i in worst],
        "status_hist": {str(k): v for k, v in sorted(st_hist.items())},
        "filters": fil,
        "zones": [{k: (rnd(v, 4) if isinstance(v, float) else v) for k, v in z.items() if k != "filters"} for z in zones],
    }


def posture_summary(states):
    if not states:
        return None
    smooth, inst, valid, sw, prev = {}, {}, 0, 0, None
    probs_sum, model = {}, None
    for s in states:
        j = s.get("state") or {}
        model = model or (j.get("model") or {}).get("file")
        if not j.get("valid"):
            continue
        valid += 1
        p = j.get("posture") or "?"
        smooth[p] = smooth.get(p, 0) + 1
        ip = (j.get("instant") or {}).get("posture") or "?"
        inst[ip] = inst.get(ip, 0) + 1
        if prev is not None and p != prev:
            sw += 1
        prev = p
        for k, v in (j.get("probs") or {}).items():
            probs_sum[k] = probs_sum.get(k, 0.0) + float(v)
    dur_min = (states[-1]["host_t"] - states[0]["host_t"]) / 60 if len(states) > 1 else 0
    frac = lambda h: {k: round(v / valid, 3) for k, v in sorted(h.items(), key=lambda kv: -kv[1])} if valid else {}
    return {"samples": len(states), "valid": valid, "model": model, "posture": frac(smooth), "instant": frac(inst),
            "mean_probs": {k: round(v / valid, 3) for k, v in probs_sum.items()} if valid else {},
            "switches_per_min": round(sw / dur_min, 2) if dur_min > 0 else None,
            "drowsy_frac": round(smooth.get("drowsy", 0) / valid, 3) if valid else None,
            "drowsy_instant_frac": round(inst.get("drowsy", 0) / valid, 3) if valid else None}


def verdicts(s):
    out = []
    if s.get("lost_pct") is not None and s["lost_pct"] > 5:
        out.append(f"프레임 {s['lost_pct']:.0f} % 놓침 — 끄덕임 대역 지표가 낮게 나온다. 10 % 넘으면 다시 녹화(뷰어·Pi 5 프리뷰 끄기).")
    if s.get("std_mm") and s["std_mm"] > 60:
        out.append(f"σ 가 {s['std_mm']:.0f} mm — 센서 잡음이라기엔 너무 크다. 녹화 중 시야 안에서 뭔가 움직였을 가능성이 크다"
                   "(사람·문·커튼·장치 흔들림). 벽 측정이면 다시 녹화한다.")
    if s.get("std_vs_sim"):
        out.append(f"σ {s['std_mm']:.1f} mm = 시뮬 기본 잡음({s['sim_sigma_mm']:.1f} mm, 같은 거리 흰 벽)의 {s['std_vs_sim']:.1f} 배. "
                   f"모델 입력 단위로는 ±{s['model_input_std']:.4f}.")
    sr, nr = s.get("slow_ratio"), s.get("nod_ratio")
    if sr is not None:
        if sr >= 1.5:
            out.append(f"느린 성분 있음: 1 s 평균의 흔들림이 흰 잡음 기대값의 {sr:.1f} 배 — 짧은 평균 필터로는 덜 줄어든다.")
        else:
            out.append(f"흔들림 대부분이 프레임마다 새로 생기는 잡음(slow_ratio {sr:.1f}) — 3~5 프레임 필터로 줄어든다.")
    if nr is not None and nr >= 1.5:
        out.append(f"0.2~1 Hz(끄덕임 대역) 성분이 흰 잡음 기대값의 {nr:.1f} 배 ({s['nod_rms_mm']:.1f} mm RMS).")
    if s.get("common_ratio") and s["common_ratio"] >= 3:
        out.append(f"칸들이 같이 움직이는 성분이 큼(독립 대비 {s['common_ratio']:.1f} 배) — 프레임 전체 거리 오프셋이 흔들린다.")
    dr, se = s.get("drift_mm_per_min"), s.get("drift_se_mm_per_min") or 0.0
    # 우연한 기울기(짧은 녹화)는 빼고, 녹화 동안 σ 의 30 % 넘게 흘러간 것만 알린다
    if dr is not None and abs(dr) >= 2 and abs(dr) >= 4 * se and abs(dr) * s["duration_s"] / 60 >= 0.3 * (s.get("std_mm") or 0):
        out.append(f"드리프트 {dr:+.1f} mm/분 — 켠 지 얼마 안 됐거나 온도 변화. 5분 데운 뒤 다시 잰다.")
    if s.get("flicker_zones"):
        out.append(f"유효↔무효가 분당 1번 넘게 바뀌는 칸 {len(s['flicker_zones'])}개: {s['flicker_zones']} "
                   f"(모델 입력 채널 1 이 깜빡인다).")
    if s.get("outlier_rate") and s["outlier_rate"] > 0.005:
        out.append(f"튀는 값 {100 * s['outlier_rate']:.2f} % — 중앙값 필터가 평균보다 낫다.")
    raw, best = (s.get("filters") or {}).get("raw"), None
    for name, f in (s.get("filters") or {}).items():
        if name != "raw" and f.get("std_mm") is not None and (best is None or f["std_mm"] < best[1]["std_mm"]):
            best = (name, f)
    if raw and best and best[1]["std_mm"] < 0.95 * raw["std_mm"]:
        out.append(f"필터 비교: 원본 σ {raw['std_mm']:.1f} → {best[0]} {best[1]['std_mm']:.1f} mm (지연 {best[1]['lag_ms']} ms).")
    elif raw and best:
        out.append(f"필터 비교: 3~5 프레임 필터로 σ 가 거의 안 준다(원본 {raw['std_mm']:.1f} mm) — 흔들림이 느리거나 장면이 움직였다.")
    return out


def run_problems(rep, kind="wall"):
    """다시 녹화할 만한 문제. 빈 목록이면 OK."""
    out = []
    if not rep["sensors"]:
        out.append("프레임이 하나도 없다")
    for sid, s in sorted(rep["sensors"].items()):
        if "std_mm" not in s:
            out.append(f"센서 {sid}: {s.get('note')} ({s.get('frames')} 프레임)")
            continue
        if s["lost_pct"] > 10:
            out.append(f"센서 {sid}: 프레임 {s['lost_pct']:.0f} % 놓침(10 % 초과)")
        if kind == "wall" and s["std_mm"] and s["std_mm"] > 60:
            out.append(f"센서 {sid}: σ {s['std_mm']:.0f} mm — 벽 녹화 중 시야 안에서 뭔가 움직였다")
    stuck = (rep.get("meta") or {}).get("sensors_stuck_at_start") or []
    if stuck:
        out.append(f"센서 {stuck} 은 처음부터 멈춰 있었다")
    return out


def fmt_grid(vals, f, w=8):
    return ["  " + " ".join(("   --" if v is None else f(v)) for v in vals[r * w:(r + 1) * w]) for r in range(len(vals) // w)]


def write_summary(run_dir, rep):
    m = rep.get("meta") or {}
    L = [f"# ToF 잡음 측정 — {m.get('label', os.path.basename(run_dir))}", "",
         f"- 폴더: `{os.path.basename(run_dir)}` · 시작 {m.get('started', '?')} · 실제 {m.get('seconds_actual', '?')} s",
         f"- 설정 거리 {m.get('distance_cm', '?')} cm · 조명 {m.get('light') or '?'} · 메모 {m.get('note') or '-'}",
         f"- 모델(서비스) {((m.get('service') or {}).get('model') or {}).get('file', '-')} · "
         f"폴링 지연 중앙값 {(m.get('poll_latency_ms') or {}).get('median', '?')} ms", ""]
    hdr = ["센서", "프레임(Hz)", "놓침 %", "좋은 칸", "σ mm", "시뮬 대비", "프레임차 σ", "1 s 평균 σ (배)", "끄덕임 대역 RMS (배)",
           "lag1", "공통(배)", "드리프트 mm/분", "가운데 cm", "깜빡임 칸"]
    L += ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for sid, s in sorted(rep["sensors"].items()):
        if "std_mm" not in s:
            L.append(f"| {sid} | {s.get('frames')} | {s.get('note', '')} |")
            continue
        L.append(f"| {sid} {NAMES.get(int(sid), '')} | {s['frames']} ({s['hz']}) | {s['lost_pct']} | {s['good_zones']}/64 | {s['std_mm']} | "
                 f"{s['std_vs_sim']}× | {s['diff_std_mm']} | {s['slow_std_mm']} ({s['slow_ratio']}) | "
                 f"{s['nod_rms_mm']} ({s['nod_ratio']}) | {s['lag1_r']} | {s['common_ratio']} | {s['drift_mm_per_min']} | "
                 f"{None if s['center4x4_mm'] is None else round(s['center4x4_mm'] / 10, 1)} | {len(s['flicker_zones'])} |")
    for sid, s in sorted(rep["sensors"].items()):
        if "std_mm" not in s:
            continue
        L += ["", f"## 센서 {sid} {NAMES.get(int(sid), '')}", ""]
        L += [f"- {v}" for v in verdicts(s)]
        L += ["", "필터(칸별 온라인 적용, 좋은 칸 중앙값): σ mm / 끄덕임 대역 mm / 지연 ms", "",
              "| 필터 | σ | 끄덕임 대역 | 지연 |", "|---|---|---|---|"]
        for name, f in s["filters"].items():
            L.append(f"| {name} | {f['std_mm']} | {f['nod_rms_mm']} | {f['lag_ms']} |")
        z = s["zones"]
        L += ["", "칸별 σ (mm, row 0 = 위, col 0 = 센서가 보는 왼쪽)", "```"]
        L += fmt_grid([q.get("std") for q in z], lambda v: f"{v:5.1f}")
        L += ["```", "", "칸별 유효율 (%)", "```"]
        L += fmt_grid([q.get("valid_rate") for q in z], lambda v: f"{100 * v:5.1f}")
        L += ["```", "", "칸별 중앙값 (cm)", "```"]
        L += fmt_grid([q.get("median") for q in z], lambda v: f"{v / 10:5.1f}")
        L += ["```", "", f"상태값 분포: {s['status_hist']}"]
    p = rep.get("posture")
    if p:
        L += ["", "## 그동안 자세 서비스 출력", "",
              f"- 표본 {p['samples']} (유효 {p['valid']}), 모델 `{p['model']}`, 라벨 바뀜 {p['switches_per_min']}/분",
              f"- 평활 자세 비율: {p['posture']}", f"- 순간 자세 비율: {p['instant']}",
              f"- drowsy 비율: 평활 {p['drowsy_frac']} · 순간 {p['drowsy_instant_frac']}"]
    if rep.get("sensor_delta_mm") is not None:
        L += ["", f"두 센서 가운데 4×4 차(A − B): {rep['sensor_delta_mm']:.0f} mm"]
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def analyze(run_dir: str, quiet: bool = False) -> dict:
    meta, frames, states = load_run(run_dir)
    dmm = float(meta["distance_cm"]) * 10 if meta.get("distance_cm") else None
    rep = {"tool": "tof_wall_noise.py", "version": TOOL_VERSION, "run": os.path.basename(os.path.normpath(run_dir)),
           "meta": meta, "sensors": {}}
    for sid in sorted(frames):
        rep["sensors"][str(sid)] = analyze_sensor(frames[sid], dmm)
    c0 = rep["sensors"].get("0", {}).get("center4x4_mm")
    c1 = rep["sensors"].get("1", {}).get("center4x4_mm")
    rep["sensor_delta_mm"] = (c0 - c1) if (c0 is not None and c1 is not None) else None
    rep["posture"] = posture_summary(states)
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    write_summary(run_dir, rep)
    if not quiet:
        print_report(run_dir, rep)
    return rep


def print_report(run_dir, rep):
    print(f"\n{rep['run']}  ({(rep.get('meta') or {}).get('distance_cm', '?')} cm)")
    for sid, s in sorted(rep["sensors"].items()):
        if "std_mm" not in s:
            print(f"  센서 {sid}: {s}")
            continue
        print(f"  센서 {sid} {NAMES.get(int(sid), '')}: {s['frames']} fr {s['hz']} Hz (놓침 {s['lost_pct']} %) | 좋은 칸 {s['good_zones']}/64 | "
              f"σ {s['std_mm']} mm · 프레임차 {s['diff_std_mm']} · 1s평균 {s['slow_std_mm']} ({s['slow_ratio']}배) · "
              f"끄덕임대역 {s['nod_rms_mm']} ({s['nod_ratio']}배) | 가운데 {s['center4x4_mm']} mm")
        for v in verdicts(s):
            print("    - " + v)
    p = rep.get("posture")
    if p:
        print(f"  자세 서비스: {p['posture']}  (drowsy 평활 {p['drowsy_frac']} · 순간 {p['drowsy_instant_frac']})")
    print(f"  → {os.path.join(run_dir, 'summary.md')}")


def report_of(run_dir):
    p = os.path.join(run_dir, "report.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            rep = json.load(f)
        if rep.get("version") == TOOL_VERSION:
            return rep
    return analyze(run_dir, quiet=True)


# ───────────────────────── 거리별 맞춤 ─────────────────────────

def fit_sigma(points):
    """σ² = a + b·d²(d m) 최소제곱 → (σ0 mm, k mm/m). b<0 이면 k=0."""
    xs = [(d / 1000.0) ** 2 for d, _ in points]
    ys = [s * s for _, s in points]
    n = len(xs)
    if n < 3:
        return None
    xm, ym = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - xm) ** 2 for x in xs)
    b = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den if den > 0 else 0.0
    if b < 0:
        b, a = 0.0, ym
    else:
        a = ym - b * xm
    return math.sqrt(max(a, 0.0)), math.sqrt(b)


def fit(run_dirs, out=None, refl=SIM_WALL_REFL, quiet=False) -> dict:
    per, rows, skipped = {}, [], []
    for rd in run_dirs:
        rep = report_of(rd)
        for sid, s in rep["sensors"].items():
            if "zones" not in s:
                skipped.append(f"{rep['run']} 센서 {sid}: {s.get('note')}")
                continue
            if (s.get("std_mm") or 0) > 60 or (s.get("lost_pct") or 0) > 10:
                skipped.append(f"{rep['run']} 센서 {sid}: σ {s.get('std_mm')} mm · 놓침 {s.get('lost_pct')} %")
                continue
            p = per.setdefault(sid, {"std": [], "diff": [], "drop": [], "lag1": []})
            for z in s["zones"]:
                # 녹화 중 잠깐 무언가 지나간 칸(σ 가 그 녹화 중앙값의 3배 넘음)은 맞춤에서 뺀다
                if z.get("valid_rate", 0) >= GOOD_VALID and z.get("std") is not None and z.get("median")                         and z["std"] <= 3 * s["std_mm"]:
                    p["std"].append((z["median"], z["std"]))
                    if z.get("diff_std"):
                        p["diff"].append((z["median"], z["diff_std"]))
                    if z.get("lag1_r") is not None:
                        p["lag1"].append(z["lag1_r"])
            p["drop"] += [1 - z["valid_rate"] for z in s["zones"] if z.get("valid_rate", 0) >= 0.5]
            rows.append({"run": rep["run"], "sensor": sid, "distance_cm": (rep.get("meta") or {}).get("distance_cm"),
                         "center_mm": s.get("center4x4_mm"), "std_mm": s.get("std_mm"), "diff_std_mm": s.get("diff_std_mm"),
                         "slow_ratio": s.get("slow_ratio"), "nod_ratio": s.get("nod_ratio"), "lost_pct": s.get("lost_pct"),
                         "valid_rate_median": s.get("valid_rate_median"), "sim_sigma_mm": s.get("sim_sigma_mm")})
    res = {"runs": [os.path.basename(os.path.normpath(r)) for r in run_dirs], "skipped": skipped,
           "reflectance_assumed": refl, "per_run": rows, "sensors": {}}
    allp = {"std": [], "diff": [], "drop": [], "lag1": []}
    for sid, p in sorted(per.items()):
        for k in allp:
            allp[k] += p[k]
    for sid, p in list(sorted(per.items())) + [("all", allp)]:
        ds = [d for d, _ in p["std"]]
        if not ds:
            continue
        ft, fd = fit_sigma(p["std"]), fit_sigma(p["diff"])
        e = {"points": len(p["std"]), "d_range_mm": [round(min(ds)), round(max(ds))],
             "dropout_mean": rnd(statistics.fmean(p["drop"]), 4) if p["drop"] else None,
             "lag1_r_median": rnd(med(p["lag1"]), 3)}
        if ft:
            e.update({"sigma0_mm": rnd(ft[0]), "k_mm_per_m": rnd(ft[1]), "sigma1_mm": rnd(ft[1] * math.sqrt(refl))})
        if fd:
            e.update({"white_sigma0_mm": rnd(fd[0]), "white_k_mm_per_m": rnd(fd[1]), "white_sigma1_mm": rnd(fd[1] * math.sqrt(refl))})
        if max(ds) - min(ds) < 200:
            e["warning"] = "거리 폭이 20 cm 미만 — 거리에 따른 증가(k)는 믿기 어렵다. 다른 거리 녹화를 더한다."
        res["sensors"][sid] = e
    a = res["sensors"].get("all", {})
    if "sigma0_mm" in a:
        res["l8params"] = (f"L8Params(sigma0_mm={a['sigma0_mm']}, sigma1_mm={a['sigma1_mm']}, "
                           f"dropout={a['dropout_mean'] if a['dropout_mean'] is not None else SIM_DROPOUT})")
        if (a.get("lag1_r_median") or 0) > 0.2:
            res["ar1_note"] = (f"칸 잡음의 1 프레임 자기상관 {a['lag1_r_median']} — 시뮬처럼 프레임마다 독립인 잡음이 아니다. "
                               f"시뮬 잡음에 AR(1) ρ≈{a['lag1_r_median']} 를 넣어야 느린 성분이 재현된다.")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out or os.path.join(os.path.dirname(os.path.normpath(run_dirs[0])) or ".", f"fit_{stamp}")
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    L = ["# ToF 잡음 — 거리별 맞춤", "", f"맞춤에 쓴 녹화: {', '.join(res['runs'])}"]
    if skipped:
        L.append(f"뺀 것: {'; '.join(skipped)}")
    L += ["", "| 녹화 | 센서 | 설정 cm | 가운데 mm | σ mm | 프레임차 σ | 시뮬 σ | slow 배 | 끄덕임 배 | 놓침 % | 유효율 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['run']} | {r['sensor']} | {r['distance_cm']} | {r['center_mm']} | {r['std_mm']} | {r['diff_std_mm']} | "
                 f"{r['sim_sigma_mm']} | {r['slow_ratio']} | {r['nod_ratio']} | {r['lost_pct']} | {r['valid_rate_median']} |")
    L += ["", f"맞춤 σ² = σ0² + (k·d)² (칸별 점, 반사율 {refl} 가정으로 시뮬 σ1 = k·√반사율):", "",
          "| 센서 | 점 | 거리 mm | σ0 | k mm/m | σ1(시뮬) | 흰 잡음 σ0 | 흰 잡음 σ1 | dropout | lag1 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for sid, e in res["sensors"].items():
        L.append(f"| {sid} | {e['points']} | {e['d_range_mm']} | {e.get('sigma0_mm')} | {e.get('k_mm_per_m')} | {e.get('sigma1_mm')} | "
                 f"{e.get('white_sigma0_mm')} | {e.get('white_sigma1_mm')} | {e['dropout_mean']} | {e['lag1_r_median']} |"
                 + (f" ⚠ {e['warning']}" if e.get("warning") else ""))
    L += ["", f"시뮬 기본값: L8Params(sigma0_mm={SIM_SIGMA0}, sigma1_mm={SIM_SIGMA1}, dropout={SIM_DROPOUT})"]
    if res.get("l8params"):
        L.append(f"실측 맞춤: {res['l8params']}")
    if res.get("ar1_note"):
        L.append(res["ar1_note"])
    with open(out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    if not quiet:
        print(L[2] + ("\n" + L[3] if skipped else ""))
        print("\n".join(L[-3:]) if res.get("l8params") else "맞춤할 점이 부족하다")
        print(f"→ {out}.md")
    res["_out"] = out
    return res


# ───────────────────────── 벽 vs 사람 ─────────────────────────

def person_zones(still_s, empty_s=None):
    """사람 칸 = 25~110 cm 에 안정적으로 잡힌 칸. 빈 자리 녹화가 있으면 그때보다 10 cm 넘게 가까워진 칸만(책상·의자 등받이 제외)."""
    out = []
    for i, z in enumerate(still_s["zones"]):
        if z.get("valid_rate", 0) < 0.8 or not z.get("median") or not (250 <= z["median"] <= 1100):
            continue
        if empty_s and "zones" in empty_s:
            e = empty_s["zones"][i]
            if e.get("valid_rate", 0) >= 0.5 and e.get("median") and e["median"] - z["median"] < 100:
                continue
        out.append(i)
    return out


def compare(wall_dir: str, still_dir: str, empty_dir=None, out=None, quiet=False) -> dict:
    wr, sr = report_of(wall_dir), report_of(still_dir)
    er = report_of(empty_dir) if empty_dir else None
    res = {"wall": wr["run"], "still": sr["run"], "empty": er["run"] if er else None, "sensors": {},
           "posture_wall": wr.get("posture"), "posture_still": sr.get("posture"),
           "posture_empty": er.get("posture") if er else None}
    for sid, s in sorted(sr["sensors"].items()):
        w = wr["sensors"].get(sid)
        if "zones" not in s or not w or "zones" not in w:
            continue
        pz = person_zones(s, (er or {}).get("sensors", {}).get(sid))
        bz = [i for i, z in enumerate(s["zones"]) if z.get("valid_rate", 0) >= 0.8 and z.get("median") and z["median"] > 1200]
        if not pz:
            res["sensors"][sid] = {"note": "사람 칸이 없다 — 사람이 시야에 없었거나 빈 자리와 차이가 없다"}
            continue
        g = lambda zs, key: rnd(med([s["zones"][i].get(key) for i in zs]))
        e = {"person_zones": len(pz), "person_zone_ids": pz, "background_zones": len(bz),
             "person": {k: g(pz, k) for k in ("median", "std", "diff_std", "slow_ratio", "nod_rms", "nod_ratio")},
             "background": {k: g(bz, k) for k in ("std", "diff_std", "nod_rms", "nod_ratio")} if bz else None,
             "wall": {"std": w.get("std_mm"), "diff_std": w.get("diff_std_mm"), "nod_rms": w.get("nod_rms_mm"),
                      "nod_ratio": w.get("nod_ratio"), "center_mm": w.get("center4x4_mm")}}
        pn, wn = e["person"]["nod_ratio"], e["wall"]["nod_ratio"]
        pr, wrm = e["person"]["nod_rms"], e["wall"]["nod_rms"]
        if pn and wn and pr is not None and wrm is not None:
            ex = pn / wn
            e["nod_excess"] = rnd(ex)
            if ex >= 2 and pr - wrm >= 2:
                e["verdict"] = (f"사람 몸의 느린 흔들림이 센서 잡음보다 크다(0.2~1 Hz 성분 {pr} mm vs 벽 {wrm} mm, 흰 잡음 대비 {pn}배 vs {wn}배). "
                                "필터만으로는 안 없어진다 — 시뮬 사람에게 미세 흔들림을 넣거나 판정 쪽에서 걸러야 한다.")
            elif ex <= 1.3:
                e["verdict"] = (f"앉은 사람 칸의 흔들림이 벽 잡음과 비슷하다(0.2~1 Hz {pr} mm vs 벽 {wrm} mm). "
                                "센서 잡음이 주원인 — 필터·펌웨어 평균으로 줄일 수 있다.")
            else:
                e["verdict"] = f"사람 흔들림과 센서 잡음이 섞여 있다(0.2~1 Hz {pr} mm vs 벽 {wrm} mm, 흰 잡음 대비 {pn}배 vs {wn}배)."
        res["sensors"][sid] = e
    out = out or os.path.join(still_dir, f"compare_{wr['run']}")
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    L = [f"# 벽({res['wall']}) vs {sr['run']}" + (f" (빈 자리 {res['empty']} 기준)" if res["empty"] else ""), "",
         "| 센서 | 사람 칸 | 사람 σ | 사람 프레임차 σ | 사람 0.2~1 Hz (배) | 벽 σ | 벽 0.2~1 Hz (배) | 초과 배 |", "|---|---|---|---|---|---|---|---|"]
    for sid, e in res["sensors"].items():
        if "person" not in e:
            L.append(f"| {sid} | {e.get('note')} |")
            continue
        p, w = e["person"], e["wall"]
        L.append(f"| {sid} | {e['person_zones']} | {p['std']} | {p['diff_std']} | {p['nod_rms']} ({p['nod_ratio']}) | "
                 f"{w['std']} | {w['nod_rms']} ({w['nod_ratio']}) | {e.get('nod_excess')} |")
    for sid, e in res["sensors"].items():
        if e.get("verdict"):
            L.append(f"\n- 센서 {sid}: {e['verdict']}")
    for name, title in (("posture_wall", "벽"), ("posture_empty", "빈 자리"), ("posture_still", sr["run"])):
        p = res.get(name)
        if p:
            L.append(f"\n- {title} 자세 출력: {p['posture']} (drowsy 평활 {p['drowsy_frac']} · 순간 {p['drowsy_instant_frac']})")
    with open(out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    if not quiet:
        print("\n".join(L))
        print(f"→ {out}.md")
    res["_out"] = out
    return res


# ───────────────────────── 안내 진행(session) ─────────────────────────

class Reminder(threading.Thread):
    """Enter 를 기다리는 동안 20 s 마다 소리로 다시 부른다(최대 10분)."""

    def __init__(self, beep_fn, period=20.0, times=30):
        super().__init__(daemon=True)
        self.beep_fn, self.period, self.times = beep_fn, period, times
        self.stop = threading.Event()

    def run(self):
        for _ in range(self.times):
            if self.stop.wait(self.period):
                return
            self.beep_fn("attention")


def session(raw_url: str, api_url: str, out: str = "noise_runs", session_dir=None, steps=None, notes=None,
            ask=input, beep_fn=beep, delay: float = 5.0, durations=None, aim_seconds: float = 3.0,
            interval: float = 0.08, pollers: int = 3, tunnel=None) -> str:
    """기본 벽 45/50/60 cm(steps 로 빈 자리·가만히·평소 작업 추가). 단계마다 조준 격자 → 소리 → Enter → 카운트다운 → 녹화 → 소리."""
    steps = steps or WALL_STEPS
    plan = [dict(p) for p in PLAN if p["label"] in steps]
    for p in plan:
        if durations and p["label"] in durations:
            p["seconds"] = durations[p["label"]]
    sess = session_dir or os.path.join(out, "session_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(sess, exist_ok=True)
    log_path = os.path.join(sess, "session.json")
    log = {"runs": {}, "bad": [], "notes": {}}
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
    log.setdefault("started", time.strftime("%Y-%m-%d %H:%M:%S"))

    def save():
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)

    total = sum(p["seconds"] for p in plan)
    print(f"\n안내 진행 — {len(plan)}단계, 녹화만 {mmss(total)} (준비 포함 약 {total / 60 + 3 * len(plan):.0f}분), "
          f"예상 {total / 60 * MB_PER_MIN:.0f} MB → {sess}")
    print("단계가 끝나면 소리가 나고, 다음 준비가 되면 Enter 를 누른다. Enter 뒤 5초 카운트다운 후 녹화가 시작된다.\n")
    if notes is None:
        notes = dict(log.get("notes") or {})
        for key, q in (("light", "조명(예: 천장 형광등)"), ("wall", "벽 재질·색(예: 흰 벽지)"),
                       ("tilt", "지그 기울기 대략(예: 위로 10°, 모르면 Enter)"), ("chin", "평소 앉을 때 기기→턱 거리 cm(모르면 Enter)")):
            if not notes.get(key):
                # 파이프로 들어온 다른 인코딩 글자(서로게이트)는 ? 로 — 저장에서 죽지 않게
                notes[key] = ask(f"{q} > ").replace("﻿", "").strip().encode("utf-8", "replace").decode("utf-8")
    log["notes"] = notes
    save()
    try:
        for idx, p in enumerate(plan, 1):
            label, kind, d, secs = p["label"], p["kind"], p["distance_cm"], p["seconds"]
            guide = GUIDE["wall"].format(d=d) if kind == "wall" else GUIDE[label if label in GUIDE else "still"]
            note = (f"벽 {notes.get('wall') or '?'}, 지그 {notes.get('tilt') or '?'}" if kind == "wall"
                    else f"기기→턱 {notes.get('chin') or '?'} cm, 지그 {notes.get('tilt') or '?'}")
            while True:
                if tunnel and not tunnel.alive():
                    print("터널이 끊겼다 — 다시 연다")
                    raw_url, api_url = tunnel.open()
                print(f"\n━━ [{idx}/{len(plan)}] {label} — {mmss(secs)} ━━\n{guide}")
                aim(raw_url, aim_seconds, d, "wall" if kind == "wall" else "person")
                beep_fn("attention")
                rem = Reminder(beep_fn)
                rem.start()
                try:
                    a = ask("준비되면 Enter · 격자 다시 보기 r · 이 단계 건너뛰기 s · 끝내기 q > ").strip().lower()
                finally:
                    rem.stop.set()
                if a == "r":
                    continue
                if a == "s":
                    print(f"{label} 건너뜀")
                    break
                if a == "q":
                    raise KeyboardInterrupt
                try:
                    rd = record(raw_url, api_url, secs, label, sess, d, notes.get("light", ""), note,
                                interval, pollers, delay, beep_fn, kind)
                except RecordError as e:
                    beep_fn("warn")
                    print(f"녹화 못 함: {e}")
                    if ask("고친 뒤 다시 Enter · 건너뛰기 s > ").strip().lower() == "s":
                        break
                    continue
                rep = analyze(rd, quiet=True)
                for sid, s in sorted(rep["sensors"].items()):
                    if "std_mm" in s:
                        print(f"  센서 {sid}: σ {s['std_mm']} mm · 놓침 {s['lost_pct']} % · 끄덕임 대역 {s['nod_rms_mm']} mm"
                              + (f" · drowsy {rep['posture']['drowsy_frac']}" if rep.get("posture") else ""))
                probs = run_problems(rep, kind)
                if probs:
                    beep_fn("warn")
                    print("  문제: " + " / ".join(probs))
                    if ask("다시 녹화하려면 Enter · 그대로 쓰려면 n > ").strip().lower() != "n":
                        bad = os.path.join(sess, "_bad")
                        os.makedirs(bad, exist_ok=True)
                        shutil.move(rd, os.path.join(bad, os.path.basename(rd)))
                        log["bad"].append(os.path.basename(rd))
                        save()
                        continue
                beep_fn("done")
                log["runs"][label] = os.path.basename(rd)
                save()
                print(f"  {label} 끝 → {rd}")
                break
    except KeyboardInterrupt:
        print("\n여기서 끝낸다 — 지금까지 녹화로 정리한다")
    return finish_session(sess, log, beep_fn)


def finish_session(sess, log, beep_fn=no_beep) -> str:
    runs = {k: os.path.join(sess, v) for k, v in log.get("runs", {}).items() if os.path.isdir(os.path.join(sess, v))}
    walls = [v for k, v in runs.items() if k.startswith("wall")]
    L = [f"# ToF 잡음 측정 세션 — {os.path.basename(sess)}", "", f"- 시작 {log.get('started')} · 정리 {time.strftime('%Y-%m-%d %H:%M:%S')}",
         f"- 메모: {log.get('notes')}", f"- 다시 녹화하느라 뺀 것: {log.get('bad') or '-'}", "",
         "| 단계 | 폴더 | 센서 | σ mm | 놓침 % | 1 s 평균 (배) | 끄덕임 대역 mm (배) | 깜빡임 칸 | drowsy 평활 · 순간 |", "|---|---|---|---|---|---|---|---|---|"]
    for k, rd in runs.items():
        rep = report_of(rd)
        p = rep.get("posture") or {}
        for sid, s in sorted(rep["sensors"].items()):
            if "std_mm" not in s:
                L.append(f"| {k} | {rep['run']} | {sid} | {s.get('note')} |")
                continue
            L.append(f"| {k} | {rep['run']} | {sid} | {s['std_mm']} | {s['lost_pct']} | {s['slow_std_mm']} ({s['slow_ratio']}) | "
                     f"{s['nod_rms_mm']} ({s['nod_ratio']}) | {len(s['flicker_zones'])} | {p.get('drowsy_frac')} · {p.get('drowsy_instant_frac')} |")
    if walls:
        r = fit(walls, out=os.path.join(sess, "fit"), quiet=True)
        L += ["", "## 거리별 맞춤", "", f"- {r.get('l8params', '맞춤할 점 부족')} (시뮬 기본 L8Params(sigma0_mm=3.0, sigma1_mm=4.0, dropout=0.003))"]
        if r.get("ar1_note"):
            L.append(f"- {r['ar1_note']}")
        L.append("- 자세히: `fit.md`")
    ref = None
    for k, rd in runs.items():
        if k.startswith("wall"):
            dcm = (report_of(rd).get("meta") or {}).get("distance_cm") or 0
            if ref is None or abs(dcm - 60) < abs(ref[1] - 60):
                ref = (rd, dcm)
    if ref:
        for k in ("still", "work"):
            if k in runs:
                c = compare(ref[0], runs[k], runs.get("empty"), out=os.path.join(sess, f"compare_{k}"), quiet=True)
                L += ["", f"## 벽 {ref[1]} cm vs {k}", ""]
                for sid, e in c["sensors"].items():
                    L.append(f"- 센서 {sid}: {e.get('verdict') or e.get('note') or '판정 없음'}")
                if c.get("posture_still"):
                    L.append(f"- 자세 출력: {c['posture_still']['posture']}")
                L.append(f"- 자세히: `compare_{k}.md`")
    left = [x for x in PERSON_STEPS if x not in runs]
    if walls and left:
        L += ["", f"사람 단계({','.join(left)})는 아직 — 나중에 같은 폴더에 이어서: "
                  f"`session --pi5 <PI5> --session-dir {sess} --steps {','.join(left)}`"]
    L += ["", "돌려보낼 것: 이 폴더 전체를 zip 으로(WALL_NOISE_TEST.md §7)."]
    path = os.path.join(sess, "SESSION.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n" + "\n".join(L))
    print(f"\n끝 — {path}")
    beep_fn("finish")
    return sess


# ───────────────────────── 자가 시험 (하드웨어 없이) ─────────────────────────

def synth_frames(dist_mm, sigma0, k, seconds=120, hz=5, rho=0.0, drop_zones=(), drop_p=0.05, person=None, seed=1, desk=False,
                 sway_all=0.0):
    """가짜 벽(또는 사람) 8×8 프레임을 센서 두 개분 만든다. person = (거리 mm, 흔들림 진폭 mm, Hz). desk = 아래 2줄 40 cm 책상.
    sway_all = 모든 칸이 0.4 Hz 로 같이 흔들리는 진폭 mm(장치가 움직인 녹화)."""
    rng = random.Random(seed)
    out = []
    n = int(seconds * hz)
    for sid in (0, 1):
        base = [dist_mm * (1 + 0.04 * ((r - 3.5) ** 2 + (c - 3.5) ** 2) / 24.5) for r in range(8) for c in range(8)]
        pz = set()
        if person:
            pz = {r * 8 + c for r in range(2, 6) for c in range(2, 6)}
            for i in pz:
                base[i] = person[0]
        if desk:
            for i in range(48, 64):
                base[i] = 400.0
        sig = [math.sqrt(sigma0 ** 2 + (k * b / 1000.0) ** 2) for b in base]
        ar = [0.0] * 64
        for f in range(n):
            t = f / hz
            d, st = [], []
            for i in range(64):
                ar[i] = rho * ar[i] + math.sqrt(1 - rho * rho) * rng.gauss(0, sig[i])
                v = base[i] + ar[i]
                if i in pz:
                    v += person[1] * math.sin(2 * math.pi * person[2] * t + 0.3 * sid)
                if sway_all:
                    v += sway_all * math.sin(2 * math.pi * 0.4 * t)
                if i in drop_zones and rng.random() < drop_p:
                    d.append(-1)
                    st.append(255)
                else:
                    d.append(int(round(v)))
                    st.append(5)
            out.append({"id": sid, "have": True, "ts_ms": 5000 + int(t * 1000), "frame_seq": f & 0xFFFF,
                        "w": 8, "h": 8, "flags": 1, "mask": 3, "dist_mm": d, "status": st, "_host_t": 1.7e9 + t})
    return out


def synth_run(root, label, dist_cm, frames, posture="away"):
    rd = os.path.join(root, label)
    os.makedirs(rd, exist_ok=True)
    with open(os.path.join(rd, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"label": label, "distance_cm": dist_cm, "synthetic": True}, f)
    with open(os.path.join(rd, "frames.jsonl"), "w", encoding="utf-8") as f:
        for s in frames:
            f.write(frame_line(s, s["_host_t"]) + "\n")
    with open(os.path.join(rd, "state.jsonl"), "w", encoding="utf-8") as f:
        for t in range(60):
            f.write(json.dumps({"host_t": 1.7e9 + t, "state": {"valid": True, "posture": posture, "instant": {"posture": posture},
                                                              "probs": {posture: 0.9}, "model": {"file": "synthetic"}}}) + "\n")
    return rd


class FakeBoard:
    """가짜 중계기(/raw) + 자세 서비스(/api/state). latency = 응답 지연 s, stale = 첫 프레임만 되풀이(ESP32 멈춤)."""

    def __init__(self, latency=0.0, stale=False, seconds=200):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        fr = synth_frames(650, 8.0, 15.0, seconds=seconds)
        by = {0: [f for f in fr if f["id"] == 0], 1: [f for f in fr if f["id"] == 1]}
        t0 = time.time()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if latency:
                    time.sleep(latency)
                k = 0 if stale else min(int((time.time() - t0) * 5), len(by[0]) - 1)
                if self.path.startswith("/raw"):
                    body = {"dev": "/dev/ttyUSB0", "sensors": [{kk: vv for kk, vv in by[s][k].items() if not kk.startswith("_")} for s in (0, 1)]}
                elif self.path.startswith("/api/state"):
                    body = {"valid": True, "posture": "away", "instant": {"posture": "away"}, "probs": {"away": 0.8},
                            "online_sensors": 2, "model": {"file": "models/fake.bin"}}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()


def selftest() -> int:
    root = tempfile.mkdtemp(prefix="tofnoise_")
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    try:
        s0, k = 8.0, 15.0
        exp60 = math.sqrt(s0 ** 2 + (k * 0.60) ** 2)
        print(f"[1] 흰 잡음 벽 60 cm (σ 기대 {exp60:.1f} mm, 칸 3개 5 % 빠짐)")
        rd = synth_run(root, "wall60", 60, synth_frames(600, s0, k, drop_zones=(0, 9, 63)))
        s = analyze(rd, quiet=True)["sensors"]["0"]
        check(abs(s["std_mm"] / exp60 - 1) < 0.12, f"σ {s['std_mm']} ≈ {exp60:.1f}")
        check(abs(s["diff_std_mm"] / exp60 - 1) < 0.12, f"프레임차 σ {s['diff_std_mm']} ≈ σ")
        check(0.7 < s["slow_ratio"] < 1.4, f"slow_ratio {s['slow_ratio']} ≈ 1")
        check(0.7 < s["nod_ratio"] < 1.4, f"nod_ratio {s['nod_ratio']} ≈ 1")
        check(set(s["flicker_zones"]) == {0, 9, 63}, f"깜빡임 칸 {s['flicker_zones']}")
        check(abs(s["center4x4_mm"] - 600) < 15, f"가운데 {s['center4x4_mm']} mm")
        check(s["filters"]["mean3"]["std_mm"] < 0.7 * s["filters"]["raw"]["std_mm"], "mean3 가 σ 를 줄인다")
        check(s["lost_pct"] == 0 and not any("드리프트" in v for v in verdicts(s)), "놓침 0 %, 드리프트 오경보 없음")
        check(os.path.isfile(os.path.join(rd, "summary.md")), "summary.md 생성")

        print("[2] 느린(AR(1) ρ=0.8) 잡음 벽")
        rd2 = synth_run(root, "wall60_ar", 60, synth_frames(600, s0, k, rho=0.8, seed=2))
        s = analyze(rd2, quiet=True)["sensors"]["0"]
        check(s["slow_ratio"] > 1.5 and s["lag1_r"] > 0.6, f"slow_ratio {s['slow_ratio']}, lag1 {s['lag1_r']} 가 느린 성분을 잡는다")

        print("[3] 45/50/60 cm 맞춤 + 움직인 녹화는 뺀다")
        rds = [synth_run(root, f"wall{d}", d, synth_frames(d * 10, s0, k, seed=d)) for d in (45, 50)] + [rd]
        moved = synth_run(root, "wall60_moved", 60, synth_frames(600, s0, k, sway_all=300.0, seed=9))
        r = fit(rds + [moved], out=os.path.join(root, "fit"), quiet=True)
        a = r["sensors"]["all"]
        check(abs(a["sigma0_mm"] - s0) < 2.5, f"σ0 {a['sigma0_mm']} ≈ {s0}")
        check(abs(a["k_mm_per_m"] / k - 1) < 0.25, f"k {a['k_mm_per_m']} ≈ {k}")
        check(len(r["skipped"]) == 2, f"σ 큰 녹화 뺌: {r['skipped']}")

        print("[4] 가만히 앉기(60 cm, 0.3 Hz 20 mm 흔들림, 아래 2줄 책상) vs 벽, 빈 자리 기준")
        rs = synth_run(root, "still", None, synth_frames(1800, s0, k, person=(600, 20.0, 0.3), seed=7, desk=True), posture="drowsy")
        re_ = synth_run(root, "empty", None, synth_frames(1800, s0, k, seed=6, desk=True))
        c = compare(rd, rs, re_, out=os.path.join(root, "cmp"), quiet=True)
        e = c["sensors"]["0"]
        check(e["person_zones"] == 16 and e["nod_excess"] >= 2, f"사람 칸 {e['person_zones']}(책상 16칸 뺌), 초과 {e.get('nod_excess')}배")
        check("사람 몸" in e.get("verdict", ""), "판정 = 사람 흔들림")
        check(c["posture_still"]["drowsy_frac"] == 1.0, "자세 출력 집계")
        print("[5] 흔들림 없는 사람 → 센서 잡음 판정")
        rs2 = synth_run(root, "still_calm", None, synth_frames(1800, s0, k, person=(600, 0.0, 0.3), seed=8))
        e = compare(rd, rs2, out=os.path.join(root, "cmp2"), quiet=True)["sensors"]["0"]
        check("센서 잡음이 주원인" in e.get("verdict", ""), f"판정 = 센서 잡음 (초과 {e.get('nod_excess')}배)")

        print("[6] 가짜 중계기로 aim·record")
        fb = FakeBoard()
        try:
            rc = aim(fb.url, 1.0, 65)
            rdr = record(fb.url, fb.url, 6.0, "fake", os.path.join(root, "rec"), 65, quiet=True)
            with open(os.path.join(rdr, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            rep = analyze(rdr, quiet=True)
            n0 = meta["frames"].get("s0", 0)
            check(rc == 0 and 26 <= n0 <= 33 and rep["sensors"]["0"]["lost_pct"] <= 4, f"aim rc {rc}, s0 {n0} 프레임(≈30), 놓침 {rep['sensors']['0']['lost_pct']} %")
            check(meta["state_lines"] >= 4 and rep["posture"]["model"] == "models/fake.bin", f"자세 {meta['state_lines']}줄 기록")
        finally:
            fb.close()

        print("[7] 느린 터널(요청마다 0.25 s) — 폴러 3개면 덜 놓친다")
        fb = FakeBoard(latency=0.25)
        try:
            r1 = analyze(record(fb.url, "", 8.0, "slow1", os.path.join(root, "rec"), pollers=1, quiet=True), quiet=True)
            r3 = analyze(record(fb.url, "", 8.0, "slow3", os.path.join(root, "rec"), pollers=3, quiet=True), quiet=True)
            l1, l3 = r1["sensors"]["0"]["lost_pct"], r3["sensors"]["0"]["lost_pct"]
            check(l3 <= 5 and l3 < l1, f"놓침 폴러 1개 {l1} % → 3개 {l3} %")
        finally:
            fb.close()

        print("[8] 멈춘 ESP32(같은 프레임만) — aim·record 가 알아챈다")
        fb = FakeBoard(stale=True)
        try:
            rc = aim(fb.url, 1.0, 65)
            try:
                record(fb.url, "", 5.0, "stale", os.path.join(root, "rec"), quiet=True)
                raised = False
            except RecordError:
                raised = True
            check(rc == 1 and raised, f"aim rc {rc}, record 멈춤 오류 {raised}")
        finally:
            fb.close()

        print("[9] 안내 진행: 기본은 벽만(r·s), 나중에 같은 폴더에 사람 단계 이어서(q)")
        fb = FakeBoard()
        dur = {p["label"]: 5 for p in PLAN}
        a1 = iter(["형광등", "흰 벽", "10°", "55",   # 메모 4개
                   "",                                  # wall45 시작
                   "r", "",                             # wall50 격자 다시 → 시작
                   "s"])                                # wall60 건너뜀
        try:
            sd = session(fb.url, fb.url, os.path.join(root, "sess"), ask=lambda q: next(a1), beep_fn=no_beep,
                         delay=0, durations=dur, aim_seconds=1.0)
            with open(os.path.join(sd, "session.json"), encoding="utf-8") as f:
                lg = json.load(f)
            with open(os.path.join(sd, "SESSION.md"), encoding="utf-8") as f:
                md1 = f.read()
            check(sorted(lg["runs"]) == ["wall45", "wall50"] and os.path.isfile(os.path.join(sd, "fit.md")),
                  f"벽만: 녹화된 단계 {sorted(lg['runs'])}, fit.md")
            check("--steps empty,still,work" in md1 and not os.path.exists(os.path.join(sd, "compare_still.md")),
                  "SESSION.md 가 사람 단계 이어 하기 명령을 알려 준다")
            a2 = iter(["", "", "", "q"])                # wall60, empty, still 시작 → work 전에 끝냄(메모는 저장된 것 사용)
            session(fb.url, fb.url, steps=["wall60"] + PERSON_STEPS, session_dir=sd, ask=lambda q: next(a2),
                    beep_fn=no_beep, delay=0, durations=dur, aim_seconds=1.0)
        finally:
            fb.close()
        with open(os.path.join(sd, "session.json"), encoding="utf-8") as f:
            lg = json.load(f)
        check(sorted(lg["runs"]) == ["empty", "still", "wall45", "wall50", "wall60"], f"이어서: 녹화된 단계 {sorted(lg['runs'])}")
        check(all(os.path.isfile(os.path.join(sd, x)) for x in ("SESSION.md", "fit.md", "compare_still.md")),
              "SESSION.md·fit.md·compare_still.md 생성")
        check(lg["notes"].get("chin") == "55", "메모 저장·이어서 재사용")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("selftest " + ("통과" if not fails else f"실패 {len(fails)}개"))
    return 0 if not fails else 1


# ───────────────────────── CLI ─────────────────────────

def expand(paths):
    out = []
    for p in paths:
        g = sorted(glob.glob(p))
        out += [x for x in (g or [p]) if os.path.isfile(os.path.join(x, "frames.jsonl"))]
    return out


def expand_one(p, what):
    xs = expand([p])
    if len(xs) != 1:
        sys.exit(f"{what}: '{p}' 에 맞는 녹화 폴더가 {len(xs)}개 — 폴더 이름을 끝까지 쓴다" + (f" ({', '.join(xs)})" if xs else ""))
    return xs[0]


def add_conn(p):
    p.add_argument("--pi5", help="Pi 5 주소. 주면 PC → Pi 5 → Pi 4 ssh 터널을 스스로 연다(없으면 127.0.0.1:8772/8771)")
    p.add_argument("--pi4", default=PI4_ADDR, help="Pi 4 eth0 주소(Pi 5 에서 본 주소)")
    p.add_argument("--raw", default="http://127.0.0.1:8772", help="중계기 주소(--pi5 없이 직접 연 터널)")
    p.add_argument("--api", default="http://127.0.0.1:8771", help="자세 서비스 주소")
    p.add_argument("--no-api", action="store_true", help="자세 서비스는 안 읽고 거리만 녹화")


def connect(a):
    """(raw_url, api_url, tunnel 또는 None)."""
    if a.pi5:
        t = Tunnel(a.pi5, a.pi4)
        raw, api = t.open()
        print(f"터널 열림: {a.pi5} → {a.pi4}  (/raw {raw}, /api {api})")
        return raw, ("" if a.no_api else api), t
    return a.raw, ("" if a.no_api else a.api), None


def main() -> int:
    for stream in (sys.stdout, sys.stderr, sys.stdin):    # sys.exit 메시지(stderr)·입력도 UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("session", help="안내 진행(사람이 실행): 벽 45/50/60 cm 각 5분(--with-person 이면 빈 자리·가만히·평소 작업까지), 소리·Enter")
    add_conn(p)
    p.add_argument("--out", default="noise_runs")
    p.add_argument("--session-dir", help="끊긴 세션 폴더에 이어서 녹화")
    p.add_argument("--steps", help="이 단계만, 쉼표로(예: wall60 / empty,still,work). 기본은 벽 3개. 단계: " + ",".join(x["label"] for x in PLAN))
    p.add_argument("--with-person", action="store_true", help="벽 3개 뒤에 빈 자리·가만히·평소 작업까지")
    p.add_argument("--delay", type=float, default=5.0, help="Enter 뒤 카운트다운 s")
    p.add_argument("--pollers", type=int, default=3, help="/raw 폴러 수(터널이 느려 놓침이 클 때 늘린다)")
    p.add_argument("--no-beep", action="store_true")
    p = sub.add_parser("aim", help="조준·연결 확인: 몇 초 중앙값 격자와 경고")
    add_conn(p)
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--distance-cm", type=float)
    p.add_argument("--person", action="store_true", help="사람·책상을 보는 자리(벽 폭 경고 안 함)")
    p = sub.add_parser("record", help="한 번 녹화 → 분석 (Claude 셸에서 돌리면 timeout 을 녹화 길이 + 120 s 이상으로)")
    add_conn(p)
    p.add_argument("--seconds", type=float, default=300.0)
    p.add_argument("--label", required=True, help="예: wall45, wall50, wall60, empty, still, work, dark60")
    p.add_argument("--kind", choices=("wall", "empty", "person"), default=None, help="기본: wall* 이면 wall, empty 면 empty, 나머지 person")
    p.add_argument("--distance-cm", type=float, help="렌즈 앞면 ~ 벽 (줄자). 사람 녹화는 비운다")
    p.add_argument("--light", default="", help="조명 조건(사람에게 물어 채운다)")
    p.add_argument("--note", default="", help="벽 재질·지그 기울기·기기→턱 거리 등(사람에게 물어 채운다)")
    p.add_argument("--out", default="noise_runs")
    p.add_argument("--delay", type=float, default=5.0, help="시작 전 카운트다운 s")
    p.add_argument("--interval", type=float, default=0.08, help="/raw 폴링 간격 s (센서 5 Hz 보다 촘촘히)")
    p.add_argument("--pollers", type=int, default=3, help="/raw 폴러 수(터널이 느릴 때 놓침을 줄인다)")
    p.add_argument("--no-beep", action="store_true")
    p = sub.add_parser("analyze", help="녹화 폴더 다시 분석(끊긴 녹화 복구 포함)")
    p.add_argument("runs", nargs="+")
    p = sub.add_parser("fit", help="여러 거리 벽 녹화로 σ(d) 맞춤 → 시뮬 L8Params")
    p.add_argument("runs", nargs="+")
    p.add_argument("--reflectance", type=float, default=SIM_WALL_REFL, help="벽 반사율 가정(시뮬 벽 0.70)")
    p.add_argument("--out", help="결과 파일 이름(확장자 없이)")
    p = sub.add_parser("compare", help="벽 녹화 vs 사람 녹화(가만히·작업), 빈 자리 녹화로 책상 칸 제외")
    p.add_argument("--wall", required=True)
    p.add_argument("--still", required=True, help="still 또는 work 녹화 폴더")
    p.add_argument("--empty", help="같은 자리 빈 자리 녹화 폴더(있으면 책상·의자 칸을 뺀다)")
    sub.add_parser("selftest", help="하드웨어 없이 가짜 데이터·가짜 중계기로 전체 확인")
    a = ap.parse_args()
    if a.cmd in ("session", "aim", "record"):
        raw, api, tun = connect(a)
        try:
            if a.cmd == "aim":
                return aim(raw, a.seconds, a.distance_cm, "person" if a.person else "wall")
            if a.cmd == "record":
                kind = a.kind or ("wall" if a.label.startswith("wall") else "empty" if a.label.startswith("empty") else "person")
                try:
                    rd = record(raw, api, a.seconds, a.label, a.out, a.distance_cm, a.light, a.note, a.interval,
                                a.pollers, a.delay, no_beep if a.no_beep else beep, kind)
                except RecordError as e:
                    print(f"녹화 못 함: {e}")
                    return 1
                rep = analyze(rd)
                probs = run_problems(rep, kind)
                if probs:
                    print("다시 녹화할 만한 문제: " + " / ".join(probs))
                    return 3
                return 0
            steps = [x.strip() for x in a.steps.split(",")] if a.steps else (WALL_STEPS + PERSON_STEPS if a.with_person else WALL_STEPS)
            bad = [x for x in steps if x not in WALL_STEPS + PERSON_STEPS]
            if bad:
                sys.exit(f"모르는 단계 {bad} — 단계: {','.join(WALL_STEPS + PERSON_STEPS)}")
            session(raw, api, a.out, a.session_dir, steps, beep_fn=no_beep if a.no_beep else beep, delay=a.delay,
                    pollers=a.pollers, tunnel=tun)
            return 0
        finally:
            if tun:
                tun.close()
    if a.cmd == "analyze":
        runs = expand(a.runs)
        if not runs:
            sys.exit("녹화 폴더(frames.jsonl 있는 곳)를 못 찾았다")
        for rd in runs:
            analyze(rd)
        return 0
    if a.cmd == "fit":
        runs = expand(a.runs)
        if not runs:
            sys.exit("녹화 폴더(frames.jsonl 있는 곳)를 못 찾았다")
        fit(runs, a.out, a.reflectance)
        return 0
    if a.cmd == "compare":
        compare(expand_one(a.wall, "--wall"), expand_one(a.still, "--still"), expand_one(a.empty, "--empty") if a.empty else None)
        return 0
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
