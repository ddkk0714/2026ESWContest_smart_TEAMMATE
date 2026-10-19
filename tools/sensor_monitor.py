#!/usr/bin/env python3
"""ESP32 센서 노드 USB(UART0) JSON 라인을 읽어 브라우저 한 페이지에 raw 값과 판정 결과를 같이 보여준다.

    python tools/sensor_monitor.py --port COM8
    → http://127.0.0.1:8777

왼쪽 raw: 환경(CO₂·온도·습도·조도) · mmWave(거리·움직임·호흡·심박) · ToF 두 센서의 원본 8×8 히트맵.
오른쪽 결과: 자세·확신도·재실·졸음(ToF) · present·motion_state·drowsy_state(mmWave) · 60 s 추이 · 줄 종류별 수신률 · 보드 로그.

ToF 원본 8×8 은 운영 펌웨어가 USB 로 안 내므로 시작할 때 'z' 를 보내 `{"t":"tof_grid"}` 줄을 켠다(펌웨어 0x0123+, 토글).
끝낼 때 다시 'z' 로 끈다. pyserial 만 있으면 된다(브라우저 쪽은 외부 라이브러리 없음).
포트를 열 때 DTR/RTS 를 내려 둔 채 열어 보드를 리셋하지 않는다(리셋하면 SCD41·C1001 이 다시 안 올라온다).
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque
from urllib.parse import parse_qs, urlparse

HISTORY_SEC = 60
RATE_WINDOW_SEC = 10.0
LOG_LINES = 40
TREND_FIELDS = {
    "mmwave": ("distance_cm", "motion_level", "resp_bpm", "heart_bpm"),
    "tof_feature": ("posture_confidence", "motion_score", "head_delta_mm", "coverage_ratio", "centroid_depth_mm"),
    "env": ("co2_ppm", "temp_c", "humidity_pct", "lux"),
}


def parse_line(line: str) -> tuple[str, Any] | None:
    """한 줄 → ('json', dict) | ('log', str) | None(빈 줄). JSON 센서 줄은 반드시 `{"t":` 로 시작한다."""
    line = line.strip()
    if not line:
        return None
    if line.startswith('{"t":'):
        try:
            obj = json.loads(line)
        except ValueError:
            return ("log", line)
        if isinstance(obj, dict) and isinstance(obj.get("t"), str):
            return ("json", obj)
        return ("log", line)
    return ("log", line)


class MonitorState:
    """스레드 안전한 최신값·추이·수신률·로그 보관. 시리얼 스레드가 feed(), HTTP 가 snapshot()."""

    def __init__(self, now: float | None = None) -> None:
        self._lock = threading.Lock()
        self.latest: dict[str, dict[str, Any]] = {}
        self.received_at: dict[str, float] = {}
        self.history: dict[str, Deque[tuple[float, dict[str, Any]]]] = collections.defaultdict(collections.deque)
        self.arrivals: dict[str, Deque[float]] = collections.defaultdict(collections.deque)
        self.counts: collections.Counter[str] = collections.Counter()
        self.logs: Deque[tuple[float, str]] = collections.deque(maxlen=LOG_LINES)
        self.port_ok = False
        self.port_error = ""
        self.started = now if now is not None else time.time()

    def feed(self, line: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        parsed = parse_line(line)
        if parsed is None:
            return
        kind, value = parsed
        with self._lock:
            if kind == "log":
                self.logs.append((now, value))
                return
            t = value["t"]
            self.latest[t] = value
            self.received_at[t] = now
            self.counts[t] += 1
            arr = self.arrivals[t]
            arr.append(now)
            while arr and now - arr[0] > RATE_WINDOW_SEC:
                arr.popleft()
            if t in TREND_FIELDS:
                hist = self.history[t]
                hist.append((now, {k: value.get(k) for k in TREND_FIELDS[t]}))
                while hist and now - hist[0][0] > HISTORY_SEC:
                    hist.popleft()

    def rate(self, t: str, now: float) -> float:
        """최근 10 s 창 안 도착 간격의 평균 주기(Hz). 끊긴 지 오래됐는지는 age_s 가 따로 말한다."""
        arr = self.arrivals.get(t)
        if not arr or len(arr) < 2:
            return 0.0
        span = arr[-1] - arr[0]
        return (len(arr) - 1) / span if span > 0 else 0.0

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = now if now is not None else time.time()
        with self._lock:
            return {
                "now": now,
                "uptime_s": now - self.started,
                "port_ok": self.port_ok,
                "port_error": self.port_error,
                "latest": dict(self.latest),
                "age_s": {t: now - at for t, at in self.received_at.items()},
                "rate_hz": {t: self.rate(t, now) for t in self.arrivals},
                "counts": dict(self.counts),
                "history": {t: [[round(ts - now, 2), v] for ts, v in hist] for t, hist in self.history.items()},
                "logs": [[round(ts - now, 1), s] for ts, s in self.logs],
            }


class SerialReader(threading.Thread):
    def __init__(self, state: MonitorState, port: str, baud: int, grid: bool, record: Path | None) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.port = port
        self.baud = baud
        self.grid = grid
        self.record = record
        self._ser = None
        self._stop = threading.Event()

    def send(self, text: str) -> bool:
        ser = self._ser
        if ser is None:
            return False
        try:
            ser.write(text.encode("ascii"))
            return True
        except Exception:  # noqa: BLE001 - 포트가 빠진 경우
            return False

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        import serial  # pyserial

        rec = self.record.open("a", encoding="utf-8") if self.record else None
        while not self._stop.is_set():
            ser = serial.Serial()
            ser.port = self.port
            ser.baudrate = self.baud
            ser.timeout = 0.2
            ser.dtr = False  # 열 때 보드를 리셋하지 않는다
            ser.rts = False
            try:
                ser.open()
            except Exception as exc:  # noqa: BLE001
                self.state.port_ok = False
                self.state.port_error = str(exc)
                time.sleep(1.0)
                continue
            self._ser = ser
            self.state.port_ok = True
            self.state.port_error = ""
            if self.grid:
                time.sleep(0.3)
                self.send("z")
            buf = b""
            try:
                while not self._stop.is_set():
                    chunk = ser.read(4096)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("utf-8", "replace")
                        self.state.feed(line)
                        if rec is not None and line.startswith('{"t":'):
                            rec.write(line.rstrip("\r") + "\n")
            except Exception as exc:  # noqa: BLE001 - 뽑힘
                self.state.port_ok = False
                self.state.port_error = str(exc)
            finally:
                if self.grid and not self._stop.is_set():
                    pass  # 끊긴 포트엔 못 보낸다. 다시 열리면 'z' 를 다시 보낸다(펌웨어 리셋이면 기본 꺼짐이라 켜지고, 아니면 꺼진다)
                try:
                    ser.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ser = None
            time.sleep(0.5)
        if rec is not None:
            rec.close()


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>DESKMATE sensor monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f6f7f9;--fg:#1b1f24;--muted:#6b7280;--card:#fff;--line:#e5e7eb;--ok:#15803d;--bad:#b91c1c;--warn:#b45309;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e5e7eb;--muted:#9ca3af;--card:#171a21;--line:#2a2f3a;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--accent:#60a5fa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,Roboto,"Malgun Gothic",sans-serif}
header{display:flex;gap:16px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:1;flex-wrap:wrap}
header h1{font-size:16px;margin:0}
.pill{padding:2px 8px;border-radius:999px;border:1px solid var(--line);font-size:12px;color:var(--muted)}
.pill.ok{color:var(--ok);border-color:var(--ok)}.pill.bad{color:var(--bad);border-color:var(--bad)}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px 16px;max-width:1500px;margin:0 auto}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
section h2{font-size:13px;margin:0 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;display:flex;justify-content:space-between}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px 12px}
.kv div{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line);padding:2px 0}
.kv b{font-variant-numeric:tabular-nums;font-weight:600}.null{color:var(--muted)}
.big{font-size:26px;font-weight:700;margin:2px 0}.sub{color:var(--muted);font-size:12px}
.grids{display:flex;gap:16px;flex-wrap:wrap}.grid{text-align:center}.grid canvas{image-rendering:pixelated;border:1px solid var(--line);border-radius:6px}
.legend{height:10px;width:200px;margin:4px auto;background:linear-gradient(90deg,#fde725,#5ec962,#21918c,#3b528b,#440154);border-radius:3px}
.result{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.result .card{border:1px solid var(--line);border-radius:8px;padding:8px 10px}
button{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--accent)}
canvas.trend{width:100%;height:70px;display:block}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:2px 6px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
pre{margin:0;font-size:11px;line-height:1.35;max-height:220px;overflow:auto;white-space:pre-wrap;word-break:break-all;color:var(--muted)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
</style></head><body>
<header><h1>DESKMATE 센서 모니터</h1><span id="port" class="pill">포트</span><span id="up" class="pill">0 s</span>
<span class="pill">명령: <button data-c="z">z ToF 격자 토글</button> <button data-c="b">b 기준선</button> <button data-c="g">g 배경</button> <button data-c="r">r 추정기 초기화</button> <button data-c="i">i 환경 I2C 스캔</button></span></header>
<main>
<section><h2>raw · ToF 원본 8×8 (mm) <span id="gridinfo"></span></h2>
 <div class="grids">
  <div class="grid"><div>센서 A (0x54)</div><canvas id="gA" width="8" height="8" style="width:224px;height:224px"></canvas><div id="gAs" class="sub"></div></div>
  <div class="grid"><div>센서 B (0x52)</div><canvas id="gB" width="8" height="8" style="width:224px;height:224px"></canvas><div id="gBs" class="sub"></div></div>
  <div class="grid"><div class="sub">색: 가까움 → 멂</div><div class="legend"></div><div class="sub" id="range">200 … 2000 mm</div><div class="sub">회색 = 타깃 없음 / 상태 ≠ 5·9</div><div class="sub">장면 방향(FLIP 적용 뒤), UART 0x05 와 같은 값</div></div>
 </div>
</section>
<section><h2>결과 · 판정</h2><div class="result" id="results"></div></section>
<section><h2>raw · mmWave C1001 <span id="r_mmwave" class="sub"></span></h2><div class="kv" id="kv_mmwave"></div></section>
<section><h2>raw · 환경 <span id="r_env" class="sub"></span></h2><div class="kv" id="kv_env"></div></section>
<section><h2>raw · ToF 특징 (규칙 추정기 입력) <span id="r_tof" class="sub"></span></h2><div class="kv" id="kv_tof"></div></section>
<section><h2>60 s 추이</h2><div id="trends"></div></section>
<section><h2>수신 통계</h2><table id="rates"><tr><th>줄</th><th>주기(Hz)</th><th>마지막</th><th>누계</th></tr></table></section>
<section><h2>보드 로그 (JSON 아닌 줄)</h2><pre id="logs"></pre></section>
</main>
<script>
const $=id=>document.getElementById(id);
const fmt=(v,d=1)=>v==null?'<span class="null">null</span>':(typeof v==='number'?(Number.isInteger(v)?v:v.toFixed(d)):String(v));
function kv(el,obj,keys){el.innerHTML=keys.map(k=>`<div><span>${k}</span><b>${fmt(obj?obj[k]:null)}</b></div>`).join('')}
const MM=['present','motion_state','motion_level','distance_cm','resp_bpm','resp_valid','heart_bpm','heart_valid','valid'];
const EV=['co2_ppm','temp_c','humidity_pct','lux','co2_valid','temp_valid','humidity_valid','lux_valid'];
const TF=['valid_zones','grid_width','grid_height','coverage_ratio','centroid_depth_mm','head_row_index','head_delta_mm','nod_rate_hz','motion_score','baseline_ready','nods_in_window','sensors_ok','tx_grid','tx_feature'];
const RANGE=[200,2000];
function color(d,st){if(d==null||d<0||!(st===5||st===9))return [128,128,128];let x=(d-RANGE[0])/(RANGE[1]-RANGE[0]);x=Math.max(0,Math.min(1,x));
 const stops=[[253,231,37],[94,201,98],[33,145,140],[59,82,139],[68,1,84]];const p=x*(stops.length-1),i=Math.floor(p),f=p-i,a=stops[i],b=stops[Math.min(i+1,stops.length-1)];
 return [0,1,2].map(k=>Math.round(a[k]+(b[k]-a[k])*f))}
function drawGrid(cv,s,w,h,lab){const ctx=cv.getContext('2d');if(!s){ctx.fillStyle='#444';ctx.fillRect(0,0,w,h);lab.textContent='죽음/미수신';return}
 const img=ctx.createImageData(w,h);let n=0,sum=0,min=1e9;
 for(let z=0;z<w*h;z++){const [r,g,b]=color(s.d[z],s.st[z]);img.data[z*4]=r;img.data[z*4+1]=g;img.data[z*4+2]=b;img.data[z*4+3]=255;if(s.d[z]>=0&&(s.st[z]===5||s.st[z]===9)){n++;sum+=s.d[z];min=Math.min(min,s.d[z])}}
 ctx.putImageData(img,0,0);lab.textContent=n?`유효 ${n}/${w*h} · 평균 ${(sum/n).toFixed(0)} mm · 최소 ${min} mm`:'유효 zone 없음'}
function card(t,v,sub,cls){return `<div class="card"><div class="sub">${t}</div><div class="big ${cls||''}">${v}</div><div class="sub">${sub||''}</div></div>`}
function results(L,age){const tf=L.tof_feature,mm=L.mmwave,ev=L.env;let h='';
 if(tf){h+=card('ToF 자세',tf.posture,`확신 ${fmt(tf.posture_confidence,2)} · 기준선 ${tf.baseline_ready?'있음':'없음'}`,tf.posture==='upright'?'ok':(tf.posture==='away'||tf.posture==='unknown'?'':'warn'));
  h+=card('ToF 재실',tf.present?'있음':'없음',`커버 ${fmt(tf.coverage_ratio,2)} · 센서 ${tf.sensors_ok}`,tf.present?'ok':'');
  h+=card('ToF 졸음',tf.drowsy?'DROWSY':'아님',`끄덕임 ${tf.nods_in_window}/창 · 머리Δ ${fmt(tf.head_delta_mm,0)} mm`,tf.drowsy?'bad':'ok')}
 if(mm){h+=card('mmWave 상태',mm.drowsy_state,`present ${mm.present} · ${mm.motion_state} (${mm.motion_level})`,mm.valid?(mm.drowsy_state==='DROWSY'?'bad':'ok'):'bad');
  h+=card('mmWave 유효',mm.valid?'OK':'응답 없음',mm.valid?`거리 ${fmt(mm.distance_cm,0)} cm`:'C1001 init 실패 — 배선·5 V 확인',mm.valid?'ok':'bad')}
 if(ev){const co2=ev.co2_valid?ev.co2_ppm:null;h+=card('CO₂',fmt(co2,0),ev.co2_valid?(co2>1000?'환기 권고 (>1000)':'정상'):'SCD41 없음/초기화 실패',ev.co2_valid?(co2>1000?'warn':'ok'):'bad');
  h+=card('조도',fmt(ev.lux,0)+' lx',ev.lux_valid?'':'BH1750 없음',ev.lux_valid?'':'bad')}
 $('results').innerHTML=h||'<span class="sub">아직 수신 없음</span>'}
const trendCv={};
function trends(H){const box=$('trends');for(const t in H){const rows=H[t];if(!rows.length)continue;const keys=Object.keys(rows[0][1]);
 for(const k of keys){const id=t+'.'+k;if(!trendCv[id]){const d=document.createElement('div');d.innerHTML=`<div class="sub">${t} · ${k} <span id="tv_${id}"></span></div><canvas class="trend" id="tc_${id}"></canvas>`;box.appendChild(d);trendCv[id]=1}
  const cv=$('tc_'+id);const W=cv.clientWidth||400;if(cv.width!==W)cv.width=W;cv.height=70;const ctx=cv.getContext('2d');ctx.clearRect(0,0,W,70);
  const pts=rows.filter(r=>typeof r[1][k]==='number');if(!pts.length){$('tv_'+id).textContent='null';continue}
  let lo=Math.min(...pts.map(r=>r[1][k])),hi=Math.max(...pts.map(r=>r[1][k]));if(hi===lo){hi=lo+1}
  ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--accent');ctx.lineWidth=1.5;ctx.beginPath();
  pts.forEach((r,i)=>{const x=(r[0]+60)/60*W,y=65-(r[1][k]-lo)/(hi-lo)*60;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();
  $('tv_'+id).textContent=`${lo.toFixed(1)} … ${hi.toFixed(1)} · 지금 ${pts[pts.length-1][1][k].toFixed(1)}`}}}
function render(S){const L=S.latest,A=S.age_s;
 $('port').textContent=S.port_ok?'포트 열림':('포트 없음 '+(S.port_error||''));$('port').className='pill '+(S.port_ok?'ok':'bad');$('up').textContent=`${S.uptime_s.toFixed(0)} s`;
 const g=L.tof_grid;if(g&&A.tof_grid<3){drawGrid($('gA'),g.s[0],g.w,g.h,$('gAs'));drawGrid($('gB'),g.s[1],g.w,g.h,$('gBs'));$('gridinfo').textContent=`frame ${g.frame} · ${(S.rate_hz.tof_grid||0).toFixed(1)} Hz · flags ${g.flags}`}
 else{$('gridinfo').innerHTML='<span class="warn">tof_grid 수신 없음 — z 로 켜기</span>'}
 kv($('kv_mmwave'),L.mmwave,MM);kv($('kv_env'),L.env,EV);kv($('kv_tof'),L.tof_feature,TF);
 for(const [t,id] of [['mmwave','r_mmwave'],['env','r_env'],['tof_feature','r_tof']]){$(id).textContent=A[t]==null?'미수신':`${A[t].toFixed(1)} s 전`}
 results(L,A);trends(S.history);
 $('rates').innerHTML='<tr><th>줄</th><th>주기(Hz)</th><th>마지막</th><th>누계</th></tr>'+Object.keys(S.counts).sort().map(t=>`<tr><td>${t}</td><td>${(S.rate_hz[t]||0).toFixed(2)}</td><td class="${A[t]>15?'bad':''}">${A[t].toFixed(1)} s 전</td><td>${S.counts[t]}</td></tr>`).join('');
 $('logs').textContent=S.logs.map(l=>`${l[0]>-1?' now':l[0].toFixed(0).padStart(5)} s  ${l[1]}`).join('\n')}
const es=new EventSource('/events');es.onmessage=e=>render(JSON.parse(e.data));
document.querySelectorAll('button[data-c]').forEach(b=>b.onclick=()=>fetch('/cmd?c='+b.dataset.c,{method:'POST'}));
</script></body></html>
"""


