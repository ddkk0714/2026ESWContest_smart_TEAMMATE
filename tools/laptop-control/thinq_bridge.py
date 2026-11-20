"""Sanitized read snapshot -> existing ApplianceCache envelope. No SDK or sockets.

The owner supplies already profile-validated Session fields. Repeated UI reads
never change sequence, API receipt, or expiry. Export is not MQTT publication.
"""

from __future__ import annotations

import copy
import json
import math
import re
import time
import uuid

from thinq_vendor.appliance_contract import topic_for, validate

KINDS = {
    "number": "number",
    "percent": "number",
    "level": "number_or_enum",
    "power": "enum",
    "mode": "enum",
}


def finite(v):
    return type(v) in (int, float) and math.isfinite(v)


class ReadSnapshot:
    def __init__(
        self, fields, *, mode="live", ttl=120, wall=time.time, monotonic=time.monotonic
    ):
        if mode not in ("fixture", "live") or not finite(ttl) or not 0 < ttl <= 300:
            raise ValueError("invalid_snapshot_configuration")
        self.fields, self.mode, self.ttl = fields, mode, ttl
        self.wall, self.mono = wall, monotonic
        self.boot, self.seq = uuid.uuid4().hex, 0
        self.api_received = None
        self._api_highwater = None
        self.updated_mono = None
        self.api_status = "unavailable"
        self.current, self.good = {}, {}
        self._last_wall = wall()
        self._clock_invalid = False
        self._issued = self._last_wall

    def _timed_out(self):
        now = self.wall()
        if now < self._last_wall:
            self._clock_invalid = True
        self._last_wall = max(now, self._last_wall)
        age = None if self.updated_mono is None else self.mono() - self.updated_mono
        return self._clock_invalid or age is None or age < 0 or age >= self.ttl

    def update(self, reply):
        self._timed_out()
        has_read = (
            isinstance(reply, dict)
            and isinstance(reply.get("fields"), dict)
            and finite(reply.get("received_at"))
        )
        received = reply.get("received_at") if has_read else None
        if has_read and (received > self.wall() or self._clock_invalid):
            has_read = False
        # Re-rendering a cached reply is not another API receipt.
        if (
            has_read
            and self._api_highwater is not None
            and received <= self._api_highwater
        ):
            return
        self.seq += 1
        self.api_received = received if has_read else None
        if has_read:
            self._api_highwater = received
        self.updated_mono = self.mono()
        self._issued = self.wall()
        self.api_status = "ok" if has_read else "api_error"
        self.current = {}
        for name, spec in self.fields.items():
            field = reply["fields"].get(name, {}) if has_read else {}
            if not isinstance(field, dict):
                field = {}
            vt = KINDS[spec.kind]
            value = field.get("value") if isinstance(field, dict) else None
            scalar = (
                finite(value)
                if vt == "number"
                else isinstance(value, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", value) is not None
            )
            if vt == "number_or_enum":
                scalar = finite(value) or scalar
            valid = (
                has_read
                and field.get("valid") is True
                and field.get("status") == "ok"
                and scalar
            )
            status = (
                field.get("status", "missing") if isinstance(field, dict) else "missing"
            )
            if (
                not isinstance(status, str)
                or re.fullmatch(r"[a-z][a-z_]{0,39}", status) is None
            ):
                status = "invalid_value"
            if not valid and status == "ok":
                status = "invalid_value"
            self.current[name] = self._field(
                spec.group,
                vt,
                spec.resource + "." + spec.property,
                value if valid else None,
                bool(valid),
                "ok" if valid else status,
            )
        for name, field in self.current.items():
            if field["valid"]:
                self.good[name] = {"value": field["value"], "received_at": received}

    @staticmethod
    def _field(category, vt, prop, value, valid, status):
        return {
            "category": category,
            "value_type": vt,
            "source_property": prop,
            "value": value,
            "valid": valid,
            "status": status,
            "unit": None,
            "unit_verified": False,
            "meaning_verified": False,
            "measured_at": None,
            "measurement_freshness": "unknown",
        }

    def view(self):
        stale = self._timed_out()
        fields = copy.deepcopy(self.current)
        for name, field in fields.items():
            if stale or self.api_status != "ok":
                field.update(
                    value=None, valid=False, status="stale" if stale else "api_error"
                )
            field["last_good"] = copy.deepcopy(self.good.get(name))
        return {
            "source": "lg_thinq_cloud",
            "mode": self.mode,
            "api_status": self.api_status,
            "connection": "unknown",
            "receipt_stale": stale,
            "received_at": self.api_received,
            "last_successful_received_at": self._api_highwater,
            "api_age_sec": None
            if self.updated_mono is None
            else max(0, self.mono() - self.updated_mono),
            "expires_at": None
            if self.api_received is None
            else self.api_received + self.ttl,
            "measured_at": None,
            "measurement_freshness": "unknown",
            "fields": fields,
        }

    def export(self):
        view = self.view()
        if not self.current or view["receipt_stale"]:
            raise ValueError("no_fresh_observation")
        topic = topic_for("thinq-reader", "purifier-one")
        issued = self._issued
        body = {
            "schema_name": "appliance_observation",
            "schema_version": "1.0",
            "node": "thinq-reader",
            "logical_device_id": "purifier-one",
            "boot_id": self.boot,
            "seq": self.seq,
            "ts": issued,
            "expires_at": min(issued + self.ttl, self.api_received + self.ttl)
            if self.api_received is not None
            else issued + self.ttl,
            "api_received_at": self.api_received,
            "data": {
                "mode": self.mode,
                "api_status": self.api_status,
                "connection": "unknown",
                "observations": copy.deepcopy(self.current),
            },
        }
        payload = json.dumps(body, allow_nan=False).encode()
        validate(topic, payload, self.wall())
        return {
            "topic": topic,
            "envelope": body,
            "qos": 0,
            "retain": False,
            "published": False,
            "hub_fsm_enabled": False,
        }
