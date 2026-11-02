"""Model-independent, explicitly registered capabilities. No network imports."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


def same_value(left: Any, right: Any) -> bool:
    # bool is an int in Python, but a power boolean is not a numeric set point.
    return type(left) is type(right) and left == right or (
        type(left) in (int, float) and type(right) in (int, float) and left == right
    )


def finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Capability:
    operation: str
    values: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    reversible: bool = True
    verified: bool = False
    # ThinQ bindings must be checked against a real fetched profile before use.
    profile_path: tuple[str, ...] = ()
    state_path: tuple[str, ...] = ()
    wire_values: tuple[tuple[Any, Any], ...] = ()

    def accepts(self, value: Any) -> bool:
        if not self.verified:
            return False
        if self.values:
            return any(same_value(value, item) for item in self.values)
        return (finite_number(value) and self.minimum is not None and self.maximum is not None
                and self.minimum <= value <= self.maximum)

    def to_wire(self, value: Any) -> Any:
        if not self.accepts(value):
            raise ValueError('unsupported_operation_value')
        if not self.wire_values:
            return value
        for logical, wire in self.wire_values:
            if same_value(logical, value):
                return wire
        raise ValueError('unmapped_value')

    def from_wire(self, value: Any) -> Any:
        if not self.wire_values:
            return value
        for logical, wire in self.wire_values:
            if same_value(wire, value):
                return logical
        raise ValueError('unmapped_state')


@dataclass
class Device:
    target_id: str
    adapter: str
    roles: tuple[str, ...]
    capabilities: dict[str, Capability]
    mode: str = 'mock'
    online: bool = False
    state: dict[str, Any] = field(default_factory=dict)
    observed_at: float | None = None
    last_manual_at: float | None = None
    external_id: str | None = field(default=None, repr=False)
    # False for devices owned by an existing controller (e.g. Pi5 iLink lamp).
    gateway_owned: bool = True


class DeviceRegistry:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}

    def register(self, device: Device) -> None:
        if not device.target_id or device.target_id in self.devices:
            raise ValueError('duplicate_or_empty_target')
        if device.mode not in ('mock', 'live'):
            raise ValueError('invalid_device_mode')
        if any(name != cap.operation for name, cap in device.capabilities.items()):
            raise ValueError('capability_operation_mismatch')
        self.devices[device.target_id] = device

    def observe(self, target_id: str, values: dict[str, Any], now: float,
                *, expected: dict[str, Any] | None = None) -> None:
        device = self.devices[target_id]
        # Unexplained changes by a user/other controller take priority over us.
        for operation, value in values.items():
            if operation in device.state and not same_value(device.state[operation], value):
                if expected is None or operation not in expected or not same_value(expected[operation], value):
                    device.last_manual_at = now
        device.state = dict(values)
        device.observed_at, device.online = now, True

    def mark_manual(self, target_id: str, now: float) -> None:
        self.devices[target_id].last_manual_at = now

    def eligible(self, device: Device, operation: str, value: Any, now: float, *, mode: str,
                 state_max_age: float, manual_hold: float) -> bool:
        capability = device.capabilities.get(operation)
        age = now - device.observed_at if device.observed_at is not None else -1
        return (device.gateway_owned and device.mode == mode and device.online and 0 <= age <= state_max_age
                and capability is not None and capability.accepts(value)
                and operation in device.state
                and (device.last_manual_at is None or now - device.last_manual_at >= manual_hold))

    def humidity_conflict(self, device: Device, operation: str, value: Any,
                          now: float, state_max_age: float) -> bool:
        if operation != 'set_power' or value != 'on':
            return False
        opposites = {'humidifying': 'dehumidifying', 'dehumidifying': 'humidifying'}
        for role, opposite in opposites.items():
            if role not in device.roles:
                continue
            for other in self.devices.values():
                if opposite not in other.roles or other.target_id == device.target_id:
                    continue
                age = now - other.observed_at if other.observed_at is not None else -1
                if (not other.online or not 0 <= age <= state_max_age
                        or other.state.get('set_power') != 'off'):
                    return True
        return False

    def select(self, role: str, operation: str, value: Any, now: float, *, mode: str,
               state_max_age: float, manual_hold: float) -> Device | None:
        for target_id in sorted(self.devices):
            device = self.devices[target_id]
            if role not in device.roles:
                continue
            if not self.eligible(device, operation, value, now, mode=mode,
                                 state_max_age=state_max_age, manual_hold=manual_hold):
                continue
            if same_value(device.state[operation], value):
                continue
            if not self.humidity_conflict(device, operation, value, now, state_max_age):
                return device
        return None
