"""Synthetic sensors -> REAL Hub/FSM -> local MQTT -> Mock gateway -> Hub.

No Pi5 app is run. MockDisplay emits the existing request_id/verdict contract.
Only fixture weights select environment evidence deterministically; production
FSM code, gate thresholds and timer config remain unchanged.
"""
import asyncio
import copy
import io
import json
import time

import pytest

pytest.importorskip('amqtt')
mqtt = pytest.importorskip('paho.mqtt.client')

from test_mqtt_reconnect import BrokerThread, _free_port, _wait
from test_control import mm
from deskmate_hub.control import load_control_config
from deskmate_hub.control.device_adapters import MockDeviceAdapter
from deskmate_hub.control.device_gateway import CMD_TOPIC, RESULT_TOPIC, DeviceGateway
from deskmate_hub.control.devices import Capability, Device, DeviceRegistry
from deskmate_hub.control.environment_policy import MockControlTransport, load_environment_control_config
from deskmate_hub.inference import GateMode, State, load_config
from deskmate_hub.ingest import Sample, SensorCache, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.live import LiveHub

FEEDBACK = 'deskmate/feedback/user'
REQUEST = 'deskmate/interaction/request'
STATE = 'deskmate/state/phase'


class Simulation:
    def __init__(self, tmp_path, *, gateway_running=True):
        self.now = 1000.0
        self.cache = SensorCache()
        self.registry = DeviceRegistry()
        self.gateway_registry = DeviceRegistry()
        self.adapter = MockDeviceAdapter({'mock_lamp': {'set_brightness': 20}, 'mock_ac': {'set_power': 'off'}},
                                         clock=lambda: self.now)
        self._register('mock_lamp', 'lighting', Capability('set_brightness', minimum=0, maximum=255, verified=True))
        self._register('mock_ac', 'cooling', Capability('set_power', ('on', 'off'), verified=True))
        self.port = _free_port(); self.broker = BrokerThread(self.port)
        self.hub_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='mock-hub-simulation')
        self.gw_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='mock-gateway-simulation')
        self.commands, self.requests, self.states, self.results, self.wire_states = [], [], [], [], []
        self.subscribed = set()
        self.gateway_running = gateway_running
        self.gateway = DeviceGateway(self.gateway_registry, {'mock': self.adapter}, enabled=True,
            publish_result=self._publish_result, clock=lambda: self.now, journal_path=tmp_path/'commands.db')
        self.gateway.attach(self.gw_client)
        self.gw_client.on_subscribe = lambda *_: self.subscribed.add('gateway')
        self.hub_client.on_connect = lambda client, *_: client.subscribe([(RESULT_TOPIC, 1), (FEEDBACK, 1), (REQUEST, 1), (STATE, 1)])
        self.hub_client.on_subscribe = lambda *_: self.subscribed.add('hub')
        def receive(client, userdata, message):
            if message.topic in (RESULT_TOPIC, FEEDBACK):
                route_mqtt_message(self.cache, message.topic, message.payload, self.now)
            elif message.topic == REQUEST:
                self.requests.append(json.loads(message.payload))
            elif message.topic == STATE:
                self.wire_states.append(json.loads(message.payload))
        self.hub_client.on_message = receive
        fcfg = copy.deepcopy(load_config())
        fcfg['weights']['fatigue']['npc'] = {'posture': .30, 'environment': .70, 'elapsed': 0}
        fcfg['weights']['focus']['npc'] = {'posture': .50, 'environment': .50, 'elapsed': 0}
        icfg = copy.deepcopy(load_ingest_config()); icfg['normalization'] = 'linear'
        pcfg = load_environment_control_config(); pcfg['enabled'] = True
        pcfg['rules'] = {name: rule for name, rule in pcfg['rules'].items()
                         if name in ('co2_high', 'temperature_high', 'lux_low')}
        ccfg = load_control_config(); ccfg['esm_log_path'] = str(tmp_path/'esm.jsonl')
        self.hub = LiveHub(self.cache, fsm_cfg=fcfg, ingest_cfg=icfg, control_cfg=ccfg,
            environment_control_cfg=pcfg, device_registry=self.registry,
            environment_mock_transport=MockControlTransport(self._publish_command),
            publish=self._publish_state, publish_request=self._publish_request, out=io.StringIO())
        self.broker.start()
        for client in ((self.hub_client, self.gw_client) if gateway_running else (self.hub_client,)):
            client.connect('127.0.0.1', self.port, keepalive=10); client.loop_start()
        required = {'hub', 'gateway'} if gateway_running else {'hub'}
        assert _wait(lambda: required.issubset(self.subscribed))
        self.comfort = {'co2_ppm': 700, 'temp_c': 25, 'humidity_pct': 45, 'lux': 500}
        self.low_light = dict(self.comfort, lux=50)
        self.co2 = dict(self.comfort, co2_ppm=2000)
        # High temperature is a focus (phi) input in current FSM. CO2 supplies
        # the environmental fatigue evidence; policy then selects cooling.
        self.hot = dict(self.co2, temp_c=32)

    def _register(self, target, role, capability):
        for registry in (self.registry, self.gateway_registry):
            registry.register(Device(target, 'mock', (role,), {capability.operation: capability}, online=True,
                                     state=dict(self.adapter.states[target]), observed_at=self.now))

    def _publish_command(self, command):
        self.commands.append(copy.deepcopy(command))
        self.hub_client.publish(CMD_TOPIC, json.dumps({'schema_version': '1.0', 'node': 'hub', 'data': command}), qos=1)

    def _publish_request(self, envelope):
        self.hub_client.publish(REQUEST, json.dumps(envelope), qos=1)

    def _publish_state(self, envelope):
        self.states.append(copy.deepcopy(envelope))
        self.hub_client.publish(STATE, json.dumps(envelope), qos=1)

    def _publish_result(self, topic, envelope):
        self.results.append(copy.deepcopy(envelope))
        self.gw_client.publish(topic, json.dumps(envelope), qos=1)

    def step(self, environment):
        self.now += self.hub.period
        self.cache.put('mmwave', mm(self.now, 40))
        self.cache.put('env', Sample(self.now, self.now, None, {**environment,
            'co2_valid': True, 'temp_valid': True, 'humidity_valid': True, 'lux_valid': True}))
        # Synthetic device polling provides fresh state to the Hub planner.
        for target, values in self.adapter.states.items():
            if target not in self.registry.devices:
                continue
            self.registry.observe(target, values, self.now, expected=values)
        return self.hub.tick_once(self.now)

    def start_focus(self):
        for _ in range(50):
            self.step(self.comfort)
            if self.hub.engine.state is State.FOCUS_NPC:
                return
        raise AssertionError('real FSM did not reach FOCUS_NPC')

    def proposal(self, environment):
        for _ in range(150):
            state = self.step(environment)
            if self.hub.engine.state is State.ACTION_ENV:
                assert self.hub.control.episode.gate is GateMode.SUGGEST
                rid = self.hub.pending_request['data']['request_id']
                assert _wait(lambda: any(e['data']['request_id'] == rid for e in self.requests))
                return state, rid
        raise AssertionError('real FSM did not route to ACTION_ENV')

    def respond(self, verdict, environment):
        rid = self.hub.pending_request['data']['request_id']
        before = len(self.commands)
        self.hub_client.publish(FEEDBACK, json.dumps({'schema_version': '1.0', 'node': 'mock-display',
            'data': {'request_id': rid, 'verdict': verdict, 'response_ms': 20}}), qos=1).wait_for_publish(2)
        assert _wait(lambda: bool(self.cache.snapshot().feedback))
        state = self.step(environment)
        if len(self.commands) > before and self.gateway_running:
            assert _wait(lambda: not self.gateway._queue.empty())
            asyncio.run(self.gateway.pump())
            cid = self.commands[-1]['command_id']
            assert _wait(lambda: any(item.get('command_id') == cid and item.get('status') in
                ('succeeded', 'failed', 'timeout', 'cancelled') for item in list(self.cache.control_results)))
            state = self.step(environment)
        return state

    def recover(self):
        for _ in range(15):
            self.step(self.comfort)
            if self.hub.engine.state is State.FOCUS_NPC:
                self.step(self.comfort)
                return
        raise AssertionError('real FSM did not recover to focus')

    def close(self):
        for client in (self.hub_client, self.gw_client):
            client.loop_stop(); client.disconnect()
        self.gateway.close(); self.broker.stop()


