"""V1.1 shadow policy, independent of FSM/Dispatcher and all I/O.

Consumes existing FSM START/IDLE boundaries and frame.present as published by
state/phase. Responses are Mock only: no Gateway, MQTT or Cloud calls exist here.
Config is JSON-compatible YAML so restricted Python needs no extra dependency.
"""
from __future__ import annotations

import copy
import math
import time
import uuid

FSM_STATES = {"IDLE", "START", "END", "CONTEXT_DETECT", "FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC",
              "FOCUS_BREAK", "FATIGUE_SUSPECT", "FATIGUE", "CAUSE_ANALYSIS", "MONITOR", "ESCALATE",
              "ACTION_ENV", "ACTION_POSTURE", "ACTION_BREAK", "REST", "RECOVERY"}

# Exact active-state projection from presentation.state_envelope, verified on Pi 4.
ACTIVE_PHASE = {
    **dict.fromkeys(("START", "CONTEXT_DETECT"), "start"),
    **dict.fromkeys(("FOCUS_PC", "FOCUS_MIXED", "FOCUS_NPC", "FOCUS_BREAK"), "focus"),
    **dict.fromkeys(("FATIGUE_SUSPECT", "FATIGUE", "CAUSE_ANALYSIS", "MONITOR", "ESCALATE",
                     "ACTION_ENV", "ACTION_POSTURE", "ACTION_BREAK"), "fatigue"),
    **dict.fromkeys(("REST", "RECOVERY"), "recovery"),
}
STATE_PHASE = {"IDLE": "idle", "END": "end", **ACTIVE_PHASE}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class AirPurifierPolicy:
    def __init__(self, config, *, clock=time.monotonic, manual_review=False, demo_absence=False):
        self.cfg = dict(config)
        self.manual_review = manual_review
        self.demo_absence = demo_absence is True
        if self.demo_absence and (not manual_review or not finite(config.get("demo_absence_off_sec"))
                                 or config["demo_absence_off_sec"] <= 0):
            raise ValueError("demo_absence_requires_manual_review_and_config")
        self.absence_since = None
        self.absence_samples = 0
        self.demo_present_observed = False
        self.hub_demo_speed = None
        self.manual_approval = None
        if config.get("automatic_power") is not False:
            raise ValueError("automatic_power_must_remain_disabled")
        for key in ("presence_on_sec", "pollution_hold_sec", "pollution_max_gap_sec",
                    "hub_max_age_sec", "proposal_ttl_sec", "manual_hold_sec"):
            if not finite(config.get(key)) or config[key] <= 0:
                raise ValueError("invalid_policy_config")
        if config.get("verified_bad_levels") != ["BAD"]:
            raise ValueError("unverified_pollution_grade")
        self.clock = clock
        self.boot = None
        self.seq = -1
        self.retired_boots = set()
        self.hub_at = None
        self.state = None
        self.present = None
        self.session = 0
        self.occupancy = 0   # bumps when our own confirmed OFF ends a stay; the next return may propose ON again
        self.active_session = False
        self.session_open = False
        self.session_origin = None
        self.mqtt_connected = None
        self.mqtt_epoch = None
        self.present_since = None
        self.off_pending = False
        self.snapshot = {}
        self.snapshot_at = clock()
        self.api_received = None
        self.last_bad_at = None
        self.bad_since = None
        self.bad_reads = 0
        self.baseline = None
        self.manual_until = 0
        self.seen = set()
        self.pending = None
        self.result = None
        self.last_clock = clock()
        self.clock_invalid = False

    def _now(self):
        now = self.clock()
        self.clock_invalid |= not finite(now) or now < self.last_clock
        self.last_clock = max(now, self.last_clock)
        return now

    def _field(self, name):
        fields = self.snapshot.get("fields", {})
        f = fields.get(name, {}) if isinstance(fields, dict) else {}
        return f.get("value") if isinstance(f, dict) and f.get("valid") is True and f.get("status") == "ok" else None

    def _clear_absence(self, *, forget_presence=False):
        self.absence_since = None
        self.absence_samples = 0
        if forget_presence:
            self.demo_present_observed = False

    def observe(self, phase, snapshot):
        now = self._now()
        # Duplicate polling cannot renew the original MQTT receipt.
        if isinstance(phase, dict):
            connected, epoch = phase.get("mqtt_connected"), phase.get("mqtt_epoch")
            changed = isinstance(epoch, str) and self.mqtt_epoch is not None and epoch != self.mqtt_epoch
            if type(connected) is bool:
                self.mqtt_connected = connected
            if isinstance(epoch, str):
                self.mqtt_epoch = epoch
            if connected is False or changed:
                self._clear_absence(forget_presence=True)
                self.active_session = False
                self.present_since = None
                self.off_pending = False
            env, age = phase.get("envelope", {}), phase.get("age_sec")
            if isinstance(env, dict):
                data = env.get("data", {})
                boot, seq = env.get("boot_id"), env.get("seq")
                proof = (isinstance(data, dict) and isinstance(data.get("fsm_state"), str) and data.get("source") == "fsm"
                         and data.get("phase") == STATE_PHASE.get(data.get("fsm_state")))
                if self.demo_absence and not proof:
                    self._clear_absence(forget_presence=True)
                if self.session_origin == "attached" and not proof:
                    self.active_session = False
                    self.present_since = None
                    self.off_pending = False
                valid = (env.get("node") == "hub" and env.get("schema_version") == "1.0"
                         and isinstance(boot, str) and 0 < len(boot) <= 128
                         and type(seq) is int and seq >= 0 and isinstance(data, dict)
                         and type(data.get("present")) is bool and isinstance(data.get("fsm_state"), str)
                         and data["fsm_state"] in FSM_STATES
                         and (not ("source" in data or "phase" in data) or proof)
                         and connected is not False
                         and phase.get("retained") is False and finite(age)
                         and 0 <= age < self.cfg["hub_max_age_sec"])
                if valid and boot not in self.retired_boots and (boot != self.boot or seq > self.seq):
                    new_boot = boot != self.boot
                    if boot != self.boot or self.hub_at is None or now - self.hub_at >= self.cfg["hub_max_age_sec"]:
                        self._clear_absence(forget_presence=True)
                        if self.boot is not None and boot != self.boot:
                            self.retired_boots.add(self.boot)
                        self.active_session = False
                        self.present_since = None
                        self.off_pending = False
                        if new_boot:
                            self.session_open = False
                            self.state = None
                    previous = self.state
                    self.boot, self.seq, self.hub_at = boot, seq, now - age
                    self.state, self.present = data["fsm_state"], data["present"]
                    summary = data.get("sensor_summary")
                    demo = summary.get("demo_mode") if isinstance(summary, dict) else None
                    speed = None
                    if isinstance(demo, dict) and type(demo.get("enabled")) is bool:
                        if demo.get("speed") == "normal" or demo.get("speed") == "fast" and demo["enabled"]:
                            speed = demo["speed"]
                    # Hub mode is diagnostic. The explicit laptop demo opt-in
                    # selects the reviewed short threshold without changing Hub.
                    self.hub_demo_speed = speed
                    if self.state == "START" and not self.active_session:
                        if previous != "START" or not self.session_open:
                            self.session += 1
                            self.seen.clear()
                            self.result = None
                        self.active_session = True
                        self.session_open = True
                        self.session_origin = "start_observed"
                        self.off_pending = False
                    if (not self.active_session and data.get("source") == "fsm"
                            and self.state in ACTIVE_PHASE and data.get("phase") == ACTIVE_PHASE[self.state]):
                        if not self.session_open:
                            self.session += 1
                            self.seen.clear()
                            self.result = None
                        self.session_open = self.active_session = True
                        self.session_origin = "attached"
                    if self.active_session and self.present:
                        if self.present_since is None:
                            self.present_since = now
                    else:
                        self.present_since = None
                    if self.state in ("IDLE", "END") and previous != self.state:
                        self.off_pending = (self.active_session and previous not in (None, "IDLE", "END")
                                            and self.state == "IDLE" and not self.present
                                            and isinstance(data.get("reasons"), list) and "absent_timeout" in data["reasons"])
                        self.active_session = False
                        self.session_open = False
                        self.session_origin = None
                        self.present_since = None
                    if self.demo_absence:
                        if not (proof and connected is True and self.active_session):
                            self._clear_absence(forget_presence=True)
                        elif self.present:
                            # Seated again: the next absence is a new OFF case, even if the last OFF proposal expired,
                            # was rejected or was blocked (it stays handled only for that absence). Not tied to the
                            # absence counter, which a viewer reconnect can reset while the person is away.
                            self.seen.discard(self._key("set_power", "off"))
                            self._clear_absence()
                            self.demo_present_observed = True
                        elif self.demo_present_observed:
                            if self.absence_since is None:
                                # Start locally; never credit a cached packet's age.
                                self.absence_since = now
                            self.absence_samples += 1
        self.snapshot = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else {}
        self.snapshot_at = now
        age, receipt, expiry = (self.snapshot.get(k) for k in ("api_age_sec", "received_at", "expires_at"))
        ready = (self.snapshot.get("api_status") == "ok" and self.snapshot.get("receipt_stale") is False
                 and self._connection_reviewable() and all(finite(v) for v in (age, receipt, expiry))
                 and 0 <= age < expiry - receipt)
        if not ready:
            self.bad_since, self.bad_reads, self.last_bad_at = None, 0, None
        received = self.snapshot.get("received_at")
        if finite(received) and (self.api_received is None or received > self.api_received):
            power, wind = self._field("power"), self._field("wind_strength")
            state = (power, wind)
            if power in ("POWER_ON", "POWER_OFF"):
                if self.baseline is not None and (power != self.baseline[0] or
                        isinstance(wind, str) and isinstance(self.baseline[1], str) and wind != self.baseline[1]):
                    self.manual_until = now + self.cfg["manual_hold_sec"]
                    self._clear_absence(forget_presence=True)
                    self.off_pending = False
                    self.seen.add(self._key("set_power", "on"))
                self.baseline = state
            self.api_received = received
            if ready and self._field("total_pollution_level") in self.cfg["verified_bad_levels"]:
                if self.last_bad_at is None or now - self.last_bad_at > self.cfg["pollution_max_gap_sec"]:
                    self.bad_since, self.bad_reads = now, 0
                self.last_bad_at = now
                self.bad_reads += 1
            else:
                self.bad_since, self.bad_reads, self.last_bad_at = None, 0, None
        return self.view()

    def _candidate(self, now):
        if self.clock_invalid:
            self._clear_absence(forget_presence=True)
            return None, "clock_regressed"
        if self.mqtt_connected is False:
            self._clear_absence(forget_presence=True)
            self.present_since = None
            return None, "hub_disconnected"
        if self.hub_at is None or now - self.hub_at >= self.cfg["hub_max_age_sec"]:
            self._clear_absence(forget_presence=True)
            self.present_since = None
            return None, "hub_missing_or_stale"
        s = self.snapshot
        age, received, expiry = s.get("api_age_sec"), s.get("received_at"), s.get("expires_at")
        if finite(age):
            age += max(0, now - self.snapshot_at)
        if (s.get("api_status") != "ok" or s.get("receipt_stale") is not False
                or not all(finite(v) for v in (age, received, expiry)) or not 0 <= age < expiry - received):
            return None, "thinq_missing_or_stale"
        if not self._connection_reviewable():
            return None, "connection_unconfirmed"
        if now < self.manual_until:
            return None, "manual_override"
        power, wind = self._field("power"), self._field("wind_strength")
        if power not in ("POWER_ON", "POWER_OFF"):
            return None, "power_unknown"
        if self.off_pending and self.state == "IDLE" and self.present is False and power == "POWER_ON":
            return ("set_power", "off", "fsm_absent_timeout"), None
        if self.demo_absence and self.active_session and self.present is False and power == "POWER_ON":
            if not self.demo_present_observed:
                return None, "waiting_for_demo_presence_baseline"
            # Only newly accepted MQTT receipts can advance the demo interval.
            # Polling one frozen false frame cannot create a candidate.
            if (self.mqtt_connected is True and self.demo_present_observed and self.absence_since is not None
                    and self.absence_samples >= 2
                    and self.hub_at - self.absence_since >= self.cfg["demo_absence_off_sec"]):
                return ("set_power", "off", "demo_absence_elapsed"), None
            return None, "waiting_for_demo_absence"
        if self.active_session and self.present is True:
            if power == "POWER_OFF" and self.present_since is not None and now - self.present_since >= self.cfg["presence_on_sec"]:
                return ("set_power", "on", "present_after_session_start"), None
            if power == "POWER_ON":
                if wind == "AUTO":
                    return None, "already_auto"
                if wind != "LOW":
                    return None, "wind_unknown_or_unsupported"
                if self._field("total_pollution_level") not in self.cfg["verified_bad_levels"]:
                    return None, "pollution_grade_unverified"
                if (self.bad_reads >= 2 and self.bad_since is not None and now - self.bad_since >= self.cfg["pollution_hold_sec"]
                        and now - self.last_bad_at <= self.cfg["pollution_max_gap_sec"]):
                    return ("set_mode", "auto", "bad_api_reports_persisted"), None
                return None, "waiting_for_api_bad_persistence"
        return None, "waiting_for_fsm_boundary_or_presence"

    def view(self):
        now = self._now()
        candidate, reason = self._candidate(now)
        key = self._key(*candidate[:2]) if candidate else None
        if key in self.seen:
            candidate, reason = None, "already_handled_this_session"
        if self.pending and (candidate is None or self.pending["key"] != key or now >= self.pending["deadline"]):
            self.seen.add(self.pending["key"])
            self.pending = None
            candidate, reason = None, "proposal_invalidated_or_expired"
        if candidate and self.pending is None:
            self.pending = {"proposal_id": uuid.uuid4().hex, "key": key,
                            "operation": candidate[0], "value": candidate[1], "reason": candidate[2],
                            "deadline": now + self.cfg["proposal_ttl_sec"]}
        proposal = None if self.pending is None else {
            k: self.pending[k] for k in ("proposal_id", "operation", "value", "reason")}
        return {"version": "1.1", "execution": "mock_only", "post_enabled": False,
                "automatic_power": False, "present": self.present, "fsm_state": self.state,
                "session_active": bool(self.active_session and self.mqtt_connected is not False and self.hub_at is not None
                                       and 0 <= now - self.hub_at < self.cfg["hub_max_age_sec"]),
                "session_origin": self.session_origin,
                "presence_elapsed_sec": max(0, now - self.present_since) if self.present_since is not None else 0,
                "presence_required_sec": self.cfg["presence_on_sec"],
                "demo_absence_mode": self.demo_absence,
                "hub_demo_speed": self.hub_demo_speed,
                "absence_elapsed_sec": max(0, self.hub_at - self.absence_since)
                    if self.absence_since is not None and self.hub_at is not None else 0,
                "absence_required_sec": self.cfg.get("demo_absence_off_sec") if self.demo_absence else None,
                "pollution_level": self._field("total_pollution_level"),
                "reason": candidate[2] if candidate else reason, "proposal": proposal,
                "result": copy.deepcopy(self.result), "persistence_basis": "api_reports_not_sensor_measurements"}

    def _key(self, operation, value):
        return (self.session, self.occupancy, operation, value)

    def expect(self, value, *, manual=False):
        """A command from this console was confirmed by GET readback. Move the baseline so the next snapshot is not
        taken for an outside manual change (that would hold every proposal for manual_hold_sec).
        Policy OFF ends this stay: the person who sits down next can get a new ON proposal in the same FSM session.
        A manual console OFF is the user's own choice: hold proposals (no ON offer 30 s later) as before."""
        power, wind = self.baseline if self.baseline is not None else (None, None)
        if value in ("POWER_ON", "POWER_OFF"):
            self.baseline = (value, None)   # ON resumes the device's own last mode; we never set or assume it
            if value == "POWER_OFF" and manual:
                self.manual_until = self._now() + self.cfg["manual_hold_sec"]
                self.off_pending = False
            elif value == "POWER_OFF":
                self.occupancy += 1
                self.off_pending = False
        elif value in ("LOW", "AUTO"):
            self.baseline = (power, value)

    def _connection_reviewable(self):
        # Unknown permits a human-review candidate only; never assert online.
        return self.snapshot.get("connection") == "online" or (
            self.manual_review and self.snapshot.get("connection") == "unknown")

    def manual_valid(self, proposal_id):
        p = self.manual_approval
        now = self._now()
        candidate, _ = self._candidate(now)
        return bool(p and p["proposal_id"] == proposal_id and now < p["deadline"]
                    and candidate and self._key(*candidate[:2]) == p["key"])

    def respond(self, proposal_id, decision, *, manual=False):
        view = self.view()  # Recheck freshness, identity, state, manual hold and expiry.
        if decision not in ("accept", "reject") or not view["proposal"] or view["proposal"]["proposal_id"] != proposal_id:
            raise ValueError("proposal_expired_or_invalid")
        p = self.pending
        if manual and not self.manual_review:
            raise ValueError("manual_review_disabled")
        self.manual_approval = copy.deepcopy(p) if manual and decision == "accept" else None
        self.seen.add(p["key"])
        self.pending = None
        self.result = {"proposal_id": proposal_id, "status": ("awaiting_manual_approval" if manual else "mock_succeeded") if decision == "accept" else "rejected",
                       "operation": p["operation"], "value": p["value"], "actual_value": None,
                       "state_confirmed": False, "transport_performed": False, "post_enabled": False}
        return self.view()


