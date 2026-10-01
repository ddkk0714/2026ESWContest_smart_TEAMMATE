"""MQTT 감시 — 실기 점검 때 PC 에서 DESKMATE 토픽을 한 줄씩 읽기 쉽게 본다.

    python tools/mqtt_watch.py --broker <PI4_IP>                  # 전부
    python tools/mqtt_watch.py --broker <PI4_IP> --only state,request,feedback,control
    python tools/mqtt_watch.py --broker <PI4_IP> --raw            # payload 원문

센서 원시 스트림(1 Hz)은 기본으로 줄여서(10 개에 1 번) 보여 준다. 점검표: docs/field-checklist.md.
paho-mqtt 만 쓴다(tools/requirements.txt). 아무것도 발행하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# 짧은 이름 → 토픽 필터
GROUPS = {
    "health": "deskmate/health/#",
    "sensor": "deskmate/sensor/#",
    "state": "deskmate/state/phase",
    "request": "deskmate/interaction/request",
    "feedback": "deskmate/feedback/user",
    "control": "deskmate/control/#",
    "report": "deskmate/session/report",
    "message": "deskmate/display/message",
}


def _data(body: Any) -> dict:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def summarize(topic: str, payload: bytes) -> str:
    """토픽별로 점검에 필요한 값만 한 줄로."""
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"(JSON 아님 {len(payload)} B)"
    d = _data(body)
    seq = body.get("seq") if isinstance(body, dict) else None
    if topic == "deskmate/state/phase":
        s = d.get("sensor_summary") or {}
        env = " ".join(f"{k}={s[k]}" for k in ("co2_ppm", "temp_c", "humidity_pct", "lux") if k in s)
        flags = ",".join(s.get("env_flags") or [])
        present = s.get("present", "?")
        ctl = (s.get("control") or {})
        ctl_txt = f" ctl={ctl.get('state')}/{ctl.get('outcome')}" if ctl.get("active") else ""
        return (f"seq={seq} {d.get('fsm_state')} gate={d.get('gate')} cause={d.get('cause')} "
                f"fat={d.get('c_fatigue')} foc={d.get('c_focus')} present={present} {env}"
                + (f" flags={flags}" if flags else "") + ctl_txt)
    if topic == "deskmate/interaction/request":
        return f"질문 {d.get('kind')} id={d.get('request_id')} 만료={d.get('expires_in_s')}s cause={d.get('cause')}"
    if topic == "deskmate/feedback/user":
        extra = f" → {d.get('corrected_state')}" if d.get("corrected_state") else ""
        return f"응답 {d.get('verdict')}{extra} id={d.get('request_id')} {d.get('response_ms')} ms"
    if topic == "deskmate/control/cmd":
        return f"명령 {d.get('target_id')}.{d.get('operation')}={d.get('value')!r} gate={d.get('gate')} id={d.get('command_id')}"
    if topic == "deskmate/control/result":
        return f"결과 {d.get('command_id')} {d.get('status')} 실제값={d.get('actual_value')!r}"
    if topic == "deskmate/session/report":
        m = d.get("metrics") or {}
        keep = {k: m.get(k) for k in ("suggest_accept_rate", "timeout_rate", "auto_undo_rate",
                                      "correction_count", "median_response_ms") if k in m}
        return (f"리포트 {d.get('duration_s')} s 집중={d.get('focus_time_s')} s "
                f"피로={len(d.get('fatigue_episodes') or [])} 개입={(d.get('intervention_counts') or {}).get('total')} "
                f"metrics={json.dumps(keep, ensure_ascii=False)}")
    if topic.startswith("deskmate/health/"):
        return f"{d.get('status')} " + " ".join(f"{k}={v}" for k, v in d.items() if k not in ("status", "node"))
    if topic.startswith("deskmate/sensor/mmwave"):
        return (f"seq={seq} present={d.get('present')} motion={d.get('motion_level')} drowsy={d.get('drowsy_state')} "
                f"resp={d.get('resp_bpm')}({d.get('resp_valid')}) heart={d.get('heart_bpm')}({d.get('heart_valid')})")
    if topic.startswith("deskmate/sensor/env"):
        return f"seq={seq} " + " ".join(f"{k}={d.get(k)}" for k in ("co2_ppm", "temp_c", "humidity_pct", "lux"))
    if topic.startswith("deskmate/sensor/keystroke"):
        return (f"typing={d.get('typing_active')} idle={d.get('idle_ratio')} "
                f"flight_cv={d.get('flight_cv')} corr={d.get('correction_rate')}")
    return json.dumps(d, ensure_ascii=False)[:200]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--only", help="쉼표로 묶은 그룹: " + ",".join(GROUPS))
    ap.add_argument("--raw", action="store_true", help="payload 원문 출력")
    ap.add_argument("--sensor-every", type=int, default=10, help="센서 원시 스트림은 N 개에 1 번만(1 = 전부)")
    args = ap.parse_args(argv)
    import paho.mqtt.client as mqtt

    groups = [g.strip() for g in args.only.split(",")] if args.only else list(GROUPS)
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        ap.error(f"모르는 그룹: {unknown}")
    counts: dict[str, int] = {}

    def on_connect(client, _u, _f, reason, _p):
        if reason != 0:
            print(f"[watch] 연결 거부: {reason}", flush=True)
            return
        client.subscribe([(GROUPS[g], 0) for g in groups])
        print(f"[watch] {args.broker}:{args.port} 연결, 구독: {', '.join(groups)}", flush=True)

    def on_message(_c, _u, msg):
        if msg.topic.startswith("deskmate/sensor/"):
            counts[msg.topic] = counts.get(msg.topic, 0) + 1
            if (counts[msg.topic] - 1) % max(1, args.sensor_every):
                return
        text = msg.payload.decode("utf-8", "replace") if args.raw else summarize(msg.topic, msg.payload)
        retained = " (retain)" if msg.retain else ""
        print(f"{time.strftime('%H:%M:%S')} {msg.topic}{retained}  {text}", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskmate-watch-{os.getpid()}")
    client.on_connect = on_connect
    client.on_disconnect = lambda *_: print("[watch] 끊김 — 자동 재연결 중", flush=True)
    client.on_message = on_message
    try:
        client.connect(args.broker, args.port, keepalive=30)
    except OSError as exc:
        print(f"[watch] {args.broker}:{args.port} 에 붙지 못함: {exc}", file=sys.stderr)
        return 1
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
