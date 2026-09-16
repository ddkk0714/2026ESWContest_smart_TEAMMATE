"""합성 센서 시나리오 MQTT 발행기 — ESP32·collector 없이 hub·Node-RED·Pi 5 경로를 리허설한다.

    python tools/mqtt_scenario_sim.py --broker <ip> [--scenario default] [--loop] [--node sim]

docs/mqtt-topics.md 계약 그대로 `deskmate/sensor/mmwave/<node>`(1 Hz) · `deskmate/sensor/env/<node>`(0.2 Hz) ·
`deskmate/sensor/keystroke`(1 Hz, collector 평면 payload) 를 발행한다. 실센서가 아니므로 node 기본값은 `sim` 이고
`deskmate/health/<node>` 에 online/offline 을 남긴다. 운영 경로에는 포함하지 않는다.

FSM 타이머(baseline 300 s 등)는 hub 의 실제 시계로 돌기 때문에 시나리오도 실시간이다.
짧게 돌려 보려면 hub 를 `--config hub/deskmate_hub/config/fsm.demo.yaml` 로 띄우고 `--scenario short` 를 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt


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
    jitter: float = 0.15          # 값 흔들림 비율


def scenario(name: str) -> list[Phase]:
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


@dataclass
class Publisher:
    client: mqtt.Client
    node: str
    boot_id: str = field(default_factory=lambda: f"{random.getrandbits(32):08x}")
    seq: dict = field(default_factory=dict)

    def envelope(self, topic: str, data: dict) -> str:
        self.seq[topic] = self.seq.get(topic, 0) + 1
        return json.dumps({"schema_version": "1.0", "ts": round(time.time(), 3), "node": self.node,
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
        self.client.publish(f"deskmate/sensor/mmwave/{self.node}", self.envelope("mmwave", data), qos=0)

    def env(self, p: Phase, rnd: random.Random) -> None:
        data = {"co2_ppm": int(p.co2_ppm * (1 + rnd.uniform(-0.03, 0.03))), "temp_c": round(25 + rnd.uniform(-0.5, 0.5), 1),
                "humidity_pct": round(45 + rnd.uniform(-2, 2), 1), "lux": round(320 + rnd.uniform(-20, 20)),
                "co2_valid": True, "temp_valid": True, "humidity_valid": True, "lux_valid": True}
        self.client.publish(f"deskmate/sensor/env/{self.node}", self.envelope("env", data), qos=0)

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
        self.client.publish("deskmate/sensor/keystroke",
                            json.dumps({"ts": round(time.time(), 3), "node": "pc-collector", **data}), qos=0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--node", default="sim")
    ap.add_argument("--scenario", choices=["default", "short"], default="default")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskmate-sim-{int(time.time())}")
    health = f"deskmate/health/{args.node}"
    client.will_set(health, json.dumps({"node": args.node, "status": "offline"}), qos=1, retain=True)
    client.connect(args.broker, args.port, keepalive=30)
    client.loop_start()
    client.publish(health, json.dumps({"ts": time.time(), "node": args.node, "status": "online", "reconnects": 0}), qos=1, retain=True)
    pub = Publisher(client, args.node)
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
                    pub.mmwave(phase, rnd)
                    pub.keystroke(phase, rnd)
                    if tick % 5 == 0:
                        pub.env(phase, rnd)
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