def fixture_policy(config, scenario, *, clock=time.monotonic):
    """Explicit synthetic timelines; never modify the real ThinQ snapshot."""
    if scenario not in ("presence_on", "absence_off", "pollution_auto"):
        raise ValueError("unknown_fixture_scenario")
    now = clock()
    duration = config["pollution_hold_sec"] if scenario == "pollution_auto" else config["presence_on_sec"]
    ticks = [now - duration - 1, now]
    cursor = [ticks[0]]
    p = AirPurifierPolicy(config, clock=lambda: cursor[0])
    for i, at in enumerate(ticks):
        cursor[0] = at
        # Intermediate Hub samples prove continuous receipt, not a long gap.
        if i:
            prev = ticks[0]
            while prev + config["hub_max_age_sec"] / 2 < at:
                prev += config["hub_max_age_sec"] / 2
                cursor[0] = prev
                p.observe({"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "fixture", "seq": p.seq + 1,
                          "data": {"fsm_state": "FOCUS_PC", "present": True, "reasons": []}}, "age_sec": 0, "retained": False}, p.snapshot)
            cursor[0] = at
        s = {"mode": "fixture", "api_status": "ok", "receipt_stale": False, "connection": "online",
             "received_at": at, "api_age_sec": 0, "expires_at": at + 120,
             "fields": {k: {"value": v, "valid": True, "status": "ok"} for k, v in {
                 "power": "POWER_OFF" if scenario == "presence_on" else "POWER_ON",
                 "wind_strength": "AUTO" if scenario == "absence_off" else "LOW", "total_pollution_level": "BAD"}.items()}}
        p.observe({"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "fixture", "seq": p.seq + 1,
                  "data": {"fsm_state": "START" if i == 0 else "FOCUS_PC", "present": True, "reasons": []}},
                   "age_sec": 0, "retained": False}, s)
    if scenario == "absence_off":
        p.observe({"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "fixture", "seq": p.seq + 1,
                  "data": {"fsm_state": "IDLE", "present": False, "reasons": ["absent_timeout"]}},
                   "age_sec": 0, "retained": False}, s)
    p.clock = clock
    return p