def test_continuous_real_fsm_policy_mqtt_mock_scenario(tmp_path):
    s = Simulation(tmp_path)
    try:
        s.start_focus()
        state, _ = s.proposal(s.low_light)
        plan = state['data']['sensor_summary']['control']['commands']
        assert [(c['target'], c['op'], c['value']) for c in plan] == [('mock_lamp', 'set_brightness', 70)]
        assert not s.commands
        done = s.respond('accept', s.low_light)
        assert done['data']['fsm_state'] == 'MONITOR'
        assert len(s.adapter.calls) == 1 and len(s.commands) == 1
        assert s.commands[0]['command_id'] == plan[0]['command_id']
        assert done['data']['sensor_summary']['control']['last_episode']['commands'][0]['status'] == 'succeeded'
        s.recover()

        _, _ = s.proposal(s.co2)
        assert not s.hub.control.episode.commands and s.hub.control.episode.advice[0]['kind'] == 'manual_ventilation'
        for _ in range(2):
            s.step(s.co2)
            assert s.hub.engine.state is State.ACTION_ENV  # Must wait for feedback even without a vent.
        s.respond('accept', s.co2)
        assert s.hub.engine.state is State.MONITOR and len(s.adapter.calls) == 1
        assert s.hub.control.history[-1].outcome == 'advised'
        s.recover()

        state, _ = s.proposal(s.hot)
        assert [c.target_id for c in s.hub.control.episode.commands] == ['mock_ac']
        s.respond('reject', s.hot)
        assert s.hub.engine.state is State.MONITOR and len(s.adapter.calls) == 1
        assert s.hub.control.history[-1].outcome == 'rejected'
        s.recover()

        s.proposal(s.hot)
        s.adapter.error = 'network_error'
        state = s.respond('accept', s.hot)
        assert s.hub.engine.state is State.MONITOR
        result = state['data']['sensor_summary']['control']['last_episode']['commands'][0]
        assert result['target'] == 'mock_ac' and result['status'] == 'failed' and result['error_code'] == 'network_error'
        assert len(s.adapter.calls) == 1
        s.adapter.error = None; s.recover()
        # Let the already-issued failed command's normal policy cooldown elapse.
        for _ in range(65): s.step(s.comfort)
        state, _ = s.proposal(s.hot)
        planned = state['data']['sensor_summary']['control']['commands'][0]
        state = s.respond('accept', s.hot)
        assert s.hub.engine.state is State.MONITOR
        assert s.adapter.calls[-1] == ('mock_ac', 'set_power', 'on') and len(s.adapter.calls) == 2
        assert s.commands[-1]['command_id'] == planned['command_id']
        assert state['data']['sensor_summary']['control']['execution_mode'] == 'mock'
        assert all(e['data']['sensor_summary']['control']['execution_mode'] == 'mock' for e in s.states)
    finally: s.close()


