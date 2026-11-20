"""Versioned appliance observations and isolated cache; stdlib only on ATLAS.

Receipt freshness is not measurement freshness. Nothing here feeds FSM scores,
DeviceRegistry.observe, actuator commands, MQTT networking or credential storage.
"""

from __future__ import annotations

import copy
import json
import math
import re
import threading
import time

PREFIX = "deskmate/sensor/appliance/"
MAX_TTL = 300.0  # protocol lifetime ceiling, not an inference threshold


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def logical_id(value):
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value)
        and not re.fullmatch(r"[a-f0-9]{16,}", value)
    )


def topic_for(source, device):
    if not logical_id(source) or not logical_id(device):
        raise ValueError("invalid_logical_identity")
    return PREFIX + source + "/" + device


def validate(topic, payload, now):
    """Strict allowlist; rejects unknown/private fields rather than caching them."""
    if not isinstance(payload, bytes) or len(payload) > 65536 or not finite(now):
        raise ValueError("invalid_observation")

    def no_constant(_):
        raise ValueError("nonfinite_json")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    body = json.loads(
        payload, parse_constant=no_constant, object_pairs_hook=object_pairs
    )
    keys = {
        "schema_name",
        "schema_version",
        "node",
        "logical_device_id",
        "boot_id",
        "seq",
        "ts",
        "expires_at",
        "api_received_at",
        "data",
    }
    if not isinstance(body, dict) or set(body) != keys:
        raise ValueError("invalid_observation")
    if (
        body["schema_name"] != "appliance_observation"
        or body["schema_version"] != "1.0"
        or topic != topic_for(body["node"], body["logical_device_id"])
        or not isinstance(body["boot_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", body["boot_id"]) is None
        or type(body["seq"]) is not int
        or not 0 <= body["seq"] <= 2**63 - 1
    ):
        raise ValueError("invalid_identity_or_version")
    issued, expiry, api_time = body["ts"], body["expires_at"], body["api_received_at"]
    if (
        not finite(issued)
        or not finite(expiry)
        or issued <= 0
        or not issued < expiry <= issued + MAX_TTL
        or not issued <= now < expiry
    ):
        raise ValueError("expired_or_unsynced")
    if api_time is not None and (
        not finite(api_time) or not 0 <= issued - api_time <= MAX_TTL
    ):
        raise ValueError("invalid_api_receipt")
    data = body["data"]
    if (
        not isinstance(data, dict)
        or set(data) != {"mode", "api_status", "connection", "observations"}
        or data["mode"] not in ("fixture", "live")
        or data["connection"] not in ("unknown", "offline")
        or not isinstance(data["api_status"], str)
        or re.fullmatch(r"[a-z][a-z_]{0,39}", data["api_status"]) is None
    ):
        raise ValueError("invalid_observation_data")
    observations = data["observations"]
    if not isinstance(observations, dict) or not 1 <= len(observations) <= 64:
        raise ValueError("invalid_observation_fields")
    for name, item in observations.items():
        if (
            not logical_id(name)
            or not isinstance(item, dict)
            or set(item)
            != {
                "category",
                "value_type",
                "source_property",
                "value",
                "valid",
                "status",
                "measured_at",
                "unit",
                "unit_verified",
                "meaning_verified",
                "measurement_freshness",
            }
        ):
            raise ValueError("invalid_field")
        if (
            item["category"] not in ("measurements", "device_state", "maintenance")
            or not isinstance(item["source_property"], str)
            or re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{0,39}\.[A-Za-z][A-Za-z0-9_]{0,39}",
                item["source_property"],
            )
            is None
            or type(item["valid"]) is not bool
            or not isinstance(item["status"], str)
            or re.fullmatch(r"[a-z][a-z_]{0,39}", item["status"]) is None
            or item["measured_at"] is not None
            or item["unit"] is not None
            or item["unit_verified"] is not False
            or item["meaning_verified"] is not False
            or item["measurement_freshness"] != "unknown"
        ):
            raise ValueError("invalid_field_metadata")
        value = item["value"]
        numeric, enum = (
            finite(value),
            (
                isinstance(value, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", value) is not None
            ),
        )
        if item["value_type"] not in ("number", "enum", "boolean", "number_or_enum"):
            raise ValueError("invalid_value_type")
        safe_value = {
            "number": numeric,
            "enum": enum,
            "boolean": type(value) is bool,
            "number_or_enum": numeric or enum,
        }[item["value_type"]]
        if item["valid"]:
            if (
                not safe_value
                or item["status"] != "ok"
                or data["api_status"] != "ok"
                or api_time is None
                or expiry > api_time + MAX_TTL
            ):
                raise ValueError("invalid_current_value")
        elif value is not None or item["status"] == "ok":
            raise ValueError("invalid_failed_value")
    return body


class ApplianceCache:
    """No env-slot overwrite. No disk/network dependencies or replay persistence.

    A startup barrier rejects all publications/API receipts predating this Hub
    instance. Thus restarts begin unavailable and require a NEW post-start poll.
    Requires synchronized wall clocks; future publications fail closed. Local TTL
    uses monotonic time and never revives on a wall-clock rollback.
    """

    def __init__(
        self,
        *,
        wall_clock=time.time,
        monotonic_clock=time.monotonic,
        max_devices=32,
        max_boots=32,
    ):
        self._wall, self._mono = wall_clock, monotonic_clock
        self.started_at = wall_clock()
        self._max_devices, self._max_boots = max_devices, max_boots
        self._entries = {}
        self._lock = threading.Lock()

    def ingest(self, topic, payload, *, received=None, retained=False):
        now = self._wall() if received is None else received
        try:
            if retained:
                return False
            body = validate(topic, payload, now)
            if body["ts"] <= self.started_at or (
                body["api_received_at"] is not None
                and body["api_received_at"] <= self.started_at
            ):
                return False
            mono = self._mono()
            key = (body["node"], body["logical_device_id"])
            with self._lock:
                old = self._entries.get(key)
                if old:
                    if now < old["received"] or mono < old["mono"]:
                        return False
                    if body["boot_id"] == old["boot"]:
                        if body["seq"] <= old["seq"] or body["ts"] < old["issued"]:
                            return False
                    elif (
                        body["boot_id"] in old["retired"]
                        or body["ts"] <= old["issued"]
                        or len(old["retired"]) >= self._max_boots
                    ):
                        return False
                    api = body["api_received_at"]
                    if api is not None and old["api"] is not None and api <= old["api"]:
                        return False
                elif len(self._entries) >= self._max_devices:
                    return False
                if old:
                    fields = body["data"]["observations"]
                    for name, previous in old["body"]["data"]["observations"].items():
                        if name not in fields:
                            fields[name] = dict(
                                previous, value=None, valid=False, status="missing"
                            )
                    if len(fields) > 64:
                        return False
                good = copy.deepcopy(old["good"]) if old else {}
                for name, field in body["data"]["observations"].items():
                    if field["valid"]:
                        good[name] = {
                            "value": field["value"],
                            "api_received_at": body["api_received_at"],
                            "hub_received_at": now,
                            "mono": mono,
                            "age_at_receipt": now - body["api_received_at"],
                        }
                retired = set(old["retired"]) if old else set()
                if old and body["boot_id"] != old["boot"]:
                    retired.add(old["boot"])
                self._entries[key] = {
                    "body": body,
                    "boot": body["boot_id"],
                    "seq": body["seq"],
                    "issued": body["ts"],
                    "api": body["api_received_at"]
                    if body["api_received_at"] is not None
                    else (old["api"] if old else None),
                    "received": now,
                    "mono": mono,
                    "deadline": mono + body["expires_at"] - now,
                    "retired": retired,
                    "good": good,
                    "invalidated": False,
                }
            return True
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            return False

    def snapshot(self):
        now, mono = self._wall(), self._mono()
        with self._lock:
            result = {}
            for key, entry in self._entries.items():
                body = entry["body"]
                expired = (
                    entry["invalidated"]
                    or mono < entry["mono"]
                    or now < entry["received"]
                    or mono >= entry["deadline"]
                    or now >= body["expires_at"]
                )
                if expired:
                    entry["invalidated"] = True
                data = copy.deepcopy(body["data"])
                for name, field in data["observations"].items():
                    if expired:
                        field.update(value=None, valid=False, status="stale")
                    previous = entry["good"].get(name)
                    field["last_good"] = (
                        {
                            k: v
                            for k, v in previous.items()
                            if k not in ("mono", "age_at_receipt")
                        }
                        | {
                            "api_age_sec": max(
                                0, previous["age_at_receipt"] + mono - previous["mono"]
                            )
                        }
                        if previous
                        else None
                    )
                result[key] = {
                    "node": key[0],
                    "logical_device_id": key[1],
                    "boot_id": entry["boot"],
                    "seq": entry["seq"],
                    "published_at": body["ts"],
                    "expires_at": body["expires_at"],
                    "api_received_at": body["api_received_at"],
                    "hub_received_at": entry["received"],
                    "receipt_stale": expired,
                    "available": not expired
                    and any(f["valid"] for f in data["observations"].values()),
                    **data,
                }
            return result
