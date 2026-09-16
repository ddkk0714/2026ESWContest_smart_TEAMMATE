"""PC 단독 리허설 — 브로커·센서 없이 전체 소프트웨어 경로를 돌려 FSM 전이를 요약한다.

    python -m pip install amqtt          # 로컬 MQTT 브로커(개발용, tools/requirements.txt 의 선택 항목)
    python tools/rehearsal_local.py [초, 기본 400]

amqtt 브로커(127.0.0.1:18832) + tools/mqtt_scenario_sim.py --scenario short + `python -m deskmate_hub run --config fsm.demo.yaml`
을 한 번에 띄우고, 끝나면 hub/logs/rehearsal/state-*.jsonl 에서 상태 전이만 뽑아 출력한다.
Node-RED 를 같은 브로커(포트 18832)에 붙이면 대시보드도 함께 확인할 수 있다. 실기 검증을 대신하지 않는다.
"""
import asyncio, glob, json, os, subprocess, sys, threading, time
from amqtt.broker import Broker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18832
LOGDIR = os.path.join(ROOT, "hub", "logs", "rehearsal")
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 400

cfg = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{PORT}"}},
       "sys_interval": 0, "auth": {"allow-anonymous": True}, "topic-check": {"enabled": False}}
stop = threading.Event()

async def run_broker():
    b = Broker(cfg); await b.start()
    while not stop.is_set():
        await asyncio.sleep(0.2)
    await b.shutdown()

os.makedirs(LOGDIR, exist_ok=True)
threading.Thread(target=lambda: asyncio.run(run_broker()), daemon=True).start()
time.sleep(1.5)

env = dict(os.environ, PYTHONIOENCODING="utf-8")
hub = subprocess.Popen([sys.executable, "-m", "deskmate_hub", "run", "--broker", "127.0.0.1", "--port", str(PORT),
                        "--config", os.path.join(ROOT, "hub", "deskmate_hub", "config", "fsm.demo.yaml"),
                        "--log-dir", LOGDIR], cwd=os.path.join(ROOT, "hub"), env=env,
                       stdout=open(os.path.join(LOGDIR, "hub-console.log"), "a", encoding="utf-8"), stderr=subprocess.STDOUT)
time.sleep(2)
sim = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "mqtt_scenario_sim.py"), "--broker", "127.0.0.1",
                        "--port", str(PORT), "--scenario", "short"], env=env,
                       stdout=subprocess.DEVNULL, stderr=open(os.path.join(LOGDIR, "sim-console.log"), "a", encoding="utf-8"))
t0 = time.time()
while time.time() - t0 < DURATION and sim.poll() is None:
    time.sleep(1)
sim.terminate(); time.sleep(1)
hub.terminate(); time.sleep(1)
stop.set()

states = sorted(glob.glob(os.path.join(LOGDIR, "state-*.jsonl")))[-1]
prev = None
print("== transitions ==")
for line in open(states, encoding="utf-8"):
    d = json.loads(line)["data"]
    if d["fsm_state"] != prev:
        print(f"seq={json.loads(line)['seq']:>3} {d['fsm_state']:<16} ctx={d['context']:<5} fat={d['c_fatigue']:.2f} foc={d['c_focus']:.2f} gate={d['gate']} cause={d['cause']}")
        prev = d["fsm_state"]
print("last sensor_summary:", json.dumps(json.loads(line)["data"]["sensor_summary"], ensure_ascii=False)[:200])
