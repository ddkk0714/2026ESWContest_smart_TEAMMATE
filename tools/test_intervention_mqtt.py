"""실제 로컬 MQTT + LiveHub + 외부 mock 응답기로 개입 5가지 경로를 검증한다.

센서와 Pi 5 응답은 합성이다. 시간만 가상으로 진행해 운영 임계값을 바꾸지 않는다.
이 시험은 항상 임시 loopback 브로커를 사용하며 실제 보드에 발행하지 않는다.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import random
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))
pytest.importorskip("amqtt")
mqtt = pytest.importorskip("paho.mqtt.client")
from amqtt.broker import Broker
from deskmate_hub.control import MockPlugAdapter, load_control_config
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.ingest.mqtt_source import MqttSource
from deskmate_hub.inference import load_config
from deskmate_hub.live import LiveHub
from demo_dryrun import DEMO_CONFIG, T0, run_dryrun
from intervention_check import COMMAND, FEEDBACK, REPORT, REQUEST, RESULT, STATE, TOPICS, capture, check_cycle
from mqtt_scenario_sim import Publisher, scenario

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def broker_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ready, stop = threading.Event(), threading.Event()
    errors = []

    async def serve():
        cfg = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
               "sys_interval": 0, "auth": {"allow-anonymous": True}, "topic-check": {"enabled": False}}
        broker = Broker(cfg)
        try:
            await broker.start()
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.01)
        finally:
            await broker.shutdown()

    def worker():
        try:
            asyncio.run(serve())
        except Exception as exc:
            errors.append(exc)
            ready.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert ready.wait(10) and not errors, errors
        yield port
    finally:
        stop.set()
        thread.join(10)
        assert not thread.is_alive() and not errors, errors


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("MQTT evidence delivery timed out")


def test_field_capture_records_only_intervention_topics(broker_port, tmp_path):
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"capture-test-{time.time_ns()}")
    publisher.connect("127.0.0.1", broker_port)
    publisher.loop_start()
    path = tmp_path / "capture.jsonl"
    try:
        publisher.publish(STATE, json.dumps({"data": {"fsm_state": "IDLE"}}), qos=1,
                          retain=True).wait_for_publish(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            recording = pool.submit(capture, "127.0.0.1", broker_port, 1.0, path)
            # retained 초기 상태가 파일에 쓰이면 구독이 완료된 것이다.
            wait_for(lambda: path.exists() and path.stat().st_size > 0)
            publisher.publish("deskmate/sensor/tof/t", json.dumps({"raw": "not-for-recording"}), qos=1)
            publisher.publish(STATE, json.dumps({"data": {"fsm_state": "START"}}), qos=1).wait_for_publish(5)
            events = recording.result(timeout=5)
        assert [e["topic"] for e in events] == [STATE, STATE]
        assert events[0]["retain"] and not events[1]["retain"]
        assert "not-for-recording" not in path.read_text(encoding="utf-8")
    finally:
        publisher.disconnect()
        publisher.loop_stop()


class ClockedSource(MqttSource):
    """수신 시간만 가상 시계로 넣고, 구독·발행은 제품 MQTT 구현을 그대로 사용한다."""
    def __init__(self, cache, port, clock):
        super().__init__(cache, "127.0.0.1", port, client_id=f"cycle-hub-{time.time_ns()}")
        self.clock = clock
        self.received = Counter()
        self.subscribed = threading.Event()
        self._client.on_subscribe = lambda *_: self.subscribed.set()

    def _on_message(self, _c, _u, msg):
        route_mqtt_message(self.cache, msg.topic, msg.payload, self.clock["t"])
        self.received[msg.topic] += 1


@pytest.mark.parametrize("case", ["accept", "reject", "timeout", "undo", "correct", "no_response", "control_failed", "control_timeout"])
def test_feedback_control_recovery_and_report_over_mqtt(broker_port, tmp_path, case):
    clock = {"t": T0}
    events = []
    source = ClockedSource(SensorCache(), broker_port, clock)
    actor = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"cycle-display-plug-{time.time_ns()}")
    ready = threading.Event()
    sent = Counter()

    def send(topic, body):
        payload = body if isinstance(body, str) else json.dumps(body)
        actor.publish(topic, payload, qos=1)
        sent[topic] += 1

    plug = MockPlugAdapter(lambda result: send(RESULT, {"data": result}), clock=lambda: clock["t"])

    def on_message(_c, _u, msg):
        body = json.loads(msg.payload)
        events.append({"topic": msg.topic, "payload": body, "retain": bool(msg.retain)})
        if msg.topic == COMMAND:
            if case == "control_failed":
                send(RESULT, {"data": {"command_id": body["data"]["command_id"], "status": "failed", "error_code": "mock_failure"}})
            elif case != "control_timeout":
                plug.handle_command(body["data"])

    actor.on_connect = lambda c, *_: c.subscribe([(topic, 1) for topic in TOPICS])
    actor.on_subscribe = lambda *_: ready.set()
    actor.on_message = on_message
    source.start()
    actor.connect("127.0.0.1", broker_port)
    actor.loop_start()
    config = load_control_config()
    config["esm_log_path"] = str(tmp_path / "esm.jsonl")
    hub = LiveHub(source.cache, fsm_cfg=load_config(DEMO_CONFIG), ingest_cfg=load_ingest_config(),
                  control_cfg=config, publish=source.publish_state, publish_request=source.publish_request,
                  publish_control=source.publish_control, out=io.StringIO())
    assert hub.mock_plug is None  # 프로세스 내 성공 폴백 없이 결과가 브로커를 왕복한다.
    pub = Publisher(send, "cycle-sim", clock=lambda: clock["t"])
    rnd = random.Random(7)
    pending_answer = None
    answered = set()
    undo_sent = correction_sent = False
    trace = []
    second = 0
    try:
        assert ready.wait(5) and source.subscribed.wait(5)
        for phase in scenario("demo"):
            for tick in range(int(phase.seconds)):
                pub.elapsed = float(tick)
                pub.second(phase, tick, rnd)
                for topic in list(sent):
                    wait_for(lambda topic=topic: source.received[topic] >= sent[topic])
                requests = [e["payload"]["data"] for e in events if e["topic"] == REQUEST]
                if pending_answer is None and requests and requests[-1]["request_id"] not in answered:
                    q = requests[-1]
                    delay = q["expires_in_s"] if case == "timeout" else 4
                    pending_answer = (clock["t"] + delay, q["request_id"])
                if case != "no_response" and pending_answer and clock["t"] >= pending_answer[0]:
                    verdict = case if case in ("reject", "timeout") else "accept"
                    send(FEEDBACK, {"request_id": pending_answer[1], "verdict": verdict, "response_ms": 4000})
                    answered.add(pending_answer[1])
                    pending_answer = None
                if case == "correct" and second == 130:
                    send(FEEDBACK, {"verdict": "correct", "corrected_state": "REST", "response_ms": 2000})
                    correction_sent = True
                if case == "undo" and not undo_sent:
                    episodes = hub.control.history + ([hub.control.episode] if hub.control.episode else [])
                    executed = next((e.executed_ts for e in episodes if e.executed_ts is not None), None)
                    if executed is not None and clock["t"] >= executed + 15:
                        send(FEEDBACK, {"verdict": "reject", "request_id": "atlas-display"})
                        undo_sent = True
                wait_for(lambda: source.received[FEEDBACK] >= sent[FEEDBACK])
                if second % int(hub.period) == 0:
                    env = hub.tick_once(clock["t"])
                    source.publish_report(hub.report_envelope(clock["t"]))
                    trace.append(env["data"]["fsm_state"])
                    wait_for(lambda: any(e["topic"] == STATE and e["payload"].get("seq") == env["seq"] for e in events))
                    wait_for(lambda: any(e["topic"] == REPORT and e["payload"].get("ts") == clock["t"] for e in events))
                    episodes = hub.control.history + ([hub.control.episode] if hub.control.episode else [])
                    command_count = sum(c.sent_ts is not None for ep in episodes for c in ep.commands)
                    if case != "control_timeout":
                        wait_for(lambda: source.received[RESULT] >= command_count)
                second += 1
                clock["t"] += 1
        evidence_case = "timeout" if case == "no_response" else "accept" if case in ("control_failed", "control_timeout") else case
        evidence = check_cycle(events, evidence_case, config)
        (tmp_path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        report = hub.report_envelope(clock["t"])["data"]
        assert "MONITOR" in trace and "RECOVERY" in trace
        if case in ("control_failed", "control_timeout"):
            assert not evidence["passed"]
            status = "failed" if case == "control_failed" else "timeout"
            assert report["control_results"]["forward"][status] == 2
            assert report["control_results"]["forward"]["succeeded"] == 0
            assert plug.state == {}
        elif case == "no_response":
            assert not evidence["passed"]  # no display response was observed by the passive checker
            assert report["metrics"]["timeout_rate"] == 1.0
            assert report["control_results"]["forward"]["total"] == 0
            assert hub.pending_request is None
        else:
            assert evidence["passed"], evidence
        labels = [json.loads(line) for line in (tmp_path / "esm.jsonl").read_text(encoding="utf-8").splitlines()]
        verdict = "timeout" if case == "no_response" else case if case in ("reject", "timeout") else "accept"
        assert sum(l["verdict"] == verdict for l in labels) == 1
        if case == "no_response":
            assert any(l["source"] == "hub" and l["verdict"] == "timeout" for l in labels)
        if case == "correct":
            assert correction_sent and any(l["corrected_state"] == "REST" for l in labels)
            # 같은 입력의 대조군과 전이 순서를 비교한다. 정정은 판정을 덮어쓰지 않는다.
            actual = [s for i, s in enumerate(trace) if i == 0 or trace[i - 1] != s]
            assert actual == [r["state"] for r in run_dryrun("demo")["trace"]]
        if case == "undo":
            assert undo_sent and plug.state == {"vent_fan": {"set_power": "off"},
                                                "desk_lamp": {"set_brightness": 40}}
    finally:
        actor.disconnect()
        actor.loop_stop()
        source.stop()
