"""Independent Mock process, SQLite and real loopback MQTT; no LAN/hardware."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import sys
import time

import pytest
pytest.importorskip('amqtt')
mqtt = pytest.importorskip('paho.mqtt.client')

from test_mqtt_reconnect import BrokerThread, _free_port, _wait
from deskmate_hub.control import ControlDispatcher
from deskmate_hub.control.command_journal import CommandJournal
from deskmate_hub.control.device_gateway import CMD_TOPIC, RESULT_TOPIC
from deskmate_hub.inference import GateMode

ROOT = Path(__file__).resolve().parents[2]


class Process:
    def __init__(self, tmp_path, port):
        self.path = tmp_path / 'private' / 'commands.db'
        self.log_path = tmp_path / 'process.log'
        self.port = port
        self.process = None

    def start(self, ready=True):
        self.log = self.log_path.open('w')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT / 'hub'))
        self.process = subprocess.Popen([sys.executable, '-u', '-m', 'deskmate_hub.control.gateway_service',
            '--mock', '--enable', '--broker-port', str(self.port), '--journal', str(self.path)],
            cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        if ready:
            assert _wait(lambda: 'mock_gateway_subscribed' in self.log_path.read_text(), 10), self.log_path.read_text()
        return self

    def stop(self, sig=signal.SIGTERM):
        if self.process and self.process.poll() is None:
            self.process.send_signal(sig)
            self.process.wait(timeout=8)
        self.log.close()
        if sig != signal.SIGKILL:
            assert self.process.returncode == 0, self.log_path.read_text()

    def records(self):
        if not self.path.exists(): return {}
        with sqlite3.connect(self.path) as db:
            return {cid: (json.loads(command), json.loads(record)) for cid, command, record in
                    db.execute('SELECT id, command, record FROM commands')}


class Observer:
    def __init__(self, port):
        self.received = []
        self.subscribed = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'gateway-test-{time.time_ns()}')
        self.client.on_connect = lambda client, *_: client.subscribe(RESULT_TOPIC, qos=1)
        self.client.on_subscribe = lambda *_: setattr(self, 'subscribed', True)
        self.client.on_message = lambda client, data, message: self.received.append(json.loads(message.payload))
        self.client.reconnect_delay_set(1, 2)
        self.client.connect('127.0.0.1', port, keepalive=5)
        self.client.loop_start()
        assert _wait(lambda: self.subscribed)

    def send(self, command, retain=False):
        envelope = {'schema_version': '1.0', 'node': 'hub', 'ts': time.time(), 'data': command}
        self.client.publish(CMD_TOPIC, json.dumps(envelope), qos=1, retain=retain).wait_for_publish(3)

    def result(self, cid, status):
        return next((item for item in reversed(self.received)
                     if item['data']['command_id'] == cid and item['data']['status'] == status), None)

    def close(self):
        self.client.disconnect(); self.client.loop_stop()


def command(cid, **changes):
    return dict(dict(command_id=cid, target_id='mock_lamp', operation='set_brightness', value=70,
                gate='suggest', requires_confirmation=False, origin_state='ACTION_ENV',
                cause='environment', expires_ts_ms=int((time.time() + 15) * 1000), execution_mode='mock'), **changes)


@pytest.fixture
def network(tmp_path):
    port = _free_port(); broker = BrokerThread(port); broker.start()
    observer = Observer(port)
    process = Process(tmp_path, port)
    try:
        yield broker, observer, process
    finally:
        if process.process:
            process.stop()
        observer.close(); broker.stop()


def test_process_command_once_duplicate_rejection_shutdown_and_restart(network):
    broker, observer, process = network
    process.start()
    c = command('once')
    observer.send(c)
    assert _wait(lambda: observer.result('once', 'succeeded'))
    result = observer.result('once', 'succeeded')
    assert result['node'] == 'device-gateway-mock' and result['data']['actual_value'] == 70
    original = process.records()['once']
    observer.send(c)
    observer.send(dict(c, value=20))  # same command_id, altered payload must not run
    time.sleep(.2)
    assert process.records()['once'] == original
    process.stop(signal.SIGINT)
    assert stat.S_IMODE(process.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(process.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(str(process.path) + '.lock').stat().st_mode) == 0o600
    observer.received.clear()
    process.start()
    assert _wait(lambda: observer.result('once', 'succeeded'))  # journal result, no new action
    observer.send(c)
    time.sleep(.2)
    assert process.records()['once'] == original
    # A fresh command still executes after recovery.
    observer.send(command('fresh', value=35))
    assert _wait(lambda: observer.result('fresh', 'succeeded'))


def test_retained_expired_auto_and_live_markers_cannot_execute(network):
    broker, observer, process = network
    # Broker-retained command delivered on subscription must be rejected.
    observer.send(command('retained'), retain=True)
    process.start()
    assert _wait(lambda: observer.result('retained', 'failed'))
    assert observer.result('retained', 'failed')['data']['error_code'] == 'retained_command_rejected'
    observer.client.publish(CMD_TOPIC, b'', qos=1, retain=True).wait_for_publish(3)
    observer.send(command('expired', expires_ts_ms=int((time.time() - 1) * 1000)))
    observer.send(command('auto', gate='auto'))
    observer.send(command('live', execution_mode='live'))
    assert _wait(lambda: observer.result('expired', 'failed') and observer.result('auto', 'failed'))
    assert observer.result('expired', 'failed')['data']['error_code'] == 'command_expired'
    assert observer.result('auto', 'failed')['data']['error_code'] == 'automatic_control_disabled'
    assert 'live' not in process.records()
    assert all(not record['evidence']['write_attempted'] for _, record in process.records().values())


def test_broker_reconnect_subscribes_again_without_resending_device_command(network):
    broker, observer, process = network
    process.start()
    observer.send(command('before'))
    assert _wait(lambda: observer.result('before', 'succeeded'))
    original = process.records()['before']
    broker.stop()
    assert _wait(lambda: 'mock_mqtt_disconnected' in process.log_path.read_text())
    assert process.process.poll() is None
    broker.start()
    assert _wait(lambda: process.log_path.read_text().count('mock_gateway_subscribed') >= 2, 15)
    assert _wait(lambda: observer.client.is_connected())
    observer.send(command('after', value=30))
    assert _wait(lambda: observer.result('after', 'succeeded'))
    assert process.records()['before'] == original


def test_unfinished_journal_recovers_read_only_without_replaying_write(network):
    broker, observer, process = network
    process.path.parent.mkdir(mode=0o700)
    c = command('unfinished', expires_ts_ms=int((time.time() + 2) * 1000))
    journal = CommandJournal(process.path)
    journal.claim(c, {'mode': 'mock', 'write_attempted': False, 'api_accepted': False, 'state_confirmed': False})
    journal.close()
    process.start()
    assert _wait(lambda: observer.result('unfinished', 'timeout'), 5)
    _, record = process.records()['unfinished']
    assert record['evidence']['recovered'] and not record['evidence']['write_attempted']
    assert not record['evidence']['state_confirmed']


def test_gateway_exit_causes_dispatcher_timeout_without_command_republish(network):
    broker, observer, process = network
    process.start(); process.stop()
    sent = []
    dispatcher = ControlDispatcher({'result_timeout_sec': .3, 'actions': {'environment': [
        {'target_id': 'mock_lamp', 'operation': 'set_brightness', 'value': 70, 'undo_value': 0}]}},
        publish_cmd=lambda c: (sent.append(c), observer.send(dict(c, execution_mode='mock'))))
    dispatcher.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST)
    dispatcher.on_feedback('accept')
    assert len(sent) == 1
    assert _wait(dispatcher.action_done, 2)
    assert dispatcher.episode.commands[0].status == 'timeout'
    assert len(sent) == 1 and not observer.received


def test_crash_releases_sqlite_lock_and_completed_command_is_not_reexecuted(network):
    broker, observer, process = network
    process.start()
    c = command('crash-safe')
    observer.send(c)
    assert _wait(lambda: observer.result('crash-safe', 'succeeded'))
    original = process.records()['crash-safe']
    process.stop(signal.SIGKILL)
    observer.received.clear()
    process.start()
    assert _wait(lambda: observer.result('crash-safe', 'succeeded'))
    observer.send(c)
    time.sleep(.15)
    assert process.records()['crash-safe'] == original


def test_second_process_cannot_share_journal(network, tmp_path):
    broker, observer, process = network
    process.start()
    duplicate_dir = tmp_path / 'duplicate'
    duplicate_dir.mkdir()
    duplicate = Process(duplicate_dir, process.port)
    duplicate.path = process.path
    duplicate.start(ready=False)
    try:
        assert _wait(lambda: duplicate.process.poll() is not None, 5)
        assert duplicate.process.returncode == 1
        assert 'mock_gateway_failed' in duplicate.log_path.read_text()
        assert process.process.poll() is None
    finally:
        if duplicate.process.poll() is None:
            duplicate.process.kill(); duplicate.process.wait(5)
        duplicate.log.close()


def test_broker_loss_causes_hub_timeout_and_delayed_expired_command_cannot_run(network):
    broker, observer, process = network
    process.start()
    broker.stop()
    assert _wait(lambda: not observer.client.is_connected())
    sent = []
    dispatcher = ControlDispatcher({'result_timeout_sec': .3, 'actions': {'environment': [
        {'target_id': 'mock_lamp', 'operation': 'set_brightness', 'value': 70, 'undo_value': 0}]}},
        publish_cmd=lambda c: (sent.append(c), observer.client.publish(CMD_TOPIC,
            json.dumps({'schema_version': '1.0', 'node': 'hub', 'data': dict(c, execution_mode='mock')}), qos=1)))
    dispatcher.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST)
    dispatcher.on_feedback('accept')
    assert _wait(dispatcher.action_done, 2)
    assert dispatcher.episode.commands[0].status == 'timeout' and len(sent) == 1
    broker.start()
    assert _wait(lambda: process.log_path.read_text().count('mock_gateway_subscribed') >= 2, 15)
    assert _wait(lambda: observer.client.is_connected())
    # Re-deliver the exact old ID, as QoS1 transport may do; it is now expired.
    observer.send(dict(sent[0], execution_mode='mock'))
    cid = sent[0]['command_id']
    assert _wait(lambda: observer.result(cid, 'failed'))
    assert observer.result(cid, 'failed')['data']['error_code'] == 'command_expired'
    assert not process.records()[cid][1]['evidence']['write_attempted']
    assert not dispatcher.on_result(observer.result(cid, 'failed')['data'])
    assert dispatcher.episode.commands[0].status == 'timeout' and len(sent) == 1


def test_real_hub_fsm_plan_acceptance_reaches_independent_mock_gateway(tmp_path):
    from test_control_platform_e2e import Simulation
    from deskmate_hub.inference import State
    hub_path = tmp_path / 'hub'; hub_path.mkdir()
    simulation = Simulation(hub_path, gateway_running=False)
    process = Process(tmp_path, simulation.port)
    try:
        # Accelerated synthetic Hub clock, anchored at wall time so process
        # command deadlines are valid. No production clock/timer is changed.
        simulation.now = time.time()
        simulation.adapter.states['mock_lamp']['set_brightness'] = 0
        simulation.registry.devices['mock_lamp'].state['set_brightness'] = 0
        process.start()
        simulation.start_focus()
        state, request_id = simulation.proposal(simulation.low_light)
        plan = state['data']['sensor_summary']['control']['commands'][0]
        assert not simulation.commands
        assert simulation.hub.pending_request['data']['request_id'] == request_id
        simulation.respond('accept', simulation.low_light)
        assert _wait(lambda: any(item.get('command_id') == plan['command_id'] and item.get('status') == 'succeeded'
                                for item in list(simulation.cache.control_results)))
        state = simulation.step(simulation.low_light)
        assert simulation.hub.engine.state is State.MONITOR
        command_sent = simulation.commands[0]
        assert len(simulation.commands) == 1
        assert (command_sent['command_id'], command_sent['target_id'], command_sent['operation'], command_sent['value']) == (
            plan['command_id'], plan['target'], plan['op'], plan['value'])
        command_record, record = process.records()[plan['command_id']]
        assert command_record == command_sent  # same deadlines/IDs, no transport rewrite
        assert record['evidence']['write_attempted'] and record['evidence']['state_confirmed']
        result = state['data']['sensor_summary']['control']['last_episode']['commands'][0]
        assert result['status'] == 'succeeded' and result['state_confirmed']
        assert state['data']['sensor_summary']['control']['execution_mode'] == 'mock'
    finally:
        if process.process:
            process.stop()
        simulation.close()


@pytest.mark.parametrize('failure', ['authentication', 'subscription'])
def test_broker_access_refusal_exits_and_releases_journal(tmp_path, failure):
    import socket
    import threading
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1)
    listener.settimeout(5)
    errors = []
    def read_packet(conn):
        first = conn.recv(1)
        if not first: return b''
        length, shift = 0, 0
        while True:
            value = conn.recv(1)[0]
            length += (value & 127) << shift
            if not value & 128: break
            shift += 7
        data = b''
        while len(data) < length:
            data += conn.recv(length - len(data))
        return data
    def reject():
        try:
            with listener.accept()[0] as conn:
                conn.settimeout(5)
                read_packet(conn)
                if failure == 'authentication':
                    conn.sendall(b'\x20\x02\x00\x05')  # CONNACK not authorised
                else:
                    conn.sendall(b'\x20\x02\x00\x00')
                    subscribe = read_packet(conn)
                    conn.sendall(b'\x90\x03' + subscribe[:2] + b'\x80')
                read_packet(conn)  # client disconnect/EOF
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            listener.close()
    thread = threading.Thread(target=reject, daemon=True); thread.start()
    process = Process(tmp_path, listener.getsockname()[1]).start(ready=False)
    try:
        process.process.wait(8)
        assert process.process.returncode == 1
        log = process.log_path.read_text()
        assert ('mock_mqtt_connection_rejected' if failure == 'authentication' else
                'mock_gateway_subscription_rejected') in log
        assert 'mock_gateway_stopped' in log and 'Traceback' not in log
        journal = CommandJournal(process.path); journal.close()
    finally:
        if process.process.poll() is None:
            process.process.kill(); process.process.wait(5)
        process.log.close(); thread.join(6)
    assert not thread.is_alive() and not errors


def test_initial_broker_unavailable_retries_until_started(tmp_path):
    port = _free_port()
    process = Process(tmp_path, port).start(ready=False)
    broker = BrokerThread(port)
    try:
        assert _wait(lambda: 'mock_mqtt_connect_failed_retrying' in process.log_path.read_text(), 5)
        assert process.process.poll() is None
        broker.start()
        assert _wait(lambda: 'mock_gateway_subscribed' in process.log_path.read_text(), 10)
    finally:
        process.stop(); broker.stop()
