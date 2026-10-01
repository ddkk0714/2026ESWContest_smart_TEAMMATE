"""브로커 재시작 — hub 의 MqttSource 가 스스로 다시 붙고, 구독·발행을 이어 가는지 (W6).

실제 MQTT 브로커(amqtt, 개발용 선택 의존)를 띄웠다 내렸다 한다. amqtt·paho 가 없으면 건너뛴다.
Pi 4 보드 경로(C++ 네이티브 브리지)의 재연결은 실기 체크리스트에서 따로 본다.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

pytest.importorskip("amqtt")
mqtt = pytest.importorskip("paho.mqtt.client")

from amqtt.broker import Broker  # noqa: E402

# amqtt 0.12 의 설정 형식 경고는 이 시험과 무관하다(개발용 브로커).
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

from deskmate_hub.ingest import SensorCache  # noqa: E402
from deskmate_hub.ingest.mqtt_source import MqttSource  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BrokerThread:
    def __init__(self, port: int) -> None:
        self.port = port
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._ready.clear()
        cfg = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}},
               "sys_interval": 0, "auth": {"allow-anonymous": True}, "topic-check": {"enabled": False}}

        async def main() -> None:
            broker = Broker(cfg)
            await broker.start()
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.05)
            await broker.shutdown()

        self._thread = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "broker did not start"

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(10)


def _wait(cond, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _publish(port: int, topic: str, data: dict, *, retain: bool = False) -> None:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"t-{time.time_ns()}")
    c.connect("127.0.0.1", port, keepalive=10)
    c.loop_start()
    body = {"schema_version": "1.0", "ts": time.time(), "node": "t", "boot_id": "b", "seq": 1, "data": data}
    c.publish(topic, json.dumps(body), qos=1, retain=retain).wait_for_publish(5)
    c.loop_stop()
    c.disconnect()


def _subscribe_once(port: int, topic: str, timeout: float = 10.0) -> dict | None:
    got: list[dict] = []
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"s-{time.time_ns()}")
    c.on_connect = lambda cl, *_: cl.subscribe(topic, qos=1)
    c.on_message = lambda _c, _u, m: got.append(json.loads(m.payload))
    c.connect("127.0.0.1", port, keepalive=10)
    c.loop_start()
    _wait(lambda: bool(got), timeout)
    c.loop_stop()
    c.disconnect()
    return got[0] if got else None


def test_hub_reconnects_after_broker_restart_and_resumes():
    port = _free_port()
    broker = BrokerThread(port)
    broker.start()
    cache = SensorCache()
    logs: list[str] = []
    source = MqttSource(cache, "127.0.0.1", port, client_id=f"hub-{time.time_ns()}", on_log=logs.append)
    source.start()
    try:
        assert _wait(lambda: source.connected), logs
        _publish(port, "deskmate/sensor/env/t", {"co2_ppm": 700, "co2_valid": True})
        assert _wait(lambda: cache.env is not None), logs
        first = cache.env.received

        broker.stop()                                         # 브로커 재시작(전원·프로세스 재기동 모사)
        assert _wait(lambda: not source.connected), logs
        source.publish_state({"seq": 0})                      # 끊긴 동안 발행해도 예외 없이 건너뛴다

        broker.start()
        assert _wait(lambda: source.connected, 45), logs      # paho 재연결 지연 1~30 s
        # 재연결 뒤 다시 구독돼서 새 표본이 들어온다
        _publish(port, "deskmate/sensor/env/t", {"co2_ppm": 900, "co2_valid": True})
        assert _wait(lambda: cache.env is not None and cache.env.received > first), logs
        assert cache.env.data["co2_ppm"] == 900
        # 상태 발행도 다시 나간다(retain 이라 늦게 붙은 화면도 받는다)
        source.publish_state({"schema_version": "1.0", "seq": 7, "data": {"fsm_state": "FOCUS_PC"}})
        got = _subscribe_once(port, "deskmate/state/phase")
        assert got is not None and got["seq"] == 7
        assert any("connected" in m for m in logs) and any("disconnected" in m for m in logs)
    finally:
        source.stop()
        broker.stop()
