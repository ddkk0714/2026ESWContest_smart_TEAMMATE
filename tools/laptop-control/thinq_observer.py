"""Strict air-purifier command observation only. No SDK, POST, or result writer."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from collections import OrderedDict

CMD_TOPIC = "deskmate/control/cmd"
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class PhaseFeed:
    """Local receipt age of fresh FSM phase messages; no presence inference."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.received, self.envelope = clock, None, None
        self.retired = set()

    def observe(self, topic, payload, *, retained=False):
        if topic != "deskmate/state/phase" or retained:
            return
        try:
            env = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
            d = env["data"]
            if (env.get("node") != "hub" or env.get("schema_version") != "1.0"
                    or not isinstance(env.get("boot_id"), str) or not ID.fullmatch(env["boot_id"])
                    or type(env.get("seq")) is not int or env["seq"] < 0
                    or type(d.get("present")) is not bool or not isinstance(d.get("fsm_state"), str)
                    or not re.fullmatch(r"[A-Z_]{1,32}", d["fsm_state"])):
                return
            if env["boot_id"] in self.retired:
                return
            old = self.envelope
            if old:
                if env["boot_id"] == old["boot_id"] and env["seq"] <= old["seq"]:
                    return
                if env["boot_id"] != old["boot_id"]:
                    if len(self.retired) >= 16:
                        return
                    self.retired.add(old["boot_id"])
            reasons = d.get("reasons")
            self.envelope = {"schema_version": "1.0", "node": "hub", "boot_id": env["boot_id"], "seq": env["seq"],
                             "data": {"present": d["present"], "fsm_state": d["fsm_state"],
                                      "reasons": ["absent_timeout"] if isinstance(reasons, list) and "absent_timeout" in reasons else []}}
            self.received = self.clock()
        except (ValueError, KeyError, TypeError, AttributeError):
            return

    def view(self):
        if self.envelope is None:
            return None
        return {"envelope": copy.deepcopy(self.envelope), "age_sec": self.clock() - self.received, "retained": False}


class CommandObserver:
    def __init__(self, *, clock=time.time, max_commands=128):
        self.clock, self.max_commands = clock, max_commands
        self.commands = OrderedDict()
        self.last_clock = clock()
        self.clock_invalid = False

    def observe(self, topic, payload, *, retained=False):
        if topic != CMD_TOPIC:
            return None
        now = self.clock()
        self.clock_invalid |= now < self.last_clock
        self.last_clock = max(now, self.last_clock)
        try:
            if isinstance(payload, (bytes, str)):
                if len(payload) > 8192:
                    return None
                body = json.loads(payload)
            else:
                body = payload
            command = body.get("data") if isinstance(body, dict) else None
            if (
                not isinstance(command, dict)
                or command.get("target_id") != "air_purifier"
            ):
                return None
            cid = command.get("command_id")
            if not isinstance(cid, str) or ID.fullmatch(cid) is None:
                return None
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if cid in self.commands:
                item = self.commands[cid]
                if digest != item["_digest"]:
                    item["reason"] = "conflicting_command_id"
                item["duplicates"] += 1
                return self._public(item)
            if len(self.commands) >= self.max_commands:
                return None
            reason = "approval_unverified_observe_only"
            ts, expiry = body.get("ts"), command.get("expires_ts_ms")

            def number(value):
                return type(value) in (int, float) and math.isfinite(value)

            if retained:
                reason = "retained_rejected"
            elif self.clock_invalid:
                reason = "clock_regressed"
            elif body.get("schema_version") != "1.0" or body.get("node") != "hub":
                reason = "invalid_envelope"
            elif (
                not number(ts) or not number(expiry) or ts > now or expiry / 1000 <= ts
            ):
                reason = "invalid_or_unsynchronized_time"
            elif now * 1000 >= expiry:
                reason = "expired"
            elif command.get("operation") != "set_mode" or command.get("value") not in (
                "low",
                "auto",
            ):
                reason = "unsupported_operation"
            elif command.get("gate") != "suggest":
                reason = "automatic_or_undo_not_enabled"
            elif type(command.get("requires_confirmation")) is not bool:
                reason = "invalid_gate_contract"
            item = {
                "command_id": cid,
                "target_id": "air_purifier",
                "operation": "set_mode"
                if command.get("operation") == "set_mode"
                else "unsupported",
                "value": command.get("value")
                if command.get("value") in ("low", "auto")
                else None,
                "expires_ts_ms": expiry if number(expiry) else None,
                "reason": reason,
                "status": "observed_only",
                "duplicates": 0,
                "post_attempted": False,
                "approval_verified": False,
                "result_published": False,
                "_digest": digest,
            }
            self.commands[cid] = item
            return self._public(item)
        except (TypeError, ValueError, OverflowError):
            return None

    def _public(self, item):
        result = {k: copy.deepcopy(v) for k, v in item.items() if not k.startswith("_")}
        expiry = result["expires_ts_ms"]
        result["expired"] = expiry is None or self.clock() * 1000 >= expiry
        return result

    def view(self):
        return {
            "mode": "observe_only",
            "post_enabled": False,
            "commands": [self._public(v) for v in reversed(self.commands.values())],
        }
