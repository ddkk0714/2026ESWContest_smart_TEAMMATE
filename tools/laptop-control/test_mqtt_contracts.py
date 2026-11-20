# ruff: noqa: E402 -- repository source paths are set before test imports
"""Actual loopback MQTT both directions. Only Fixture and Mock adapters."""

import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "hub/tests"))
import paho.mqtt.client as mqtt
from test_mqtt_reconnect import BrokerThread, _free_port, _wait
from deskmate_hub.ingest.cache import SensorCache, Sample
from deskmate_hub.ingest.mqtt_source import MqttSource
from deskmate_hub.ingest.appliance_display import display_appliances
from deskmate_hub.control.device_adapters import MockDeviceAdapter
from deskmate_hub.control.device_gateway import DeviceGateway, CMD_TOPIC, RESULT_TOPIC
from deskmate_hub.control.devices import Device, DeviceRegistry, Capability
from deskmate_hub.control.dispatcher import ControlDispatcher
from deskmate_hub.inference import GateMode
from thinq_host import HostSession, Session
from poc.worker import FixtureSdk
from thinq_observer import CommandObserver


def test_get_observation_to_hub_and_approved_mock_command_result(tmp_path):
    port = _free_port()
    broker = BrokerThread(port)
    cache = SensorCache()
    env = Sample(time.time(), time.time(), 1, {"co2_ppm": 700, "co2_valid": True})
    cache.put("env", env)
    source = MqttSource(cache, "127.0.0.1", port, client_id="thinq-contract-hub")
    subscribed = set()
    source._client.on_subscribe = lambda *_: subscribed.add("source")
    ui = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="contract-ui")
    gateway_client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="contract-mock-gateway"
    )
    publisher = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="fixture-reader"
    )
    observed = CommandObserver()
    results = []
    issued = []
    registry = DeviceRegistry()
    registry.register(
        Device(
            "air_purifier",
            "mock",
            ("air_cleaning",),
            {"set_mode": Capability("set_mode", ("low", "auto"), verified=True)},
            online=True,
            state={"set_mode": "auto"},
            observed_at=time.time(),
        )
    )
    adapter = MockDeviceAdapter({"air_purifier": {"set_mode": "auto"}})

    def publish_command(command):
        body = {
            "schema_version": "1.0",
            "node": "hub",
            "ts": time.time(),
            "data": command,
        }
        issued.append(body)
        publisher.publish(
            CMD_TOPIC, json.dumps(body), qos=1, retain=False
        ).wait_for_publish(5)

    dispatcher = ControlDispatcher(
        {
            "result_timeout_sec": 15,
            "actions": {
                "environment": [
                    {
                        "target_id": "air_purifier",
                        "operation": "set_mode",
                        "value": "low",
                        "undo_value": "auto",
                    }
                ]
            },
        },
        publish_cmd=publish_command,
    )
    gateway = DeviceGateway(
        registry,
        {"mock": adapter},
        enabled=True,
        publish_result=lambda topic, body: gateway_client.publish(
            topic, json.dumps(body), qos=1, retain=False
        ),
    )
    gateway.attach(gateway_client)
    gateway_client.on_subscribe = lambda *_: subscribed.add("gateway")
    ui.on_connect = lambda client, *_: client.subscribe(
        [(CMD_TOPIC, 1), (RESULT_TOPIC, 1)]
    )
    ui.on_subscribe = lambda *_: subscribed.add("ui")

    def receive(_client, _userdata, message):
        if message.topic == CMD_TOPIC:
            observed.observe(message.topic, message.payload, retained=message.retain)
        else:
            body = json.loads(message.payload)
            results.append(body)
            dispatcher.on_result(body["data"])

    ui.on_message = receive
    broker.start()
    try:
        source.start()
        for client in (ui, gateway_client, publisher):
            client.connect("127.0.0.1", port)
            client.loop_start()
        assert _wait(
            lambda: subscribed == {"source", "gateway", "ui"}
            and publisher.is_connected(),
            10,
        )
        fixture = json.loads(
            (Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text()
        )
        sdk = FixtureSdk(fixture)
        host = HostSession(Session(sdk, {"enabled": False, "backend": "fixture"}))
        key = host.claim()["key"]
        read = asyncio.run(host.call(key, {"op": "read"}))
        export = asyncio.run(host.call(key, {"op": "observation"}))
        payload = json.dumps(export["envelope"])
        publisher.publish(
            export["topic"], payload, qos=0, retain=False
        ).wait_for_publish(5)

        def available():
            return (
                bool(cache.appliances.snapshot())
                and next(iter(cache.appliances.snapshot().values()))["available"]
            )

        assert _wait(available, 10)
        assert cache.env is env and "post" not in sdk.calls
        view = display_appliances(cache.appliances)[0]
        assert view["observations"]["pm2"]["value"] == 8
        assert view["connection"] == "unknown"
        received_at = view["hub_received_at"]
        # A duplicate observation cannot refresh its receipt, even through MQTT.
        publisher.publish(
            export["topic"], payload, qos=0, retain=False
        ).wait_for_publish(5)
        time.sleep(0.1)
        assert display_appliances(cache.appliances)[0]["hub_received_at"] == received_at

        dispatcher.on_enter_action("ACTION_ENV", "environment", GateMode.SUGGEST)
        assert adapter.calls == [] and dispatcher.episode.awaiting_user
        assert issued == []
        dispatcher.on_feedback("accept")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            r["data"]["status"] == "succeeded" for r in results
        ):
            asyncio.run(gateway.pump())
            time.sleep(0.02)
        assert len(adapter.calls) == 1
        assert dispatcher.action_done()
        assert results[-1]["node"] == "device-gateway-mock"
        assert results[-1]["data"]["actual_value"] == "low"
        assert observed.view()["commands"][0]["approval_verified"] is False
        assert not observed.view()["commands"][0]["post_attempted"]
        assert "post" not in sdk.calls
        # No replay of a write on a duplicate command.
        publisher.publish(
            CMD_TOPIC, json.dumps(issued[-1]), qos=1, retain=False
        ).wait_for_publish(5)
        time.sleep(0.1)
        assert len(adapter.calls) == 1
        (tmp_path / "mqtt-contract.json").write_text(
            json.dumps(
                {
                    "snapshot": read["snapshot"],
                    "hub_appliance": view,
                    "observed_commands": observed.view(),
                    "result_mode": "mock",
                    "sdk_post_count": sdk.calls.count("post"),
                },
                indent=2,
            )
        )
    finally:
        source.stop()
        for client in (ui, gateway_client, publisher):
            client.loop_stop()
            client.disconnect()
        broker.stop()
