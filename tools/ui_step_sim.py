"""앱 화면 빠른 점검 — hub 없이 앱이 받는 메시지를 계약 그대로 단계별로(기본 10 s) 보낸다.

    python tools/ui_step_sim.py --broker <PI4_IP>              # 10 s 간격
    python tools/ui_step_sim.py --broker <PI4_IP> --step 5 --card 20

hub 의 `state/phase`(retain)·`interaction/request`·`session/report` 를 흉내 내 Pi 5 화면 흐름만 빨리 본다:
대기 → 몰입(환경 이유) → 피로 의심 → 자세 자동 알림 → 환경 자동 알림(되돌리기) → 환경 제안 카드 → 회복 → 몰입 → 리포트.
화면에서 누른 응답(feedback/user)을 콘솔에 찍는다. **hub 를 같은 브로커에서 돌리는 중이면 먼저 끈다**(상태가 섞인다).
FSM 판정·제어 경로 검증은 tools/rehearsal_local.py·demo_dryrun.py 가 맡는다 — 이 도구는 화면 확인용이다.
리포트는 마지막에 한 번, retain 없이 보낸다(실제 10초 갱신은 hub 가 한다). 남은 가짜 retain 지우기: --clear-report
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time

BOOT = f"ui{random.getrandbits(24):06x}"


def _summary(env: str, present: bool = True) -> dict:
    base = {"present": present, "co2_ppm": 680, "temp_c": 24.2, "humidity_pct": 45.0, "lux": 420,
            "mmwave": {"motion_state": "active", "motion_level": 30, "drowsy_state": "AWAKE",
                       "resp_valid": False, "heart_valid": False}}
    if env == "warm":
        base.update(temp_c=28.6)
        base["env_flags"] = ["too_hot"]
    elif env == "stuffy":
        base.update(co2_ppm=1380, temp_c=28.5)
        base["env_flags"] = ["co2_high", "co2_rising", "too_hot"]
        base["mmwave"].update(motion_state="still", motion_level=4, drowsy_state="DROWSY")
    return base


# (설명, fsm_state, phase, context, gate, cause, c_fatigue, c_focus, 환경, 질문 여부)
STEPS = [
    ("대기 위젯", "IDLE", "idle", "npc", "none", None, 0.0, 0.0, "office", False),
    ("몰입 + '방이 더워요'", "FOCUS_PC", "focus", "pc", "none", None, 0.15, 0.14, "warm", False),
    ("피로 의심(노란 경고)", "FATIGUE_SUSPECT", "fatigue", "mixed", "none", None, 0.62, 0.11, "stuffy", False),
    ("자세 자동 알림", "ACTION_POSTURE", "fatigue", "mixed", "auto", "posture", 0.78, 0.13, "stuffy", False),
    ("모니터", "MONITOR", "fatigue", "mixed", "none", None, 0.78, 0.14, "stuffy", False),
    ("환경 자동 알림(되돌리기)", "ACTION_ENV", "fatigue", "mixed", "auto", "environment", 0.81, 0.15, "stuffy", False),
    ("모니터", "MONITOR", "fatigue", "mixed", "none", None, 0.79, 0.15, "stuffy", False),
    ("환경 제안 카드", "ACTION_ENV", "fatigue", "mixed", "suggest", "environment", 0.72, 0.15, "stuffy", True),
    ("회복", "RECOVERY", "recovery", "mixed", "none", None, 0.30, 0.17, "office", False),
    ("몰입 복귀", "FOCUS_MIXED", "focus", "mixed", "none", None, 0.21, 0.15, "office", False),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--step", type=float, default=10.0, help="단계 간격(초)")
    ap.add_argument("--card", type=int, default=30, help="제안 카드 만료(expires_in_s)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--clear-report", action="store_true", help="브로커에 retain 된 session/report 를 지우고 끝낸다")
    args = ap.parse_args(argv)
    import paho.mqtt.client as mqtt

    answered = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskmate-ui-step-{os.getpid()}")

    def on_feedback(_c, _u, msg):
        try:
            body = json.loads(msg.payload.decode("utf-8"))
        except ValueError:
            return
        d = body.get("data") if isinstance(body.get("data"), dict) else body
        extra = f" → {d.get('corrected_state')}" if d.get("corrected_state") else ""
        print(f"{time.strftime('%H:%M:%S')}   ◀ 화면 응답: {d.get('verdict')}{extra} "
              f"(id={d.get('request_id')}, {d.get('response_ms')} ms)", flush=True)
        if d.get("verdict") in ("accept", "reject", "timeout"):
            answered.set()

    client.on_connect = lambda c, *_: c.subscribe("deskmate/feedback/user", qos=1)
    client.message_callback_add("deskmate/feedback/user", on_feedback)
    client.connect(args.broker, args.port, keepalive=30)
    client.loop_start()
    if args.clear_report:
        client.publish("deskmate/session/report", b"", qos=1, retain=True).wait_for_publish(5)
        print("retain 된 session/report 를 지웠다", flush=True)
        client.loop_stop()
        client.disconnect()
        return 0
    client.publish("deskmate/health/hub", json.dumps({"ts": time.time(), "node": "hub", "status": "online",
                                                       "source": "ui_step_sim"}), qos=1, retain=True)
    seq = 0
    t0 = time.time()
    try:
        while True:
            for i, (label, state, phase, ctx, gate, cause, fat, foc, env, ask) in enumerate(STEPS, 1):
                seq += 1
                envelope = {"schema_version": "1.0", "ts": time.time(), "node": "hub", "boot_id": BOOT, "seq": seq,
                            "data": {"fsm_state": state, "phase": phase, "context": ctx, "c_focus": foc,
                                     "c_fatigue": fat, "confidence": max(fat, foc), "source": "fsm", "gate": gate,
                                     "cause": cause, "reasons": [], "sensor_summary": _summary(env)}}
                client.publish("deskmate/state/phase", json.dumps(envelope, ensure_ascii=False), qos=1, retain=True)
                print(f"{time.strftime('%H:%M:%S')} [{i:>2}/{len(STEPS)}] {label:<22} {state} gate={gate}", flush=True)
                if ask:
                    seq += 1
                    answered.clear()
                    req = {"schema_version": "1.0", "ts": time.time(), "node": "hub", "boot_id": BOOT, "seq": seq,
                           "data": {"request_id": f"{BOOT}-{seq}", "kind": "env_suggest",
                                    "prompt_code": "adjust_environment", "options": ["accept", "reject"],
                                    "expires_in_s": args.card, "evidence": [], "cause": cause, "c_fatigue": fat}}
                    client.publish("deskmate/interaction/request", json.dumps(req, ensure_ascii=False), qos=1)
                    print(f"           ▶ 제안 카드 보냄 — {args.card} s 안에 적용할게요/괜찮아요 (기다리는 중)", flush=True)
                    answered.wait(args.card + 5)
                    time.sleep(2)
                else:
                    time.sleep(args.step)
            report = {"schema_version": "1.0", "ts": time.time(), "node": "hub",
                      "data": {"duration_s": round(time.time() - t0, 1), "focus_time_s": 40.0, "focus_ratio": 0.4,
                               "fatigue_episodes": [{"t_onset": t0 + 20, "peak_fatigue": 0.81, "t_resolved": time.time()}],
                               "intervention_counts": {"total": 3, "recovered": 1}, "break_accept_rate": None,
                               "metrics": {"suggest_accept_rate": None, "correction_count": 0},
                               "state_durations_s": {"FOCUS_PC": 20.0, "FATIGUE_SUSPECT": 10.0, "RECOVERY": 10.0}}}
            # retain 하지 않는다 — 가짜 리포트가 브로커에 남으면 이후 실제 hub 가 없을 때 앱이 그 숫자를 계속 보여 준다.
            client.publish("deskmate/session/report", json.dumps(report, ensure_ascii=False), qos=1, retain=False)
            print(f"{time.strftime('%H:%M:%S')} 리포트 보냄 — 리포트 탭 확인", flush=True)
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
