"""Opt-in Mock-only MQTT process for a full Linux Python host.

No ThinQ client, UART, Hub lifecycle or system service management. Command
execution, journal recovery and result contracts belong to DeviceGateway.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import stat
import threading
import uuid

from .device_adapters import MockDeviceAdapter
from .device_gateway import DeviceGateway, RESULT_TOPIC
from .devices import Capability, Device, DeviceRegistry, finite_number

LOG = logging.getLogger(__name__)
DEFAULT_CONFIG = Path(__file__).parent.parent / 'config' / 'mock_gateway.yaml'


class ConfigurationError(ValueError):
    """Message is a fixed safe code, never configuration input or credentials."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal message echoes unknown arguments/values.
        self.exit(2, 'mock_gateway_configuration_error\n')


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    journal: Path
    devices: list[dict]
    username_env: str | None = None
    password_env: str | None = None
    tls_ca: str | None = None
    poll_interval: float = 0.05


def load_settings(argv=None) -> Settings:
    parser = SafeParser(description='Explicit Mock device gateway; no live control.')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--mock', action='store_true', help='required Mock-only execution acknowledgement')
    parser.add_argument('--enable', action='store_true', help='override the disabled configuration')
    parser.add_argument('--broker-profile', choices=('localhost', 'pi4'))
    parser.add_argument('--broker-host')
    parser.add_argument('--broker-port', type=int)
    parser.add_argument('--journal', type=Path, help='absolute SQLite file in a private directory')
    args = parser.parse_args(argv)
    if not args.mock:
        raise ConfigurationError('explicit_mock_required')
    try:
        import yaml
        cfg = yaml.safe_load(args.config.read_text())
        if not isinstance(cfg, dict) or set(cfg) - {'enabled', 'mode', 'automatic_control', 'broker', 'journal', 'devices'}:
            raise ValueError()
        if (cfg.get('mode') != 'mock' or cfg.get('automatic_control') is not False
                or type(cfg.get('enabled')) is not bool or not (args.enable or cfg['enabled'])):
            raise ValueError()
        broker = cfg['broker']
        if not isinstance(broker, dict) or set(broker) - {'profile', 'host', 'port', 'username_env', 'password_env', 'tls_ca'}:
            raise ValueError()
        profile = args.broker_profile or broker.get('profile')
        host = args.broker_host or broker.get('host')
        port = args.broker_port if args.broker_port is not None else broker.get('port')
        journal = args.journal if args.journal is not None else Path(cfg.get('journal') or '')
        devices = cfg['devices']
        username, password, ca = (broker.get(key) for key in ('username_env', 'password_env', 'tls_ca'))
        if (not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9_.:\-]+', host)
                or type(port) is not int or not 1 <= port <= 65535 or not journal.is_absolute()
                or not isinstance(devices, list) or not devices):
            raise ValueError()
        if profile not in ('localhost', 'pi4'):
            raise ValueError()
        if profile == 'localhost' and host not in ('127.0.0.1', '::1', 'localhost'):
            raise ValueError()
        for name in (username, password):
            if name is not None and (not isinstance(name, str) or not re.fullmatch(r'[A-Z_][A-Z0-9_]*', name)):
                raise ValueError()
        if bool(username) != bool(password) or (ca is not None and not isinstance(ca, str)):
            raise ValueError()
        # Remote credentials must not travel in cleartext. The Pi4 profile is
        # a future deployment option, not permission to contact a board.
        if profile == 'pi4' and not (username and password and ca):
            raise ValueError()
        if any(name and not os.environ.get(name) for name in (username, password)):
            raise ValueError()
        settings = Settings(host, port, journal, devices, username, password, ca)
        build_mock_devices(settings)  # validate before creating files or sockets
        return settings
    except Exception:
        raise ConfigurationError('invalid_or_disabled_mock_configuration') from None


def build_mock_devices(settings: Settings):
    registry, states = DeviceRegistry(), {}
    try:
        for spec in settings.devices:
            if (not isinstance(spec, dict) or set(spec) != {'target_id', 'roles', 'capabilities', 'initial_state'}
                    or not isinstance(spec['target_id'], str)
                    or not re.fullmatch(r'mock_[a-z0-9_]{1,64}', spec['target_id'])
                    or not isinstance(spec['roles'], list) or not spec['roles']
                    or not all(isinstance(role, str) and role for role in spec['roles'])
                    or not isinstance(spec['capabilities'], dict) or not spec['capabilities']
                    or not isinstance(spec['initial_state'], dict)):
                raise ValueError()
            caps = {}
            for operation, values in spec['capabilities'].items():
                if (not isinstance(operation, str) or not operation or not isinstance(values, dict)
                        or set(values) - {'values', 'minimum', 'maximum'}):
                    raise ValueError()
                choices = values.get('values', [])
                lower, upper = values.get('minimum'), values.get('maximum')
                if not isinstance(choices, list):
                    raise ValueError()
                if choices:
                    if (lower is not None or upper is not None
                            or not all(type(item) in (str, bool) or finite_number(item) for item in choices)):
                        raise ValueError()
                elif not (finite_number(lower) and finite_number(upper) and lower <= upper):
                    raise ValueError()
                caps[operation] = Capability(operation, tuple(choices), lower, upper, verified=True)
            if (set(spec['initial_state']) != set(caps)
                    or not all(cap.accepts(spec['initial_state'][op]) for op, cap in caps.items())):
                raise ValueError()
            device = Device(spec['target_id'], 'mock', tuple(spec['roles']), caps,
                            online=True, state=dict(spec['initial_state']))
            registry.register(device)
            states[device.target_id] = device.state
        return registry, MockDeviceAdapter(states)
    except Exception:
        raise ConfigurationError('invalid_mock_devices') from None


