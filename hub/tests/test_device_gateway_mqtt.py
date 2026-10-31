"""Synthetic end-to-end command/result over a temporary loopback-only broker."""
import asyncio
import json
import time

import pytest

pytest.importorskip('amqtt')
mqtt = pytest.importorskip('paho.mqtt.client')

from test_mqtt_reconnect import BrokerThread, _free_port, _wait
from deskmate_hub.control import ControlDispatcher
from deskmate_hub.control.device_adapters import MockDeviceAdapter
from deskmate_hub.control.device_gateway import CMD_TOPIC, RESULT_TOPIC, DeviceGateway
from deskmate_hub.control.devices import Capability, Device, DeviceRegistry
from deskmate_hub.inference import GateMode


def test_dispatcher_gateway_mock_roundtrip_over_local_mqtt():
    port = _free_port(); broker = BrokerThread(port)
    registry = DeviceRegistry()
    target = Device('mock_vent', 'mock', ('outside_air',),
        {'set_power': Capability('set_power', ('on', 'off'), verified=True)},
        online=True, state={'set_power': 'off'}, observed_at=time.time())
    registry.register(target)
    adapter = MockDeviceAdapter({target.target_id: target.state})
    gateway_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='test-device-gateway')
    hub_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='test-dispatcher')
    received = []
    subscriptions = set()
    dispatcher = ControlDispatcher({'result_timeout_sec': 15, 'actions': {'environment': [
        {'target_id': target.target_id, 'operation': 'set_power', 'value': 'on', 'undo_value': 'off'}]}},
        publish_cmd=lambda command: hub_client.publish(CMD_TOPIC, json.dumps({'schema_version': '1.0',
                         'node': 'hub', 'data': command}), qos=1, retain=False))
    gateway = DeviceGateway(registry, {'mock': adapter}, enabled=True,
        publish_result=lambda topic, envelope: gateway_client.publish(topic, json.dumps(envelope), qos=1, retain=False))
    gateway.attach(gateway_client)
    gateway_client.on_subscribe = lambda *_: subscriptions.add('gateway')
    hub_client.on_subscribe = lambda *_: subscriptions.add('hub')
    hub_client.on_connect = lambda client, *_: client.subscribe(RESULT_TOPIC, qos=1)
    def on_result(client, userdata, message):
        envelope = json.loads(message.payload)
        received.append(envelope)
        dispatcher.on_result(envelope['data'])
    hub_client.on_message = on_result
    broker.start()
    try:
        for client in (gateway_client, hub_client):
            client.connect('127.0.0.1', port, keepalive=10); client.loop_start()
        assert _wait(lambda: gateway_client.is_connected() and hub_client.is_connected()
                           and subscriptions == {'gateway', 'hub'})
        # The test loop keeps the service's pump alive; source is entirely Mock.
        dispatcher.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST)
        assert adapter.calls == [] and dispatcher.episode.awaiting_user
        dispatcher.on_feedback('accept')
        deadline = time.time() + 10
        while time.time() < deadline and not any(e['data']['status'] == 'succeeded' for e in received):
            asyncio.run(gateway.pump())
            time.sleep(0.05)
        assert [e['data']['status'] for e in received] == ['executing', 'succeeded']
        assert received[-1]['node'] == 'device-gateway-mock'
        assert received[-1]['data']['actual_value'] == 'on'
        assert dispatcher.action_done() and len(adapter.calls) == 1
        assert gateway.evidence[dispatcher.episode.commands[0].command_id]['state_confirmed']
    finally:
        for client in (gateway_client, hub_client):
            client.loop_stop(); client.disconnect()
        broker.stop()