def test_gateway_absent_hub_keeps_running_and_times_out_to_monitor(tmp_path):
    s = Simulation(tmp_path, gateway_running=False)
    try:
        s.start_focus(); s.proposal(s.low_light)
        s.respond('accept', s.low_light)
        for _ in range(4):
            state = s.step(s.low_light)
            if s.hub.engine.state is State.MONITOR: break
        assert s.hub.engine.state is State.MONITOR and not s.adapter.calls
        assert state['data']['sensor_summary']['control']['last_episode']['commands'][0]['status'] == 'timeout'
        assert s.commands and s.states  # Hub didn't depend on Gateway availability.
    finally: s.close()


def test_all_devices_unregistered_keep_advice_waiting_and_reject_is_safe(tmp_path):
    s = Simulation(tmp_path)
    try:
        s.registry.devices.clear()
        s.start_focus(); s.proposal(s.low_light)
        assert s.hub.control.episode.awaiting_user and not s.hub.control.episode.commands
        assert s.hub.control.episode.advice[0]['reason'] == 'no_eligible_device'
        s.respond('reject', s.low_light)
        assert s.hub.engine.state is State.MONITOR and not s.commands and not s.adapter.calls
    finally: s.close()


def test_new_policy_rejects_legacy_or_stale_card_acceptance(tmp_path):
    s = Simulation(tmp_path)
    try:
        s.start_focus(); s.proposal(s.low_light)
        for rid in (None, 'atlas-display', 'stale-card'):
            s.cache.put_feedback({'request_id': rid, 'verdict': 'accept'})
            s.step(s.low_light)
            assert s.hub.control.episode.awaiting_user and not s.commands
        s.respond('accept', s.low_light)
        assert s.hub.engine.state is State.MONITOR and len(s.adapter.calls) == 1
    finally: s.close()


def test_policy_veto_does_not_publish_a_card_for_an_unready_plan(tmp_path):
    s = Simulation(tmp_path)
    try:
        s.hub.environment_policy.rules['lux_low']['minimum_duration_sec'] = 99999
        s.start_focus()
        for _ in range(150):
            state = s.step(s.low_light)
            if s.hub.engine.state is State.ACTION_ENV: break
        assert s.hub.engine.state is State.ACTION_ENV and state['data']['gate'] == 'suggest'
        assert not s.hub.pending_request and not s.hub.control.episode.awaiting_user
        assert s.hub.control.episode.outcome == 'skipped' and not s.commands
        s.step(s.low_light)
        assert s.hub.engine.state is State.MONITOR and not s.adapter.calls
    finally: s.close()
