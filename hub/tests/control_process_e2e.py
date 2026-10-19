"""Local-only process E2E: real Hub/FSM, gateway entrypoint and MQTT.

The sensor/device inputs and prewarm clock are synthetic. Optional Flutter uses
real MqttStateSource and widgets in a separate test process, not a Pi5 device.
No production config, clock, LAN listener, broker or service is changed.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
from pathlib import Path
import runpy
import selectors
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from test_mqtt_reconnect import BrokerThread, _free_port, _wait

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
CONTAINER = 'deskmate-atlas-atlas-dev-1'


def write_snapshot(path: Path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, allow_nan=False))
    temporary.replace(path)


def fixture_hub(directory, publish, publish_request, publish_cmd):
    from deskmate_hub.control import load_control_config
    from deskmate_hub.control.environment_policy import MockControlTransport, load_environment_control_config
    from deskmate_hub.control.gateway_service import DEFAULT_CONFIG, Settings, build_mock_devices
    from deskmate_hub.inference import load_config
    from deskmate_hub.ingest import SensorCache, load_ingest_config
    from deskmate_hub.live import LiveHub
    import yaml
    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text())
    registry, fixture_states = build_mock_devices(Settings('127.0.0.1', 1, directory / 'unused.db', cfg['devices']))
    fsm = copy.deepcopy(load_config())
    # Reuse Simulation's evidence fixture, not new production scoring/timers.
    fsm['weights']['fatigue']['npc'] = {'posture': .30, 'environment': .70, 'elapsed': 0}
    fsm['weights']['focus']['npc'] = {'posture': .50, 'environment': .50, 'elapsed': 0}
    ingest = copy.deepcopy(load_ingest_config()); ingest['normalization'] = 'linear'
    policy = load_environment_control_config(); policy['enabled'] = True
    policy['rules'] = {key: value for key, value in policy['rules'].items() if key == 'lux_low'}
    control = load_control_config(); control['esm_log_path'] = str(directory / 'mock-esm.jsonl')
    hub = LiveHub(SensorCache(), fsm_cfg=fsm, ingest_cfg=ingest, control_cfg=control,
        environment_control_cfg=policy, device_registry=registry,
        environment_mock_transport=MockControlTransport(publish_cmd),
        publish=publish, publish_request=publish_request, out=io.StringIO())
    return hub, registry, fixture_states


def sensor_tick(hub, registry, states, now, *, low_light):
    from test_control import mm
    from deskmate_hub.ingest import Sample
    env = {'co2_ppm': 700, 'temp_c': 25, 'humidity_pct': 45, 'lux': 50 if low_light else 500,
           'co2_valid': True, 'temp_valid': True, 'humidity_valid': True, 'lux_valid': True}
    hub.cache.put('mmwave', mm(now, 40))
    hub.cache.put('env', Sample(now, now, None, env))
    for target, values in states.states.items():
        registry.observe(target, values, now, expected=values)
    return hub.tick_once(now)


def prewarm(hub, registry, states, start):
    from deskmate_hub.inference import State
    now = start
    for low_light, goal, limit in ((False, State.FOCUS_NPC, 50), (True, State.ACTION_ENV, 150)):
        for _ in range(limit):
            now += hub.period
            result = sensor_tick(hub, registry, states, now, low_light=low_light)
            if hub.engine.state is goal:
                break
        else:
            raise AssertionError('synthetic FSM fixture did not reach requested stage')
    assert hub.pending_request and hub.control.episode.awaiting_user
    return now, result


def hub_child(port: int, snapshot: Path):
    from deskmate_hub.ingest.mqtt_source import MqttSource
    directory = snapshot.parent
    dry, registry, states = fixture_hub(directory, lambda _: None, lambda _: None, lambda _: None)
    finished, _ = prewarm(dry, registry, states, 1000)
    # Replay the same prewarm trace in the past; subsequent ticks use real wall
    # time. No control deadline, request ID or MQTT field is rewritten.
    duration = finished - 1000
    commands, requests = [], []
    stage_ready = False
    source = None
    def state_publish(envelope):
        if stage_ready:
            source.publish_state(envelope)
    def request_publish(envelope):
        requests.append(copy.deepcopy(envelope))
        if stage_ready:
            source.publish_request(envelope)
    def command_publish(command):
        commands.append(copy.deepcopy(command))
        source.publish_control(command)
    hub, registry, states = fixture_hub(directory, state_publish, request_publish, command_publish)
    source = MqttSource(hub.cache, '127.0.0.1', port, client_id='mock-process-hub', on_log=lambda _: None)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    source.start()
    try:
        assert _wait(lambda: source.connected)
        _, current = prewarm(hub, registry, states, time.time() - duration - 1)
        stage_ready = True
        proposal = copy.deepcopy(hub.pending_request)
        completed_state = None
        while not stop.is_set():
            current = sensor_tick(hub, registry, states, time.time(), low_light=True)
            if hub.pending_request:
                source.publish_request(hub.pending_request)
            control = current['data']['sensor_summary']['control']
            episode = control.get('last_episode')
            if (completed_state is None and current['data']['fsm_state'] == 'MONITOR' and episode
                    and episode['commands'] and episode['commands'][0]['status'] == 'succeeded'):
                completed_state = copy.deepcopy(current)
            write_snapshot(snapshot, {'pid': os.getpid(), 'state': current, 'proposal': proposal,
                                      'completed_state': completed_state, 'commands': commands,
                                      'mode': 'mock', 'synthetic_sensors': True})
            stop.wait(.1)
    finally:
        source.stop()


def gateway_child(port: int, journal: Path, audit: Path):
    from deskmate_hub.control import device_adapters
    original = device_adapters.MockDeviceAdapter
    class AuditedMock(original):
        async def control(self, device, operation, value, *, expires_at=None):
            # Test-only observation of the unmodified Mock adapter. Never
            # fabricate a result, mutate a receipt or replace readback.
            calls = json.loads(audit.read_text()) if audit.exists() else []
            calls.append({'target': device.target_id, 'operation': operation, 'value': value})
            write_snapshot(audit, calls)
            return await super().control(device, operation, value, expires_at=expires_at)
    device_adapters.MockDeviceAdapter = AuditedMock
    sys.argv = ['gateway_service', '--mock', '--enable', '--broker-port', str(port), '--journal', str(journal)]
    runpy.run_module('deskmate_hub.control.gateway_service', run_name='__main__')


class Children:
    def __init__(self, directory):
        self.directory, self.children, self.logs = directory, [], []
    def start(self, name, *args):
        log = (self.directory / (name + '.log')).open('w')
        self.logs.append(log)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=f'{ROOT}:{ROOT / "hub"}:{ROOT / "hub/tests"}')
        child = subprocess.Popen([sys.executable, '-u', str(SELF), *map(str, args)], cwd=ROOT,
                                 env=env, stdout=log, stderr=subprocess.STDOUT)
        self.children.append(child)
        return child
    def close(self):
        for child in reversed(self.children):
            if child.poll() is None: child.terminate()
            try: child.wait(8)
            except subprocess.TimeoutExpired: child.kill(); child.wait(5)
        for log in self.logs: log.close()


class FlutterClient:
    def __init__(self, port, app, directory):
        from control_flutter_live_scenario import BRIDGE
        if not app.startswith('/tmp/'):
            raise ValueError('Flutter must run from a temporary copy')
        self.pipe = self.socket = self.process = self.log = self.thread = None
        self.errors = []
        try:
            self.pipe = subprocess.Popen(['docker', 'exec', '-i', CONTAINER, 'python3', '-u', '-c', BRIDGE],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            bridge_port = json.loads(self.pipe.stdout.readline())['port']
            self.socket = socket.create_connection(('127.0.0.1', port), timeout=5)
            self.socket.settimeout(None)
            def forward():
                selector = selectors.DefaultSelector()
                selector.register(self.socket, selectors.EVENT_READ)
                selector.register(self.pipe.stdout, selectors.EVENT_READ)
                try:
                    while True:
                        for key, _ in selector.select(1):
                            if key.fileobj is self.socket:
                                chunk = self.socket.recv(65536)
                                if not chunk: return
                                self.pipe.stdin.write(chunk); self.pipe.stdin.flush()
                            else:
                                chunk = os.read(self.pipe.stdout.fileno(), 65536)
                                if not chunk: return
                                self.socket.sendall(chunk)
                except (OSError, ValueError):
                    pass
                finally:
                    selector.close()
            self.thread = threading.Thread(target=forward, daemon=True); self.thread.start()
            self.log = (directory / 'flutter.log').open('w')
            self.process = subprocess.Popen(['docker', 'exec', '-w', app, CONTAINER, 'flutter', 'test',
                'test/control_live_mqtt.dart', '--reporter', 'expanded',
                f'--dart-define=DESKMATE_MOCK_BRIDGE_PORT={bridge_port}'], stdout=self.log, stderr=subprocess.STDOUT)
        except BaseException:
            self.close()
            raise
    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(8)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(5)
        if self.socket:
            try: self.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.socket.close()
        if self.pipe:
            self.pipe.terminate(); self.pipe.wait(5)
        if self.thread: self.thread.join(3)
        if self.log: self.log.close()


def run_scenario(directory: Path, flutter_app: str | None = None):
    import paho.mqtt.client as mqtt
    from deskmate_hub.control.device_gateway import CMD_TOPIC, RESULT_TOPIC
    port = _free_port(); broker = BrokerThread(port)
    children, frontend = Children(directory), None
    received, subscribed = [], threading.Event()
    observer = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='mock-process-observer')
    observer.on_connect = lambda client, *_: client.subscribe('deskmate/#', qos=1)
    observer.on_subscribe = lambda *_: subscribed.set()
    observer.on_message = lambda client, userdata, message: received.append((message.topic, json.loads(message.payload)))
    journal, audit, snapshot = directory / 'private' / 'commands.db', directory / 'audit.json', directory / 'hub.json'
    broker.start()
    try:
        observer.connect('127.0.0.1', port, keepalive=10); observer.loop_start()
        assert subscribed.wait(5)
        gateway = children.start('gateway', 'gateway', '--port', port, '--journal', journal, '--audit', audit)
        assert _wait(lambda: 'mock_gateway_subscribed' in (directory / 'gateway.log').read_text(), 10)
        hub = children.start('hub', 'hub', '--port', port, '--snapshot', snapshot)
        assert _wait(snapshot.exists, 10), (directory / 'hub.log').read_text()
        initial = json.loads(snapshot.read_text())
        proposal = initial['proposal']; planned = proposal['data']['control_plan']['commands'][0]
        if flutter_app:
            frontend = FlutterClient(port, flutter_app, directory)
            assert _wait(lambda: frontend.process.poll() is not None, 60), 'Flutter process timed out'
            assert frontend.process.returncode == 0, (directory / 'flutter.log').read_text()
        else:
            # Explicit headless replacement for the Flutter interaction only.
            observer.publish('deskmate/feedback/user', json.dumps({'schema_version': '1.0', 'node': 'mock-display',
                'data': {'request_id': proposal['data']['request_id'], 'verdict': 'accept'}}), qos=1).wait_for_publish(3)
        def finished():
            return json.loads(snapshot.read_text()).get('completed_state') is not None
        assert _wait(finished, 10), (directory / 'hub.log').read_text()
        final = json.loads(snapshot.read_text())
        assert hub.pid != gateway.pid and hub.poll() is None and gateway.poll() is None
        cmds = [item['data'] for topic, item in received if topic == CMD_TOPIC]
        feedback = [item['data'] for topic, item in received if topic == 'deskmate/feedback/user']
        results = [item for topic, item in received if topic == RESULT_TOPIC]
        assert len(cmds) == 1 and len(final['commands']) == 1
        assert cmds[0] == final['commands'][0]
        assert (cmds[0]['command_id'], cmds[0]['target_id'], cmds[0]['operation'], cmds[0]['value']) == (
            planned['command_id'], planned['target'], planned['op'], planned['value'])
        assert len(feedback) == 1 and feedback[0]['request_id'] == proposal['data']['request_id']
        assert any(topic == 'deskmate/state/phase' and item['data']['fsm_state'] == 'MONITOR'
                   and item['data']['sensor_summary']['control'].get('last_episode')
                   and item['data']['sensor_summary']['control']['last_episode']['commands'][0]['command_id'] == planned['command_id']
                   and item['data']['sensor_summary']['control']['last_episode']['commands'][0]['status'] == 'succeeded'
                   for topic, item in received)
        calls = json.loads(audit.read_text())
        assert calls == [{'target': 'mock_lamp', 'operation': 'set_brightness', 'value': 70}]
        with sqlite3.connect(journal) as db:
            entries = db.execute('SELECT command, record FROM commands').fetchall()
        assert len(entries) == 1 and json.loads(entries[0][0]) == cmds[0]
        record = json.loads(entries[0][1])
        assert record['evidence']['api_accepted'] and record['evidence']['state_confirmed']
        assert record['result']['data']['status'] == 'succeeded'
        assert any(item['node'] == 'device-gateway-mock' and item['data']['actual_value'] == 70 for item in results)
        summary = {'result': 'passed', 'mode': 'mock', 'hardware': False, 'hub_pid': hub.pid, 'gateway_pid': gateway.pid,
            'flutter_process': frontend is not None, 'flutter_launcher_pid': frontend.process.pid if frontend else None,
            'feedback_request_matches': True, 'commands': len(cmds), 'adapter_calls': len(calls),
            'state_confirmed': True, 'fsm_state': final['completed_state']['data']['fsm_state'],
            'current_fsm_state': final['state']['data']['fsm_state'],
            'target': planned['target'], 'value': planned['value'],
            'synthetic_sensors': True, 'test_only_adapter_audit': True}
        write_snapshot(directory / 'result.json', summary)
        return summary
    finally:
        if frontend: frontend.close()
        children.close()
        observer.disconnect(); observer.loop_stop(); broker.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('run', 'hub', 'gateway'))
    parser.add_argument('--port', type=int)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--flutter-app')
    args = parser.parse_args()
    if args.role == 'hub': hub_child(args.port, args.snapshot)
    elif args.role == 'gateway': gateway_child(args.port, args.journal, args.audit)
    else:
        with tempfile.TemporaryDirectory(prefix='deskmate-process-e2e-') as tmp:
            print(json.dumps(run_scenario(Path(tmp), args.flutter_app), indent=2))


if __name__ == '__main__':
    main()
