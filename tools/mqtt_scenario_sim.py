"""합성 센서 시나리오 MQTT 발행기 — ESP32·collector 없이 hub·Node-RED·Pi 5 경로를 리허설한다.

    python tools/mqtt_scenario_sim.py --broker <ip> [--scenario default] [--loop] [--node sim]

docs/mqtt-topics.md 계약 그대로 `deskmate/sensor/mmwave/<node>`(1 Hz) · `deskmate/sensor/env/<node>`(0.2 Hz) ·
`deskmate/sensor/keystroke`(1 Hz, collector 평면 payload) 를 발행한다. 실센서가 아니므로 node 기본값은 `sim` 이고
`deskmate/health/<node>` 에 online/offline 을 남긴다. 운영 경로에는 포함하지 않는다.

FSM 타이머(baseline 300 s 등)는 hub 의 실제 시계로 돌기 때문에 시나리오도 실시간이다.
짧게 돌려 보려면 hub 를 `--config hub/deskmate_hub/config/fsm.demo.yaml` 로 띄우고 `--scenario short` 를 쓴다.
시연(5분)은 `--scenario demo` — 동선과 기대 전이는 docs/demo-scenario.md, 실시간 없이 확인은 tools/demo_dryrun.py.
`--respond accept|reject` 를 주면 화면 대신 제안 카드(interaction/request)에 자동으로 답한다(무인 리허설용).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass, field



@dataclass
class Phase:
    name: str
    seconds: float
    present: bool = True
    motion_level: int = 20
    drowsy: str = "AWAKE"
    resp_valid: bool = True
    typing: bool = False
    idle_ratio: float = 1.0
    flight_cv: float = 0.0
    correction_rate: float = 0.0
    co2_ppm: int = 700
    co2_end: int | None = None    # 주면 단계 동안 co2_ppm → co2_end 로 선형 변화
    temp_c: float = 25.0
    humidity_pct: float = 45.0
    lux: int = 320
    jitter: float = 0.15          # 값 흔들림 비율
    env_jitter: float = 0.03      # 환경값 흔들림 비율(CO₂·조도)

    def co2_at(self, elapsed: float) -> float:
        if self.co2_end is None or self.seconds <= 0:
            return float(self.co2_ppm)
        r = min(1.0, max(0.0, elapsed / self.seconds))
        return self.co2_ppm + (self.co2_end - self.co2_ppm) * r


SCENARIOS = ("default", "short", "demo")


def scenario(name: str) -> list[Phase]:
    if name == "demo":
        return demo_phases()
    if name == "short":           # fsm.demo.yaml(짧은 타이머)과 함께 약 6분
        return [
            Phase("absent", 20, present=False, motion_level=0, drowsy="NOPERSON", resp_valid=False),
            Phase("seated_typing", 90, motion_level=35, typing=True, idle_ratio=0.15, flight_cv=0.30, correction_rate=0.04),
            Phase("still_drowsy", 150, motion_level=4, drowsy="DROWSY", typing=False, co2_ppm=1300),
            Phase("recover_typing", 60, motion_level=30, typing=True, idle_ratio=0.2, flight_cv=0.3, co2_ppm=900),
            Phase("absent", 60, present=False, motion_level=0, drowsy="NOPERSON", resp_valid=False),
        ]
    return [                      # default: 실제 fsm.yaml 타이머 기준 약 25분
        Phase("absent", 30, present=False, motion_level=0, drowsy="NOPERSON", resp_valid=False),
        Phase("seated_typing", 420, motion_level=35, typing=True, idle_ratio=0.15, flight_cv=0.30, correction_rate=0.04),
        Phase("tired_typing", 240, motion_level=25, typing=True, idle_ratio=0.45, flight_cv=0.65, correction_rate=0.14, co2_ppm=1200),
        Phase("still_drowsy", 480, motion_level=4, drowsy="DROWSY", typing=False, co2_ppm=1400),
        Phase("recover_typing", 180, motion_level=30, typing=True, idle_ratio=0.2, flight_cv=0.3, co2_ppm=1000),
        Phase("absent", 660, present=False, motion_level=0, drowsy="NOPERSON", resp_valid=False),
    ]


def demo_phases() -> list[Phase]:
    """5분 시연 — fsm.demo.yaml(짧은 타이머)과 함께 쓴다. 단계 이름이 곧 시연 동선이다(docs/demo-scenario.md).

    시작(착석·타이핑) → 몰입 → 환경 악화(CO₂ 상승·어두움) → 환경 개입 → 회복 → 졸음 → 자세 개입 → 이탈·리포트.
    값은 tools/demo_dryrun.py 로 전이를 보며 맞췄다. 바꾸면 dry-run 과 tools/test_demo_scenario.py 를 다시 돌린다.
    """
    calm = 0.005                  # 환경값 흔들림을 줄여 전이 시점이 시드마다 같게 한다
    office = dict(co2_ppm=650, temp_c=24.0, humidity_pct=45.0, lux=420, env_jitter=calm)
    typing = dict(motion_level=35, typing=True, idle_ratio=0.15, flight_cv=0.30, correction_rate=0.04)
    warm = dict(temp_c=28.5, humidity_pct=45.0, lux=320, env_jitter=calm)
    return [
        Phase("absent", 10, present=False, motion_level=0, drowsy="NOPERSON", resp_valid=False, **office),
        Phase("start_typing", 40, **typing, **office),
        Phase("env_worse", 30, **typing, co2_ppm=700, co2_end=950, **warm),
        # 졸음 시작: 피로 의심(노란 경고). 환경은 아직 견딜 만하다.
        Phase("drowsy", 60, motion_level=4, drowsy="DROWSY", typing=False, co2_ppm=950, co2_end=1050, **warm),
        # 졸음 + 공기 탁함: 자세 알림(자동) → 나아지지 않음 → 다른 원인(환경) 재시도 → 팬·조명 자동 제어
        Phase("drowsy_stuffy", 110, motion_level=4, drowsy="DROWSY", typing=False, co2_ppm=1350, co2_end=1450, **warm),
        # 환기·조명 후 깨어남: CO₂ 하강, 조도 상승(조명 70%) → 회복 → 몰입 복귀
        Phase("wake_fresh", 45, **typing, co2_ppm=1300, co2_end=750, temp_c=25.0, humidity_pct=45.0, lux=480,
              env_jitter=calm),
        Phase("leave", 20, present=False, motion_level=0, drowsy="NOPERSON", resp_valid=False, **office),
    ]


@dataclass
class Publisher:
    """센서 payload 를 만들어 `send(topic, payload)` 로 넘긴다. MQTT 없이 dry-run 도 같은 payload 를 쓴다."""
    send: object                  # (topic: str, payload: str) -> None
    node: str
    clock: object = time.time     # () -> float
    boot_id: str = field(default_factory=lambda: f"{random.getrandbits(32):08x}")
    seq: dict = field(default_factory=dict)
    elapsed: float = 0.0          # 현재 단계 안에서 지난 시간(CO₂ 램프용)

    def envelope(self, topic: str, data: dict) -> str:
        self.seq[topic] = self.seq.get(topic, 0) + 1
        return json.dumps({"schema_version": "1.0", "ts": round(self.clock(), 3), "node": self.node,
                           "boot_id": self.boot_id, "seq": self.seq[topic], "data": data}, ensure_ascii=False)

    def mmwave(self, p: Phase, rnd: random.Random) -> None:
        level = 0 if not p.present else max(0, min(100, int(p.motion_level * (1 + rnd.uniform(-p.jitter, p.jitter)))))
        data = {
            "present": p.present,
            "motion_state": "none" if not p.present else ("still" if level <= 10 else "active"),
            "motion_level": level,
            "distance_cm": None if not p.present else 48 + 12 * rnd.randint(0, 1),
            "resp_bpm": 12 + rnd.randint(0, 6) if p.resp_valid else None, "resp_valid": p.resp_valid and p.present,
            "heart_bpm": None, "heart_valid": False,
            "drowsy_state": p.drowsy, "valid": True,
        }
        self.send(f"deskmate/sensor/mmwave/{self.node}", self.envelope("mmwave", data))

    def env(self, p: Phase, rnd: random.Random) -> None:
        data = {"co2_ppm": int(p.co2_at(self.elapsed) * (1 + rnd.uniform(-p.env_jitter, p.env_jitter))),
                "temp_c": round(p.temp_c + rnd.uniform(-0.3, 0.3), 1),
                "humidity_pct": round(p.humidity_pct + rnd.uniform(-2, 2), 1),
                "lux": round(p.lux * (1 + rnd.uniform(-p.env_jitter, p.env_jitter))),
                "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True}
        self.send(f"deskmate/sensor/env/{self.node}", self.envelope("env", data))

    def keystroke(self, p: Phase, rnd: random.Random) -> None:
        j = lambda v, r=p.jitter: round(v * (1 + rnd.uniform(-r, r)), 3)  # noqa: E731
        if p.typing:
            flight = j(150.0)
            data = {"window_s": 60, "dwell_mean_ms": j(92.0), "dwell_std_ms": j(21.0), "flight_mean_ms": flight,
                    "flight_std_ms": round(flight * p.flight_cv, 2), "idle_ratio": min(1.0, j(p.idle_ratio)),
                    "correction_rate": round(min(1.0, j(p.correction_rate)), 4), "typing_active": True, "mouse_active": True,
                    "input_active": True, "flight_cv": j(p.flight_cv), "mouse_event_rate": j(40.0)}
        else:
            data = {"window_s": 60, "dwell_mean_ms": 0, "dwell_std_ms": 0, "flight_mean_ms": 0, "flight_std_ms": 0,
                    "idle_ratio": 1.0, "correction_rate": 0, "typing_active": False, "mouse_active": False,
                    "input_active": False, "flight_cv": 0, "mouse_event_rate": 0}
        # collector 는 평면 payload(계약 확정 09-14). 그대로 흉내 낸다.
        self.send("deskmate/sensor/keystroke",
                  json.dumps({"ts": round(self.clock(), 3), "node": "pc-collector", **data}))

    def second(self, p: Phase, tick: int, rnd: random.Random) -> None:
        """1초치 발행 — mmWave·키스트로크 1 Hz, 환경 0.2 Hz."""
        self.mmwave(p, rnd)
        self.keystroke(p, rnd)
        if tick % 5 == 0:
            self.env(p, rnd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--node", default="sim")
    ap.add_argument("--scenario", choices=SCENARIOS, default="default")
    ap.add_argument("--respond", choices=["accept", "reject"], help="제안 카드에 자동 응답(무인 리허설)")
    ap.add_argument("--respond-delay", type=float, default=4.0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskmate-sim-{int(time.time())}")
    health = f"deskmate/health/{args.node}"
    client.will_set(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)
    if args.respond:
        def on_request(_c, _u, msg):
            try:
                body = json.loads(msg.payload.decode("utf-8"))
                rid = (body.get("data") or body).get("request_id")
            except (ValueError, AttributeError):
                return
            if not rid:
                return

            def answer():
                time.sleep(args.respond_delay)
                client.publish("deskmate/feedback/user", json.dumps(
                    {"request_id": rid, "verdict": args.respond, "response_ms": int(args.respond_delay * 1000)}), qos=1)
                print(f"[sim] respond {args.respond} -> {rid}", file=sys.stderr, flush=True)
            threading.Thread(target=answer, daemon=True).start()
        client.on_connect = lambda c, *_: c.subscribe("deskmate/interaction/request", qos=1)
        client.message_callback_add("deskmate/interaction/request", on_request)
    client.connect(args.broker, args.port, keepalive=30)
    client.loop_start()
    client.publish(health, json.dumps({"ts": time.time(), "node": args.node, "status": "online", "reconnects": 0}), qos=1, retain=True)
    pub = Publisher(lambda topic, payload: client.publish(topic, payload, qos=0), args.node)
    rnd = random.Random(args.seed)
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))

    try:
        while not stop["flag"]:
            for phase in scenario(args.scenario):
                print(f"[sim] phase {phase.name} for {phase.seconds:.0f}s", file=sys.stderr, flush=True)
                t0 = time.time()
                tick = 0
                while time.time() - t0 < phase.seconds and not stop["flag"]:
                    pub.elapsed = time.time() - t0
                    pub.second(phase, tick, rnd)
                    tick += 1
                    time.sleep(max(0.0, t0 + tick - time.time()))
                if stop["flag"]:
                    break
            if not args.loop:
                break
    finally:
        client.publish(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)
        time.sleep(0.3)
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