def make_handler(state: MonitorState, reader: SerialReader, interval: float):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # 조용히
            pass

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/state":
                body = json.dumps(state.snapshot()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(b"data: " + json.dumps(state.snapshot()).encode("utf-8") + b"\n\n")
                        self.wfile.flush()
                        time.sleep(interval)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/cmd":
                c = parse_qs(url.query).get("c", [""])[0]
                ok = len(c) == 1 and c in "bgrzi" and reader.send(c)
                self.send_response(200 if ok else 400)
                self.end_headers()
            else:
                self.send_error(404)

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="ESP32 USB 포트 (예: COM8)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--http", default="127.0.0.1:8777", help="페이지 주소 (기본 127.0.0.1:8777)")
    ap.add_argument("--no-grid", action="store_true", help="시작할 때 'z' 를 보내지 않는다(ToF 원본 8×8 끔)")
    ap.add_argument("--record", type=Path, help="수신한 JSON 줄을 이 파일에 덧붙여 기록(JSONL)")
    ap.add_argument("--interval", type=float, default=0.25, help="브라우저 갱신 간격 s")
    args = ap.parse_args(argv)

    state = MonitorState()
    reader = SerialReader(state, args.port, args.baud, grid=not args.no_grid, record=args.record)
    reader.start()
    host, _, port = args.http.rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), make_handler(state, reader, args.interval))
    server.daemon_threads = True
    print(f"sensor_monitor: {args.port} @ {args.baud} → http://{host or '127.0.0.1'}:{port}  (Ctrl-C 로 종료)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if not args.no_grid:
            reader.send("z")  # 켜 둔 원본 출력을 끈다
            time.sleep(0.2)
        reader.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
