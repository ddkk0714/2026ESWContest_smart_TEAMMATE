"""Existing MQTT cmd/result contract; sequential I/O and deduplicated execution.

Run on a full Python host, not Atlas restricted Python. This module never starts
an MQTT connection itself. attach(client) uses an operator-owned MQTT client;
pump() must be awaited by its owner. No UART, new topic, or proposal ownership.
"""
from __future__ import annotations

import json
import queue
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from .command_journal import CommandJournal, JournalError
from .device_adapters import DeviceAdapter, DeviceIOError
from .devices import Device, DeviceRegistry, finite_number, same_value

CMD_TOPIC = 'deskmate/control/cmd'
RESULT_TOPIC = 'deskmate/control/result'


@dataclass
class Pending:
    command: dict[str, Any]
    device: Device
    adapter: DeviceAdapter
    previous_value: Any
    recovery: bool = False


class DeviceGateway:
    def __init__(self, registry: DeviceRegistry, adapters: dict[str, DeviceAdapter], *,
                 publish_result: Callable[[str, dict[str, Any]], None], enabled: bool = False,
                 mode: str = 'mock', allow_live: bool = False, automatic_control: bool = False,
                 state_max_age_sec: float = 30, manual_override_sec: float = 1800,
                 max_commands: int = 1024, external_targets: tuple[str, ...] = (), journal_path=None, clock: Callable[[], float] = time.time) -> None:
        if mode not in ('mock', 'live') or max_commands < 1:
            raise ValueError('invalid_gateway_configuration')
        if any(type(flag) is not bool for flag in (enabled, allow_live, automatic_control)):
            raise ValueError('gateway_flags_must_be_booleans')
        if any(not finite_number(value) or value < 0 for value in (state_max_age_sec, manual_override_sec)):
            raise ValueError('invalid_gateway_freshness_or_manual_hold')
        self.registry, self.adapters, self.publish_result = registry, adapters, publish_result
        self.enabled, self.mode, self.allow_live = enabled, mode, allow_live
        self.automatic_control = automatic_control
        self.state_age, self.manual_hold, self.max_commands = state_max_age_sec, manual_override_sec, max_commands
        self.clock = clock
        self._initial_mode = mode
        self._closed = False
        self._recovery_cancel: dict[str, dict[str, Any]] = {}
        self._recovery_unavailable: dict[str, dict[str, Any]] = {}
        # Pi5 (or its explicitly selected BLE bridge) owns canonical lamp/fan
        # targets. An empty registry must never publish competing failures.
        self.external_targets = {'desk_lamp', 'vent_fan', *external_targets}
        self._commands: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.results: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, Pending] = {}
        self.waiting_undo: dict[str, tuple[dict[str, Any], str]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue = queue.Queue(maxsize=max_commands)
        self._records: dict[str, dict[str, Any]] = {}
        self._journal = CommandJournal(journal_path, mode) if journal_path is not None else None
        if self._journal is not None:
            try:
                for command, record in self._journal.load():
                    if not self._valid(command) or len(self._commands) >= max_commands:
                        raise JournalError('incompatible_journal_record')
                    cid = command['command_id']
                    self._commands[cid], self._records[cid] = command, record
                    self.evidence[cid] = dict(record['evidence'])
                    result = record.get('result')
                    if result is not None:
                        self.results[cid] = result
                    if result is None or result['data']['status'] == 'executing':
                        self.evidence[cid]['recovered'] = True
                        # Resolution is read-only, including a crash before POST.
                        if record.get('waiting_for') is not None:
                            self._recovery_cancel[cid] = command
                            continue
                        device = self.registry.devices.get(command['target_id'])
                        adapter = self.adapters.get(device.adapter) if device is not None else None
                        if device is not None and adapter is not None and device.mode == adapter.mode == mode:
                            self.pending[cid] = Pending(command, device, adapter, record.get('previous_value'), True)
                        else:
                            self._recovery_unavailable[cid] = command
            except Exception:
                self.close()
                raise JournalError('journal_restore_failed') from None

    def close(self) -> None:
        """Release only the local journal. No service, adapter or MQTT restart."""
        self._closed = True
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    def _save(self, command_id: str, **changes) -> None:
        record = self._records[command_id]
        record.update(changes, evidence=dict(self.evidence[command_id]))
        if self._journal is not None:
            self._journal.save(command_id, record)

    def attach(self, client) -> None:
        """Attach to a dedicated, operator-created paho-compatible MQTT client.

        Broker ACL must restrict cmd publishing to Hub. node='hub' is routing
        metadata, not authentication. Do not attach to Hub's MQTT client.
        """
        def on_connect(client, userdata, flags, reason_code, properties=None):
            if reason_code == 0:
                client.subscribe(CMD_TOPIC, qos=1)

        def on_message(client, userdata, message):
            try:
                self._queue.put_nowait((message.topic, message.payload, bool(message.retain)))
            except queue.Full:
                pass  # Do not execute a command that cannot be tracked safely.

        client.on_connect, client.on_message = on_connect, on_message

    async def pump(self) -> None:
        # Single caller/event loop is required. Process commands sequentially,
        # so humidity exclusion and per-device reservations are atomic here.
        while not self._queue.empty():
            topic, payload, retained = self._queue.get_nowait()
            await self.handle_message(topic, payload, retained=retained)
        await self.poll()

    def _emit(self, command: dict[str, Any], status: str, *, error: str | None = None,
              actual: Any = None, has_actual: bool = False) -> dict[str, Any]:
        data = {'command_id': command['command_id'], 'status': status, 'error_code': error,
                'error_message': None, 'completed_ts_ms': int(self.clock() * 1000) if status != 'executing' else None}
        if has_actual:
            data['actual_value'] = actual
        envelope = {'schema_version': '1.0', 'ts': self.clock(),
                    'node': f'device-gateway-{self._initial_mode}', 'data': data}
        self._save(command['command_id'], result=envelope, phase=status)
        self.results[command['command_id']] = envelope
        self.publish_result(RESULT_TOPIC, envelope)
        return envelope

    def _check(self, command: dict[str, Any], device: Device, *, ignore_busy: bool = False) -> str | None:
        now = self.clock()
        if now * 1000 >= command['expires_ts_ms']:
            return 'command_expired'
        if not self.enabled or self.mode != 'mock' or self.mode != self._initial_mode:
            return 'control_disabled'
        if command['gate'] == 'auto' and not self.automatic_control:
            return 'automatic_control_disabled'
        if command['gate'] == 'auto' and 'air_cleaning' in device.roles:
            return 'air_quality_policy_unverified'
        if device.mode != self.mode or not device.gateway_owned:
            return 'target_owned_elsewhere_or_mode_mismatch'
        if not device.online:
            return 'offline'
        if device.last_manual_at is not None and now - device.last_manual_at < self.manual_hold:
            return 'manual_override'
        cap = device.capabilities.get(command['operation'])
        if cap is None or not cap.accepts(command['value']):
            return 'unsupported_operation'
        if not cap.reversible and not (command['gate'] == 'suggest' and command['requires_confirmation']):
            return 'confirmation_required'
        if self.registry.humidity_conflict(device, command['operation'], command['value'], now, self.state_age):
            return 'humidity_interlock'
        for pending in self.pending.values():
            if pending.device.target_id == device.target_id and not ignore_busy:
                return 'device_busy'
            other = pending.device
            if command['operation'] == 'set_power' and command['value'] == 'on':
                if (('humidifying' in device.roles and 'dehumidifying' in other.roles)
                        or ('dehumidifying' in device.roles and 'humidifying' in other.roles)):
                    return 'humidity_interlock'
        return None

    @staticmethod
    def _valid(command: Any) -> bool:
        return (isinstance(command, dict) and isinstance(command.get('command_id'), str)
                and 0 < len(command['command_id']) <= 128
                and isinstance(command.get('target_id'), str) and isinstance(command.get('operation'), str)
                and 'value' in command and finite_number(command.get('expires_ts_ms'))
                and command.get('gate') in ('auto', 'suggest', 'undo')
                and type(command.get('requires_confirmation')) is bool
                and (not command['requires_confirmation'] or command['gate'] == 'suggest'))

    async def handle_message(self, topic: str, payload: bytes | str | dict, *, retained: bool = False):
        if self._closed or topic != CMD_TOPIC:
            return None
        try:
            envelope = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
        except (ValueError, UnicodeError):
            return None
        if not isinstance(envelope, dict) or envelope.get('node') != 'hub':
            return None
        command = envelope.get('data')
        if not self._valid(command):
            return None
        # Ignore targets not owned by this gateway: no competing result for the
        # existing Pi5/BLE controller. Unknown canonical targets fail explicitly.
        if command['target_id'] in self.external_targets:
            return None
        device = self.registry.devices.get(command['target_id'])
        if device is not None and not device.gateway_owned:
            return None
        cid = command['command_id']
        if cid in self._commands:
            if command != self._commands[cid]:
                # An altered duplicate never overwrites the original outcome.
                return None
            cached = self.results.get(cid)
            if cached is None and cid not in self.pending and cid not in self._recovery_cancel:
                return self._emit(command, 'failed', error='recovery_target_unavailable')
            if cached is not None:
                self.publish_result(RESULT_TOPIC, cached)
            return cached
        # Never evict an ID and accidentally repeat a QoS1 command. With a
        # journal, the claim is committed before any adapter interaction.
        if len(self._commands) >= self.max_commands:
            return None
        evidence = {'mode': self._initial_mode, 'write_attempted': False,
                    'api_accepted': False, 'state_confirmed': False}
        if self._journal is not None:
            self._journal.claim(command, evidence)
        self._commands[cid] = json.loads(json.dumps(command))
        self.evidence[cid] = evidence
        self._records[cid] = {'phase': 'claimed', 'evidence': dict(evidence)}
        if retained:
            return self._emit(command, 'failed', error='retained_command_rejected')
        if device is None:
            return self._emit(command, 'failed', error='unregistered_device')
        adapter = self.adapters.get(device.adapter)
        error = self._check(command, device)
        if error == 'device_busy' and command['gate'] == 'undo':
            for parent_id, parent in self.pending.items():
                if (parent.device.target_id == device.target_id
                        and parent.command['operation'] == command['operation']
                        and same_value(parent.previous_value, command['value'])):
                    error = self._check(command, device, ignore_busy=True)
                    if error is None:
                        self.waiting_undo[cid] = (dict(command), parent_id)
                        self._save(cid, waiting_for=parent_id)
                        # No POST yet: the original request must settle first.
                        return self._emit(command, 'executing')
                    break
        if error or adapter is None or adapter.mode != self.mode:
            return self._emit(command, 'failed', error=error or 'adapter_unavailable')
        return await self._execute(command, device, adapter)

    async def _execute(self, command: dict[str, Any], device: Device, adapter: DeviceAdapter):
        cid = command['command_id']
        error = self._check(command, device)
        if error:
            return self._emit(command, 'failed', error=error)
        try:
            # Refresh actual state before changing it; unexpected external
            # changes impose the manual override hold instead of being undone.
            before = await adapter.read_state(device)
            self.registry.observe(device.target_id, before, self.clock())
            error = self._check(command, device)
            if error:
                return self._emit(command, 'failed', error=error)
            if (command['operation'] not in before
                    or not device.capabilities[command['operation']].accepts(before[command['operation']])):
                return self._emit(command, 'failed', error='unknown_current_state')
            if same_value(before[command['operation']], command['value']):
                self.evidence[cid].update(state_confirmed=True, outcome='already_satisfied')
                return self._emit(command, 'succeeded', actual=before[command['operation']], has_actual=True)
            self.evidence[cid]['write_attempted'] = True
            self._save(cid, phase='attempting', previous_value=before[command['operation']])
            receipt = await adapter.control(device, command['operation'], command['value'],
                                            expires_at=command['expires_ts_ms'] / 1000)
            if not receipt.api_accepted or receipt.mode != self.mode:
                return self._emit(command, 'failed', error='request_not_accepted')
        except DeviceIOError as error:
            if self.evidence[cid]['write_attempted'] and str(error) in ('network_timeout', 'network_error'):
                # The device may have acted. Reconcile by GET; never resend POST.
                self.evidence[cid].update(api_accepted=None, request_outcome='unknown')
                self.pending[cid] = Pending(dict(command), device, adapter, before[command['operation']])
                return self._emit(command, 'executing', error='request_outcome_unknown')
            return self._emit(command, 'failed', error=str(error))
        self.evidence[cid]['api_accepted'] = True
        self.pending[cid] = Pending(dict(command), device, adapter, before[command['operation']])
        return self._emit(command, 'executing')  # API accepted, physical result unconfirmed.

    async def poll(self) -> None:
        if self._closed:
            return
        for cid, command in list(self._recovery_unavailable.items()):
            self._emit(command, 'failed', error='recovery_target_unavailable')
            del self._recovery_unavailable[cid]
        for cid, command in list(self._recovery_cancel.items()):
            self._emit(command, 'cancelled', error='recovered_undo_requires_new_request')
            del self._recovery_cancel[cid]
        for cid, pending in list(self.pending.items()):
            command, device, adapter = pending.command, pending.device, pending.adapter
            if self.mode != self._initial_mode or self.mode != 'mock' or device.mode != adapter.mode or device.mode != self.mode:
                self._emit(command, 'cancelled', error='mode_change_blocked')
                del self.pending[cid]
                continue
            if self.clock() * 1000 >= command['expires_ts_ms']:
                self._emit(command, 'timeout', error='state_not_confirmed_before_expiry')
                del self.pending[cid]
                continue
            if device.last_manual_at is not None and self.clock() - device.last_manual_at < self.manual_hold:
                self._emit(command, 'cancelled', error='manual_override')
                del self.pending[cid]
                continue
            try:
                after = await adapter.read_state(device)
                if self.clock() * 1000 >= command['expires_ts_ms']:
                    self._emit(command, 'timeout', error='state_not_confirmed_before_expiry')
                    del self.pending[cid]
                    continue
                expected = {command['operation']: command['value']}
                self.registry.observe(device.target_id, after, self.clock(), expected=expected)
                if device.last_manual_at is not None and self.clock() - device.last_manual_at < self.manual_hold:
                    self._emit(command, 'cancelled', error='manual_override')
                    del self.pending[cid]
                elif command['operation'] in after and same_value(after[command['operation']], command['value']):
                    self.evidence[cid].update(state_confirmed=True, outcome=(
                        'recovered_state_matches' if pending.recovery else 'state_confirmed_after_request'))
                    self._emit(command, 'succeeded', actual=after[command['operation']], has_actual=True)
                    del self.pending[cid]
            except DeviceIOError:
                # Query failure is not proof the command failed or succeeded;
                # keep waiting within the original deadline, without resending.
                continue

        for cid, (command, parent_id) in list(self.waiting_undo.items()):
            if self.clock() * 1000 >= command['expires_ts_ms']:
                self._emit(command, 'timeout', error='undo_expired_waiting_for_original')
            elif parent_id in self.pending:
                continue
            elif self.results[parent_id]['data']['status'] != 'succeeded':
                self._emit(command, 'failed', error='original_state_unconfirmed')
            else:
                device = self.registry.devices[command['target_id']]
                await self._execute(command, device, self.adapters[device.adapter])
            del self.waiting_undo[cid]
