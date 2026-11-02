"""Host-side device I/O. No SDK import, account access or network at import time.

ThinQApi is injected by its owner, with a live aiohttp session. Only the official
async_get_device_profile/status and async_post_device_control methods are used.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .devices import Capability, Device, finite_number, same_value


class DeviceIOError(Exception):
    """Safe code only: never attach SDK exceptions, headers, device IDs or PATs."""


@dataclass(frozen=True)
class ControlReceipt:
    api_accepted: bool
    mode: str
    # API acceptance alone is not evidence of physical operation.


class DeviceAdapter(Protocol):
    mode: str
    async def read_state(self, device: Device) -> dict[str, Any]: ...
    async def control(self, device: Device, operation: str, value: Any, *, expires_at: float | None = None) -> ControlReceipt: ...


class MockDeviceAdapter:
    mode = 'mock'

    def __init__(self, states: dict[str, dict[str, Any]], *, apply: bool = True, clock=time.time) -> None:
        self.states = {key: dict(value) for key, value in states.items()}
        self.apply, self.clock = apply, clock
        self.calls: list[tuple[str, str, Any]] = []
        self.error: str | None = None

    async def read_state(self, device: Device) -> dict[str, Any]:
        if self.error:
            raise DeviceIOError(self.error)
        if device.mode != self.mode or device.target_id not in self.states:
            raise DeviceIOError('offline')
        return dict(self.states[device.target_id])

    async def control(self, device: Device, operation: str, value: Any, *, expires_at: float | None = None) -> ControlReceipt:
        if self.error:
            raise DeviceIOError(self.error)
        cap = device.capabilities.get(operation)
        if device.mode != self.mode or cap is None or not cap.accepts(value):
            raise DeviceIOError('unsupported_operation')
        if expires_at is not None and self.clock() >= expires_at:
            raise DeviceIOError('command_expired')
        self.calls.append((device.target_id, operation, value))
        if self.apply:
            self.states[device.target_id][operation] = value
        return ControlReceipt(api_accepted=True, mode=self.mode)


def _path(data: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            raise DeviceIOError('profile_or_state_missing')
        data = data[key]
    return data


def _profile_accepts(prop: Any, value: Any) -> bool:
    if not isinstance(prop, dict) or 'w' not in prop.get('mode', []) or 'r' not in prop.get('mode', []):
        return False
    writable = (prop.get('value') or {}).get('w')
    if prop.get('type') in ('enum', 'list'):
        return isinstance(writable, list) and any(same_value(value, item) for item in writable)
    if prop.get('type') == 'boolean':
        return type(value) is bool
    if prop.get('type') == 'range' and isinstance(writable, dict) and finite_number(value):
        lower, upper, step = writable.get('min'), writable.get('max'), writable.get('step')
        if not all(finite_number(item) for item in (lower, upper, step)) or step <= 0:
            return False
        steps = (value - lower) / step
        return lower <= value <= upper and abs(steps - round(steps)) < 1e-8
    return False  # Unknown profile forms are not guessed.


class ThinQConnectAdapter:
    mode = 'live'

    def __init__(self, api, *, allow_control: bool = False, timeout_sec: float = 5, clock=time.time) -> None:
        if not finite_number(timeout_sec) or timeout_sec <= 0:
            raise ValueError('invalid_timeout')
        self._api, self.allow_control, self.timeout, self.clock = api, allow_control, timeout_sec, clock

    async def _request(self, method: str, *args) -> Any:
        try:
            return await asyncio.wait_for(getattr(self._api, method)(*args, timeout=self.timeout), self.timeout)
        except asyncio.TimeoutError:
            raise DeviceIOError('network_timeout') from None
        except Exception as error:
            # Official ThinQAPIException stores sensitive request headers. Never
            # stringify it; expose only a small allowlisted category.
            code = str(getattr(error, 'code', ''))
            if code in ('1103', '1218', '1302', '1308'):
                safe = 'authentication_or_authority'
            elif code == '1222':
                safe = 'offline'
            elif isinstance(error, OSError):
                safe = 'network_error'
            else:
                safe = 'api_error'
            raise DeviceIOError(safe) from None

    def _capability(self, device: Device, operation: str) -> Capability:
        cap = device.capabilities.get(operation)
        if (device.mode != self.mode or not device.external_id or cap is None or not cap.verified
                or len(cap.profile_path) != 3 or cap.profile_path[0] != 'property'
                or len(cap.state_path) != 2 or cap.state_path != cap.profile_path[1:]):
            raise DeviceIOError('unverified_profile_binding')
        return cap

    async def read_state(self, device: Device) -> dict[str, Any]:
        if device.mode != self.mode or not device.external_id:
            raise DeviceIOError('unregistered_device')
        raw = await self._request('async_get_device_status', device.external_id)
        state = {}
        for operation in device.capabilities:
            cap = self._capability(device, operation)
            try:
                state[operation] = cap.from_wire(_path(raw, cap.state_path))
            except ValueError:
                raise DeviceIOError('unmapped_state') from None
        return state

    async def control(self, device: Device, operation: str, value: Any, *, expires_at: float | None = None) -> ControlReceipt:
        if not self.allow_control:
            raise DeviceIOError('live_control_disabled')
        cap = self._capability(device, operation)
        try:
            wire = cap.to_wire(value)
        except ValueError:
            raise DeviceIOError('unsupported_operation') from None
        profile = await self._request('async_get_device_profile', device.external_id)
        if not _profile_accepts(_path(profile, cap.profile_path), wire):
            raise DeviceIOError('profile_not_writable_or_value_unsupported')
        resource, prop = cap.state_path
        # SDK successful POST may return None. Only absence of an exception
        # acknowledges the API request; Gateway must poll status separately.
        if expires_at is not None and self.clock() >= expires_at:
            raise DeviceIOError('command_expired')
        await self._request('async_post_device_control', device.external_id, {resource: {prop: wire}})
        return ControlReceipt(api_accepted=True, mode=self.mode)
