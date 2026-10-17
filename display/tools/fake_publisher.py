"""브로커 배선 점검용 — collector 없이 가짜 keystroke 페이로드를 MQTT 로 발행한다.

  python display/tools/fake_publisher.py --broker <pi5-ip>

Pi 5 화면이 실제 MQTT 경로로 갱신되는지 확인할 때 쓴다.
페이로드 스키마는 collector/features.py 의 to_payload() 와 같다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskmate_display.demo import _payload  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="가짜 keystroke 발행기")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--topic", default="deskmate/sensor/keystroke")
    p.add_argument("--period", type=float, default=1.0)
    a = p.parse_args()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fake-collector")
    c.connect(a.broker, a.port, keepalive=30)
    c.loop_start()
    print(f"[fake] {a.broker}:{a.port} → {a.topic} @ {a.period}s. Ctrl+C 로 종료")

    t0 = time.time()
    try:
        while True:
            payload = _payload(time.time() - t0)
            c.publish(a.topic, json.dumps(payload), qos=0)
            print(".", end="", flush=True)
            time.sleep(a.period)
    except KeyboardInterrupt:
        print("\n[fake] 종료")
    finally:
        c.loop_stop()
        c.disconnect()


if __name__ == "__main__":
    main()
