"""YAML-driven command planning only; FSM gates/feedback remain in Dispatcher."""
from __future__ import annotations

import json
import pkgutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

from ..inference import GateMode
from ..ingest.cache import Sample
from .devices import DeviceRegistry, finite_number, same_value


def load_environment_control_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        try:
            raw = pkgutil.get_data('deskmate_hub', 'config/environment_control.json')
        except OSError:
            raw = None
        if raw is not None:
            return json.loads(raw)
        path = Path(__file__).parent.parent / 'config/environment_control.yaml'
    import yaml
    with open(path, encoding='utf-8') as handle:
        return yaml.safe_load(handle)


@dataclass(frozen=True)
class MockControlTransport:
    publish: Callable[[dict[str, Any]], None]
    mode: str = field(default='mock', init=False)


class EnvironmentControlPolicy:
    def __init__(self, cfg: dict[str, Any], registry: DeviceRegistry) -> None:
        self.cfg, self.registry = cfg, registry
        self.enabled = cfg.get('enabled') is True
        if self.enabled and cfg.get('mode') != 'mock':
            raise ValueError('environment policy can only be enabled in mock mode in this milestone')
        for key in ('sensor_max_age_sec', 'sensor_future_skew_sec', 'state_max_age_sec', 'manual_override_sec'):
            if not finite_number(cfg.get(key)) or cfg[key] < 0:
                raise ValueError(f'invalid {key}')
        self.rules = cfg.get('rules', {})
        for rule in self.rules.values():
            if rule['direction'] not in ('above', 'below'):
                raise ValueError('invalid rule direction')
            for key in ('threshold', 'hysteresis', 'minimum_duration_sec', 'cooldown_sec'):
                if not finite_number(rule.get(key)) or (key != 'threshold' and rule[key] < 0):
                    raise ValueError(f'invalid rule {key}')
            # No model-independent evidence currently justifies purifier control.
            if rule['role'] == 'air_cleaning':
                raise ValueError('air-cleaning policy requires a separately validated PM/air-quality design')
            if rule['sensor'] == 'co2_ppm' and rule['role'] != 'outside_air':
                raise ValueError('CO2 can only request outside-air exchange')
        self._since: dict[str, float] = {}
        self._last_sent: dict[str, float] = {}
        self._last_sample: float | None = None
        self._now: float | None = None
        self.recommendations: list[str] = []

    def observe(self, sample: Sample | None, now: float) -> None:
        self.recommendations = []
        if self._now is not None and now < self._now:
            self._since.clear()
        self._now = now
        fresh = (sample is not None and finite_number(sample.received)
                 and 0 <= now - sample.received <= self.cfg['sensor_max_age_sec']
                 and finite_number(sample.ts)
                 and -self.cfg['sensor_future_skew_sec'] <= now - sample.ts <= self.cfg['sensor_max_age_sec'])
        if (not self.enabled or not fresh or (self._last_sample is not None and sample is not None
                and (sample.received < self._last_sample or
                     sample.received - self._last_sample > self.cfg['sensor_max_age_sec']))):
            self._since.clear()
        if not self.enabled or not fresh:
            self._last_sample = None
            return
        self._last_sample = sample.received
        for name, rule in self.rules.items():
            value = sample.data.get(rule['sensor'])
            low, high = rule['valid_range']
            if (sample.data.get(rule['valid_field']) is not True or not finite_number(value)
                    or not low <= value <= high):
                self._since.pop(name, None)
                continue
            threshold, margin = rule['threshold'], rule['hysteresis']
            active = name in self._since
            if rule['direction'] == 'above':
                enters, exits = value >= threshold, value <= threshold - margin
            else:
                enters, exits = value <= threshold, value >= threshold + margin
            if active and exits:
                self._since.pop(name, None)
            elif not active and enters:
                self._since[name] = now

    def _ready(self, name: str, now: float) -> bool:
        rule = self.rules[name]
        return (self.enabled and self._now is not None and 0 <= now - self._now <= self.cfg['sensor_max_age_sec']
                and self._last_sample is not None and 0 <= now - self._last_sample <= self.cfg['sensor_max_age_sec']
                and name in self._since and now - self._since[name] >= rule['minimum_duration_sec']
                and (name not in self._last_sent or now - self._last_sent[name] >= rule['cooldown_sec']))

    def plan(self, state: str, cause: str | None, gate: GateMode, now: float) -> list[dict[str, Any]]:
        self.recommendations = []
        if state != 'ACTION_ENV' or cause != 'environment' or gate is GateMode.NONE:
            return []
        if gate is GateMode.AUTO and self.cfg.get('automatic_control') is not True:
            return []
        plans: list[dict[str, Any]] = []
        used: set[tuple[str, str]] = set()
        humidity_role: str | None = None
        for name, rule in self.rules.items():
            if not self._ready(name, now):
                continue
            role = rule['role']
            if role in ('humidifying', 'dehumidifying') and humidity_role not in (None, role):
                continue
            device = self.registry.select(role, rule['operation'], rule['value'], now, mode='mock',
                                          state_max_age=self.cfg['state_max_age_sec'],
                                          manual_hold=self.cfg['manual_override_sec'])
            if device is None:
                self.recommendations.append(name)  # Existing env_suggest card remains the only UI proposal.
                continue
            key = device.target_id, rule['operation']
            cap = device.capabilities[rule['operation']]
            previous = device.state[rule['operation']]
            # No irreversible/unknown rollback operations in environmental automation.
            if key in used or not cap.reversible or not cap.accepts(previous):
                continue
            used.add(key)
            if role in ('humidifying', 'dehumidifying'):
                humidity_role = role
            plans.append({'target_id': device.target_id, 'operation': rule['operation'], 'value': rule['value'],
                          'undo_value': previous, 'context': {'policy_rule': name}})
        return plans

    def advice(self) -> list[dict[str, Any]]:
        return [{'rule': name, 'sensor': self.rules[name]['sensor'], 'role': self.rules[name]['role'],
                 'reason': 'no_eligible_device',
                 'kind': 'manual_ventilation' if self.rules[name]['role'] == 'outside_air' else 'manual_environment_adjustment'}
                for name in self.recommendations]

    def can_dispatch(self, command, now: float) -> bool:
        name = command.context.get('policy_rule')
        if name not in self.rules:
            return False
        device = self.registry.devices.get(command.target_id)
        if device is None or not self.registry.eligible(device, command.operation, command.value, now,
                mode='mock', state_max_age=self.cfg['state_max_age_sec'], manual_hold=self.cfg['manual_override_sec']):
            return False
        if self.registry.humidity_conflict(device, command.operation, command.value, now, self.cfg['state_max_age_sec']):
            return False
        if command.gate == 'undo':
            return same_value(device.state.get(command.operation), command.context.get('undo_of_value'))
        return (self._ready(name, now) and same_value(device.state.get(command.operation), command.undo_value)
                and not same_value(device.state.get(command.operation), command.value))

    def dispatched(self, command, now: float) -> None:
        if command.gate != 'undo':
            self._last_sent[command.context['policy_rule']] = now
