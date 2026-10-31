"""Host-only AIR_910604_WW PoC using profile-driven read validation.

No CLI, credentials, logging, MQTT, control adapter or background task. The owner
injects an existing probe-compatible SDK client and owns its HTTP session. All
public values are allowlisted; raw responses and SDK exceptions are not exposed.
Target selection and FIELDS are the PoC mapping, not a general appliance catalog.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .probe import ThinQReadApi, summarize_device

Scalar = int | float | str | bool
_collecting = ContextVar("thinq_read_collection", default=False)


class _SdkLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # SDK 1.0.14 DEBUG includes full URLs (device IDs). Suppress only this
        # collection task's SDK messages; don't change process-wide log levels.
        return not _collecting.get()


@contextmanager
def _private_sdk_call():
    logger = logging.getLogger("thinqconnect.thinq_api")
    log_filter = _SdkLogFilter()
    token = _collecting.set(True)
    logger.addFilter(log_filter)
    try:
        yield
    finally:
        logger.removeFilter(log_filter)
        _collecting.reset(token)


@dataclass(frozen=True)
class FieldSpec:
    group: str
    resource: str
    property: str
    kind: str
    # Attribute semantics belong to a mapping, not universal numeric validation.
    minimum: float | None = None
    maximum: float | None = None


# Only properties reported in the user's 2026-10-22 Profile/State validation.
# Keep PM2's API name: PM2.5 mapping and concentration units remain unverified.
FIELDS = MappingProxyType(
    {
        **{
            name: FieldSpec("measurements", "airQualitySensor", prop, kind, minimum=0)
            for name, prop, kind in (
                ("pm1", "PM1", "number"),
                ("pm1_level", "PM1Level", "level"),
                ("pm2", "PM2", "number"),
                ("pm2_level", "PM2Level", "level"),
                ("pm10", "PM10", "number"),
                ("pm10_level", "PM10Level", "level"),
                ("odor", "odor", "number"),
                ("odor_level", "odorLevel", "level"),
                ("total_pollution", "totalPollution", "number"),
                ("total_pollution_level", "totalPollutionLevel", "level"),
            )
        },
        "power": FieldSpec(
            "device_state", "operation", "airPurifierOperationMode", "power"
        ),
        "monitoring_mode": FieldSpec(
            "device_state", "airQualitySensor", "monitoringEnabled", "mode"
        ),
        "filter_remain_percent": FieldSpec(
            "maintenance",
            "filterInfo",
            "filterRemainPercent",
            "percent",
            minimum=0,
            maximum=100,
        ),
    }
)


def _finite(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _get(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _validate(spec: FieldSpec, profile: Any, state: Any) -> tuple[Scalar | None, str]:
    prop = _get(profile, "property", spec.resource, spec.property)
    if prop is None:
        return None, "unsupported"
    if not isinstance(prop, dict) or not isinstance(prop.get("mode"), list):
        return None, "unsupported_profile"
    if "r" not in prop["mode"]:
        return None, "not_readable"
    value = _get(state, spec.resource, spec.property)
    if value is None:
        return None, "missing"
    if spec.kind == "mode":
        type_ok = isinstance(value, str)
    elif spec.kind == "power":
        type_ok = isinstance(value, str) and value in ("POWER_ON", "POWER_OFF")
    elif spec.kind == "level":
        type_ok = _finite(value) or isinstance(value, str)
    else:
        type_ok = _finite(value)
    if not type_ok:
        return None, "invalid_value"
    if _finite(value) and (
        (spec.minimum is not None and value < spec.minimum)
        or (spec.maximum is not None and value > spec.maximum)
    ):
        return None, "out_of_range"

    readable = _get(prop, "value", "r")
    kind = prop.get("type")
    if (
        kind == "number"
        and prop.get("value") is not None
        and not isinstance(prop["value"], dict)
    ):
        return None, "unsupported_profile"
    if spec.kind == "mode" and kind not in ("enum", "list"):
        return None, "unsupported_profile"
    if kind in ("enum", "list") and isinstance(readable, list):
        accepted = any(type(value) is type(v) and value == v for v in readable)
        # JSON numeric int/double representation is immaterial, but bool is not 1.
        accepted |= _finite(value) and any(_finite(v) and value == v for v in readable)
    elif kind == "number" and readable is None:
        # Generic readable-number rule: no bound or unit is invented. Per-field
        # mapping bounds still apply; bool/nonfinite values never reach here.
        accepted = _finite(value)
    elif kind in ("range", "number") and isinstance(readable, dict):
        low, high = readable.get("min"), readable.get("max")
        if not (_finite(low) and _finite(high) and low <= high):
            return None, "unsupported_profile"
        accepted = _finite(value) and low <= value <= high
    else:
        return None, "unsupported_profile"
    if not accepted:
        return None, "out_of_range"
    return value, "ok"


@dataclass(frozen=True)
class LastGood:
    value: Scalar
    received_at: float
    api_age_sec: float | None
    # Historical value only, never a fallback current value.


@dataclass(frozen=True)
class Reading:
    value: Scalar | None
    valid: bool
    status: str
    received_at: float | None
    api_age_sec: float | None
    last_good: LastGood | None
    measured_at: None = None
    unit: None = None
    measurement_freshness: str = "unknown"


@dataclass(frozen=True)
class Snapshot:
    api_status: str
    connection: str
    received_at: float | None
    fields: Mapping[str, Reading]

    def to_dict(self) -> dict[str, Any]:
        """Allowlisted, identifier-free output; no raw Profile/State included."""
        result: dict[str, Any] = {
            "source": "thinq_api",
            "api_status": self.api_status,
            "connection": self.connection,
            "received_at": self.received_at,
            "freshness_basis": "api_receipt_only",
            "measurement_freshness": "unknown",
        }
        for name, spec in FIELDS.items():
            result.setdefault(spec.group, {})[name] = {
                "source_property": f"{spec.resource}.{spec.property}",
                **asdict(self.fields[name]),
            }
        return result


class _ReadFailure(Exception):
    """Only fixed safe categories, never exception text or response objects."""


class AirQualityCollector:
    """Call poll() explicitly; snapshot() ages stored readings without I/O.

    max_receipt_age_sec must be supplied by the caller: it is a local retrieval
    freshness setting, NOT an LG measurement/update interval or FSM threshold.
    An API success leaves connection unknown; only SDK error 1222 proves offline.
    Sessions and credentials stay with the caller. Cancellation propagates.
    """

    def __init__(
        self,
        api: ThinQReadApi,
        *,
        max_receipt_age_sec: float,
        timeout_sec: float = 15,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for value in (max_receipt_age_sec, timeout_sec):
            if not _finite(value) or value <= 0:
                raise ValueError("invalid_collection_timing")
        self._api = api
        self._max_age = max_receipt_age_sec
        self._timeout = timeout_sec
        self._wall, self._mono = wall_clock, monotonic_clock
        self._target: str | None = None
        self._lock = asyncio.Lock()
        self._api_status = "not_polled"
        self._connection = "unknown"
        self._received_at: float | None = None
        self._received_mono: float | None = None
        self._current = {name: (None, "missing") for name in FIELDS}
        self._good: dict[str, tuple[Scalar, float, float]] = {}

    async def _request(self, method: str, *args: str) -> Any:
        try:
            with _private_sdk_call():
                return await asyncio.wait_for(
                    getattr(self._api, method)(*args), timeout=self._timeout
                )
        except asyncio.TimeoutError:
            safe = "network_timeout"
        except Exception as error:
            code = getattr(error, "code", None)
            if code in ("1103", "1218", "1302", "1308"):
                safe = "authentication_or_authority"
            elif code == "1222":
                safe = "offline"
            elif code == "1306":
                safe = "rate_limited"
            elif isinstance(error, OSError):
                safe = "network_error"
            else:
                safe = "api_error"
        # Raise outside except: do not retain sensitive exception context either.
        raise _ReadFailure(safe)

    async def _select(self) -> None:
        devices = await self._request("async_get_device_list")
        if not isinstance(devices, list) or not all(
            isinstance(d, dict) for d in devices
        ):
            raise _ReadFailure("invalid_device_list")
        matches = [
            d
            for d in devices
            if summarize_device(d)["device_type"] == "DEVICE_AIR_PURIFIER"
            and summarize_device(d)["model_name"] == "AIR_910604_WW"
        ]
        if len(matches) != 1:
            raise _ReadFailure("target_not_unique")
        summary = summarize_device(matches[0], show_device_id=True)
        target = summary["device_id"]
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", target):
            raise _ReadFailure("invalid_target")
        if summary["reportable"] is not True:
            raise _ReadFailure("reporting_unavailable")
        # Pinned for this collector's lifetime; never silently switch appliances.
        self._target = target

    async def poll(self) -> Snapshot:
        async with self._lock:
            try:
                if self._target is None:
                    await self._select()
                profile = await self._request("async_get_device_profile", self._target)
                if not isinstance(profile, dict) or not isinstance(
                    profile.get("property"), dict
                ):
                    raise _ReadFailure("invalid_profile")
                state = await self._request("async_get_device_status", self._target)
                if not isinstance(state, dict) or not state:
                    raise _ReadFailure("invalid_state")
            except _ReadFailure as error:
                self._api_status = str(error)
                self._connection = "offline" if str(error) == "offline" else "unknown"
                self._received_at = self._received_mono = None
                self._current = {name: (None, str(error)) for name in FIELDS}
            else:
                self._api_status, self._connection = "ok", "unknown"
                self._received_at, self._received_mono = self._wall(), self._mono()
                self._current = {
                    name: _validate(spec, profile, state)
                    for name, spec in FIELDS.items()
                }
                for name, (value, status) in self._current.items():
                    if status == "ok":
                        self._good[name] = (
                            value,
                            self._received_at,
                            self._received_mono,
                        )
            return self.snapshot()

    def snapshot(self) -> Snapshot:
        """Recalculate API receipt ages; never make old cached data current."""
        now = self._mono()

        def age(stamp: float | None) -> float | None:
            return None if stamp is None or now < stamp else now - stamp

        current_age = age(self._received_mono)
        fields = {}
        for name, (value, status) in self._current.items():
            previous = self._good.get(name)
            last_good = (
                LastGood(previous[0], previous[1], age(previous[2]))
                if previous is not None
                else None
            )
            if status == "ok":
                if current_age is None:
                    status = "clock_error"
                elif current_age >= self._max_age:
                    status = "stale"
            fields[name] = Reading(
                value if status == "ok" else None,
                status == "ok",
                status,
                self._received_at,
                current_age,
                last_good,
            )
        return Snapshot(
            self._api_status,
            self._connection,
            self._received_at,
            MappingProxyType(fields),
        )
