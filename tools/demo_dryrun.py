"""시연 시나리오 dry-run — 브로커·실시간 없이 시나리오를 hub(LiveHub)에 흘려 전이를 몇 초 만에 본다.

    python tools/demo_dryrun.py [--scenario demo] [--respond accept|reject|none] [--json]

tools/mqtt_scenario_sim.py 와 **같은 payload** 를 만들어 MQTT 라우팅 함수(route_mqtt_message)로 cache 에 넣고,
가상 시계로 score_period(10 s)마다 tick 한다. 제어는 hub 안의 mock 플러그가 즉시 성공으로 답한다.
`--respond` 는 제안 카드(interaction/request)에 사람 대신 답한다(기본 accept, 4 s 뒤; timeout 은 카드 만료 뒤 무응답).

실시간 리허설(tools/rehearsal_local.py)과 시계만 다르고 경로는 같다. 시나리오나 fsm.demo.yaml 을 바꾸면
먼저 여기서 전이를 확인하고, tools/test_demo_scenario.py 가 기대 동선을 지킨다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hub"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from deskmate_hub.ingest import SensorCache, load_ingest_config  # noqa: E402
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message  # noqa: E402
from deskmate_hub.inference import load_config  # noqa: E402
from deskmate_hub.live import LiveHub  # noqa: E402
from mqtt_scenario_sim import SCENARIOS, Publisher, scenario  # noqa: E402

DEMO_CONFIG = os.path.join(ROOT, "hub", "deskmate_hub", "config", "fsm.demo.yaml")
T0 = 1_790_000_000.0   # 가상 시계 시작(2026-09 근처). 값 자체는 의미 없다.


def run_dryrun(name: str = "demo", *, fsm_config: str = DEMO_CONFIG, respond: str | None = "accept",
               respond_delay: float = 4.0, seed: int = 7, offset: int = 0,
               frame_log=None, undo_at: int | None = None, phases=None,
               tick_times: list[float] | None = None) -> dict[str, Any]:
    """시나리오 하나를 끝까지 돌려 전이·질문·제어·리포트를 돌려준다.

    offset: hub tick 과 시나리오 시작의 어긋남(초). 실시간 리허설에서는 둘이 따로 시작하므로 0~period-1 이 다 나온다.
    phases: 시나리오 이름 대신 Phase 목록을 직접 준다(시험에서 단계를 늘리거나 바꿀 때).
    tick_times: 주면 tick_once 한 번에 걸린 시간(초)을 모은다(tools/measure_tick.py).
    undo_at: 시나리오 시작 후 이 초에 자동 실행 알림의 '되돌리기'(request_id 없는 reject)를 누른다.
    frame_log: 주면 tick 마다 SensorFrame 을 리플레이 JSONL 로 쓴다(`python -m deskmate_hub --replay` 로 재현).
    """
    clock = {"t": T0}
    cache = SensorCache()
    out = io.StringIO()
    hub = LiveHub(cache, fsm_cfg=load_config(fsm_config), ingest_cfg=load_ingest_config(), out=out,
                 frame_log=frame_log)
    requests: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    hub.publish_request = lambda env: requests.append({"t": clock["t"] - T0, **env["data"]})
    if hub.control is not None:
        send = hub.control.publish_cmd
        hub.control.publish_cmd = lambda p: (commands.append({"t": clock["t"] - T0, **p}), send(p))[1]

    pub = Publisher(lambda topic, payload: route_mqtt_message(cache, topic, payload.encode("utf-8"), clock["t"]),
                    "sim", clock=lambda: clock["t"])
    rnd = random.Random(seed)
    period = int(hub.period)
    trace: list[dict[str, Any]] = []
    answer_at: tuple[float, str] | None = None
    prev = None
    second = 0
    for phase in (phases if phases is not None else scenario(name)):
        for tick in range(int(phase.seconds)):
            pub.elapsed = float(tick)
            pub.second(phase, tick, rnd)
            if respond and answer_at is None and requests and hub.pending_request is not None \
                    and requests[-1]["request_id"] == hub.pending_request["data"]["request_id"]:
                # timeout 은 화면이 카드 만료(expires_in_s) 뒤 보내는 무응답 라벨을 흉내 낸다
                delay = requests[-1]["expires_in_s"] if respond == "timeout" else respond_delay
                answer_at = (clock["t"] + delay, requests[-1]["request_id"])
            if answer_at is not None and clock["t"] >= answer_at[0]:
                fb = {"request_id": answer_at[1], "verdict": respond}
                if respond != "timeout":
                    fb["response_ms"] = int(respond_delay * 1000)
                cache.put_feedback(fb)
                answer_at = None
            if undo_at is not None and second == undo_at:
                cache.put_feedback({"verdict": "reject", "request_id": "atlas-display"})
            if (second + offset) % period == 0:
                t_tick = time.perf_counter()
                env = hub.tick_once(clock["t"])
                if tick_times is not None:
                    tick_times.append(time.perf_counter() - t_tick)
                d = env["data"]
                if d["fsm_state"] != prev:
                    trace.append({"t": round(clock["t"] - T0), "phase": phase.name, "state": d["fsm_state"],
                                  "gate": d.get("gate"), "cause": d.get("cause"),
                                  "c_fatigue": round(float(d.get("c_fatigue", 0)), 2),
                                  "c_focus": round(float(d.get("c_focus", 0)), 2),
                                  "env_flags": (d.get("sensor_summary") or {}).get("env_flags", [])})
                    prev = d["fsm_state"]
            clock["t"] += 1
            second += 1
    report = hub.report_envelope(clock["t"])
    return {"scenario": name, "duration_s": second, "trace": trace, "requests": requests,
            "commands": [{k: c.get(k) for k in ("t", "target_id", "operation", "value", "gate")} for c in commands],
            "report": report.get("data", report)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=SCENARIOS, default="demo")
    ap.add_argument("--config", default=DEMO_CONFIG, help="FSM 설정(기본 fsm.demo.yaml)")
    ap.add_argument("--respond", choices=["accept", "reject", "timeout", "none"], default="accept",
                    help="제안 카드 응답: timeout = 화면 카드 만료 뒤 무응답 전송")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--offset", type=int, default=0, help="hub tick 과 시나리오 시작의 어긋남(초)")
    ap.add_argument("--undo-at", type=int, metavar="초", help="이 시각에 자동 실행 '되돌리기'를 누른다")
    ap.add_argument("--frames-out", metavar="frames.jsonl", help="리플레이용 SensorFrame JSONL 저장")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    flog = open(args.frames_out, "w", encoding="utf-8") if args.frames_out else None
    res = run_dryrun(args.scenario, fsm_config=args.config,
                     respond=None if args.respond == "none" else args.respond, seed=args.seed,
                     offset=args.offset, frame_log=flog,
                     undo_at=args.undo_at)
    if flog is not None:
        flog.close()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print(f"== {res['scenario']} — {res['duration_s']} s ==")
    for r in res["trace"]:
        flags = ",".join(r["env_flags"])
        print(f"{r['t']:>4}s [{r['phase']:<13}] {r['state']:<17} gate={r['gate']:<7} cause={str(r['cause']):<11} "
              f"fat={r['c_fatigue']:.2f} foc={r['c_focus']:.2f} {flags}")
    for q in res["requests"]:
        print(f"{q['t']:>4.0f}s 질문 {q['kind']} ({q['request_id']})")
    for c in res["commands"]:
        print(f"{c['t']:>4.0f}s 제어 {c['target_id']}.{c['operation']}={c['value']} ({c['gate']})")
    print("report:", json.dumps(res["report"], ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