def prepare_journal(path: Path) -> None:
    """Do not chmod/chown existing data. Refuse shared/foreign/symlink files."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
            raise ValueError()
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError()
        for candidate in (path, *(Path(str(path) + suffix) for suffix in ('.lock', '-journal', '-wal', '-shm'))):
            if candidate.exists() or candidate.is_symlink():
                info = candidate.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError()
    except Exception:
        raise ConfigurationError('private_journal_required') from None


def reject_json_number(value):
    raise ValueError('nonfinite_json_number')


def finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite_json_number')
    return number


class MockGatewayService:
    def __init__(self, settings: Settings) -> None:
        import paho.mqtt.client as mqtt
        self.settings = settings
        self.ready = threading.Event()
        self.rejected = threading.Event()
        self.resync = threading.Event()
        self.outbox: dict[str, dict] = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id='deskmate-mock-' + uuid.uuid4().hex,
                                  clean_session=True, protocol=mqtt.MQTTv311)
        self.client.connect_timeout = 3
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.max_queued_messages_set(1024)
        if settings.username_env:
            self.client.username_pw_set(os.environ[settings.username_env], os.environ[settings.password_env])
        if settings.tls_ca:
            self.client.tls_set(ca_certs=settings.tls_ca)
        prepare_journal(settings.journal)
        registry, adapter = build_mock_devices(settings)
        self.gateway = DeviceGateway(registry, {'mock': adapter}, publish_result=self.queue_result,
                                     enabled=True, mode='mock', automatic_control=False,
                                     journal_path=settings.journal)
        self.gateway.attach(self.client)
        original_connect = self.client.on_connect
        def on_connect(client, userdata, flags, reason, properties=None):
            self.ready.clear()
            if reason != 0:
                self.rejected.set()
                LOG.error('mock_mqtt_connection_rejected')
                return
            original_connect(client, userdata, flags, reason, properties)
        self.client.on_connect = on_connect
        original_message = self.client.on_message
        def on_message(client, userdata, message):
            try:
                data = json.loads(message.payload, parse_constant=reject_json_number,
                                  parse_float=finite_json_float).get('data')
                # Never claim canonical/live controller targets or unmarked
                # commands. Core Gateway still validates all command fields.
                if (not isinstance(data, dict) or data.get('execution_mode') != 'mock'
                        or not isinstance(data.get('target_id'), str)
                        or data['target_id'] not in registry.devices):
                    return
            except (ValueError, AttributeError, TypeError, UnicodeError, RecursionError):
                return
            original_message(client, userdata, message)
        self.client.on_message = on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.on_disconnect = self.on_disconnect
        self.client.on_connect_fail = lambda *_: LOG.warning('mock_mqtt_connect_failed_retrying')

    def on_subscribe(self, client, userdata, mid, reasons, properties=None):
        if reasons and all(not reason.is_failure for reason in reasons):
            self.resync.set()
            self.ready.set()
            LOG.info('mock_gateway_subscribed')
        else:
            self.ready.clear()
            self.rejected.set()
            LOG.error('mock_gateway_subscription_rejected')

    def on_disconnect(self, *args):
        self.ready.clear()
        LOG.info('mock_mqtt_disconnected')

    def queue_result(self, topic: str, envelope: dict) -> None:
        assert topic == RESULT_TOPIC and envelope['node'] == 'device-gateway-mock'
        # Gateway journals this result first. Only results, never device
        # commands, are replayed after MQTT reconnection or process restart.
        self.outbox[envelope['data']['command_id']] = envelope

    def flush_results(self) -> None:
        if not self.ready.is_set():
            return
        if self.resync.is_set():
            self.resync.clear()
            self.outbox.update(self.gateway.results)
        for cid, envelope in list(self.outbox.items()):
            info = self.client.publish(RESULT_TOPIC, json.dumps(envelope, allow_nan=False), qos=1, retain=False)
            if info.rc != 0:
                break
            del self.outbox[cid]

    async def run(self, stop: asyncio.Event) -> None:
        try:
            self.client.connect_async(self.settings.host, self.settings.port, keepalive=20)
            self.client.loop_start()
            LOG.info('mock_gateway_started_no_live_control')
            while not stop.is_set():
                if self.rejected.is_set():
                    raise RuntimeError('mqtt_access_rejected')
                if self.ready.is_set():
                    await self.gateway.pump()
                else:
                    await self.gateway.poll()  # expire/reconcile; no new command execution
                self.flush_results()
                try:
                    await asyncio.wait_for(stop.wait(), self.settings.poll_interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.ready.clear()
            try:
                self.client.disconnect()
            finally:
                try:
                    self.client.loop_stop()
                finally:
                    self.gateway.close()
                    LOG.info('mock_gateway_stopped')


async def serve(settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await MockGatewayService(settings).run(stop)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    previous_umask = os.umask(0o077)
    try:
        settings = load_settings(argv)
        asyncio.run(serve(settings))
        return 0
    except ConfigurationError:
        LOG.error('mock_gateway_configuration_error')
        return 2
    except Exception:
        # No exception text/tracebacks: libraries may include broker credentials,
        # payloads, local paths or account/device identifiers in their errors.
        LOG.error('mock_gateway_failed')
        return 1
    finally:
        os.umask(previous_umask)


if __name__ == '__main__':
    raise SystemExit(main())
