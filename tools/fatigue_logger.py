"""피로 데이터 수집 기록기 — 작업 세션마다 hub 상태 tick 과 Pi 5 피로 응답을 한 파일에 모은다.

    python tools/fatigue_logger.py --broker <PI4_IP>              # 기본 저장 위치 ml/datasets/fatigue/raw/

Pi 5 피로 기록 앱(display/atlas/fatigue_app)에서 작업을 시작하면(`deskmate/fatigue/label` session_start) 그때부터
`deskmate/state/phase`(10 s, sensor_summary 포함)를 `<out>/<profile_id>/<session_id>.jsonl` 에 붙여 쓴다.
session_end 가 오면 멈춘다. 데이터 형식과 수집 절차: docs/fatigue-dataset.md.

센서 원본은 받지 않는다 — hub 가 화면용으로 내보내는 요약(ToF 자세 클래스, mmWave, 환경 원값, 키스트로크
타이밍 통계)만 남는다. 저장 위치는 gitignore(ml/datasets/). 아무것도 발행하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, TextIO

STATE_TOPIC = "deskmate/state/phase"
FATIGUE_TOPIC = "deskmate/fatigue/label"
EVENTS = ("session_start", "kss", "sleepy", "prompt_missed", "session_end")
# 파일 이름으로 쓰므로 경로 문자를 막는다.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TICK_KEYS = ("fsm_state", "phase", "context", "c_fatigue", "c_focus", "gate", "cause", "reasons", "sensor_summary")


class FatigueRecorder:
    """MQTT 없이 시험할 수 있게 메시지 처리만 떼어 둔다."""

    def __init__(self, out_dir: str, out: TextIO = sys.stdout) -> None:
        self.out_dir = out_dir
        self.out = out
        self.session: tuple[str, str] | None = None  # (profile_id, session_id)
        self._seen: set[tuple[Any, ...]] = set()
        self.ticks = 0

    def path(self, profile_id: str, session_id: str) -> str:
        return os.path.join(self.out_dir, profile_id, f"{session_id}.jsonl")

    def _write(self, profile_id: str, session_id: str, record: dict[str, Any]) -> None:
        path = self.path(profile_id, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def handle(self, topic: str, payload: bytes, *, retained: bool = False, rx_ts: float | None = None) -> str | None:
        """처리한 기록 종류(label/tick)를 돌려준다. 버린 메시지는 None."""
        rx_ts = time.time() if rx_ts is None else rx_ts
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None
        if topic == FATIGUE_TOPIC:
            return self._label(data, rx_ts)
        if topic == STATE_TOPIC and self.session is not None and not retained:
            # retain 은 세션 전의 마지막 상태라 이 세션 기록이 아니다.
            record = {"kind": "tick", "rx_ts": rx_ts, "ts": body.get("ts"), "seq": body.get("seq")}
            record.update({k: data[k] for k in _TICK_KEYS if k in data})
            self._write(*self.session, record)
            self.ticks += 1
            return "tick"
        return None

    def _label(self, data: dict[str, Any], rx_ts: float) -> str | None:
        event, sid, pid = data.get("event"), data.get("session_id"), data.get("profile_id")
        if event not in EVENTS or not isinstance(sid, str) or not isinstance(pid, str) \
                or not _SAFE_ID.match(sid) or not _SAFE_ID.match(pid):
            print(f"[fatigue] 알 수 없는 기록 무시: {data!r}", file=self.out, flush=True)
            return None
        key = (sid, event, data.get("ts"))
        if key in self._seen:  # QoS 1 재전송·오프라인 재발행 중복
            return None
        self._seen.add(key)
        if event != "session_end":
            # 기록기를 세션 도중에 켜도 다음 응답부터는 tick 을 붙인다.
            if self.session != (pid, sid):
                self.ticks = 0
            self.session = (pid, sid)
        self._write(pid, sid, {"kind": "label", "rx_ts": rx_ts, **data})
        kss = f" KSS={data['kss']}" if "kss" in data else ""
        print(f"{time.strftime('%H:%M:%S')} [{pid}] {event}{kss}  (tick {self.ticks})", file=self.out, flush=True)
        if event == "session_end" and self.session == (pid, sid):
            print(f"[fatigue] 저장: {self.path(pid, sid)}", file=self.out, flush=True)
            self.session = None
        return "label"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=os.environ.get("DESKMATE_BROKER", "localhost"))
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--out", default=os.path.join("ml", "datasets", "fatigue", "raw"))
    args = ap.parse_args(argv)
    import paho.mqtt.client as mqtt

    recorder = FatigueRecorder(args.out)

    def on_connect(client, _u, _f, reason, _p):
        if reason != 0:
            print(f"[fatigue] 연결 거부: {reason}", flush=True)
            return
        client.subscribe([(STATE_TOPIC, 1), (FATIGUE_TOPIC, 1)])
        print(f"[fatigue] {args.broker}:{args.port} 연결 — Pi 5 피로 기록 앱에서 작업을 시작하세요. 저장: {args.out}",
              flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskmate-fatigue-{os.getpid()}")
    client.on_connect = on_connect
    client.on_disconnect = lambda *_: print("[fatigue] 끊김 — 자동 재연결 중(그동안의 tick 은 빠진다)", flush=True)
    client.on_message = lambda _c, _u, msg: recorder.handle(msg.topic, msg.payload, retained=bool(msg.retain))
    try:
        client.connect(args.broker, args.port, keepalive=30)
    except OSError as exc:
        print(f"[fatigue] {args.broker}:{args.port} 에 붙지 못함: {exc}", file=sys.stderr)
        return 1
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
