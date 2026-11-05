"""Token-free worker session; native owns credentials, mode authorization and HTTPS."""

import asyncio
import copy
import os
import time

from tools.thinq_connect import probe
from tools.thinq_connect.air_quality import FIELDS, FieldSpec, _validate
from tools.thinq_connect.manual_trial import (
    ApiFailure,
    DeviceIO,
    StopTrial,
    config,
    invariant,
    profile_check,
    public_state,
    power_config,
    power_profile_check,
)


# Laptop display fields use the same Profile-based validator, never state-only guesses.
OBSERVATION_FIELDS = dict(FIELDS,
    job_mode=FieldSpec("device_state", "airPurifierJobMode", "currentJobMode", "mode"),
    wind_strength=FieldSpec("device_state", "airFlow", "windStrength", "mode"))

class SafeApiFailure(ApiFailure):
    def __init__(self, http_status=None, code=None, category="api_or_network_error"):
        super().__init__(http_status, code)
        self.category = (
            category
            if category
            in {
                "network_timeout",
                "network_or_tls_error",
                "invalid_response",
                "transport_unavailable",
                "api_error",
                "authorization_expired_or_revoked",
            }
            else "api_or_network_error"
        )


# StopTrial reasons the UI may show. Anything else stays the generic blocked status.
PUBLIC_REASONS = frozenset({
    "post_disabled_or_outcome_uncertain", "policy_handoff_required", "stage_forbidden", "power_control_disabled",
    "previous_physical_confirmation_required", "already_in_requested_state", "baseline_changed",
    "restore_not_authorized", "approval_invalid_or_expired", "external_change_detected",
    "approval_or_session_expired_before_post", "policy_changed_or_live_post_blocked", "too_soon_after_previous_post",
    "reconcile_requires_uncertain_outcome", "reconcile_state_unreadable", "reconcile_not_approved",
    "target_changed_between_calls", "read_only_operation_blocked", "unsupported_operation",
})


def error_category(error):
    if error.http_status == 401:
        return "authentication_error"
    if error.http_status == 403:
        return "authorization_error"
    if error.http_status == 429:
        return "rate_limited"
    if error.code == "1222":
        return "device_offline"
    return getattr(error, "category", "api_or_network_error")


class Permit:
    def __init__(self, enabled, power_control=False):
        self.enabled = enabled
        self.power_control = power_control
        self.target = None
        self.value = None
        self.policy_auto = False

    def select(self, target):
        if self.target is not None:
            raise StopTrial("target_changed")
        self.target = target

    def approve(self, value):
        allowed = ("LOW", "AUTO", "POWER_ON", "POWER_OFF") if self.power_control else ("LOW", "AUTO")
        if not self.enabled or value not in allowed or self.value is not None:
            raise StopTrial("post_disabled")
        self.value = value


class Session:
    """intent_control (unified console / policy link): every prepare is a new user intent with its own nonce and
    at most one POST. Repeating a value later is a new intent, not a resend. An uncertain outcome still locks all
    new intents until a separate fresh GET is reviewed (`reconcile`); nothing is ever resent automatically.
    Without intent_control the original one-shot trial stages (LOW then AUTO restore) are unchanged."""

    READBACK_DELAYS = (1.5, 1.5, 2.0)   # extra GETs after a mismatched readback; never another POST
    MIN_POST_INTERVAL = 5.0

    def __init__(self, sdk, context=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.sdk, self.clock, self.sleep = sdk, clock, sleep
        self.authorization_valid = lambda: True
        self.before_post = lambda challenge, fresh: challenge.get("policy") is None
        self.policy_approval = None  # Host-only handoff; never accepted from JSON.
        self.ctx = copy.deepcopy(context or {})
        self.ctx.setdefault("enabled", False)
        self.ctx.setdefault("attempted", [])
        self.ctx.setdefault("uncertain", False)
        self.ctx.setdefault("wind", "AUTO")
        self.ctx.setdefault("intent_control", False)
        self.ctx.setdefault("control_capable", self.ctx["enabled"])
        self.permit = Permit(self.ctx["enabled"], self.ctx.get("power_control") is True)
        self.io = DeviceIO(sdk, self.permit, probe)
        sdk.permit = self.permit

    async def read(self):
        if self.io.target is None:
            await self.io.select()
        if "private_target" not in self.ctx:
            self.ctx["private_target"] = self.io.target
        elif self.ctx["private_target"] != self.io.target:
            raise StopTrial("target_changed_between_calls")
        profile = await self.io.profile()
        state = await self.io.state()
        # Parsing is reused verbatim; unknown units and measurement time stay unknown.
        fields = {}
        for name, spec in OBSERVATION_FIELDS.items():
            value, status = _validate(spec, profile, state)
            fields[name] = {
                "value": value,
                "valid": status == "ok",
                "status": status,
                "unit": None,
                "measured_at": None,
            }
        self.latest_profile, self.latest_state = profile, state
        try:
            profile_check(profile)
            wind_profile_verified = True
        except StopTrial:
            wind_profile_verified = False
        try:
            power_profile_check(profile)
            power_profile_verified = True
        except StopTrial:
            power_profile_verified = False
        result = {
            "status": "read_ok",
            "fields": fields,
            "received_at": time.time(),
            "measured_at": None,
            "connection": "unknown",
            "pm2_meaning": "unverified",
            "wind_profile_verified": wind_profile_verified,
            "power_profile_verified": power_profile_verified,
        }
        try:
            summary = public_state(state)
            allowed = {
                "power": ("POWER_ON", "POWER_OFF"),
                "job": ("CLEAN", "SLEEP"),
                "windStrength": ("LOW", "MID", "HIGH", "POWER", "AUTO"),
                "windStrengthDetail": (
                    None,
                    "OFF",
                    "LOW",
                    "MID",
                    "HIGH",
                    "POWER",
                    "AUTO",
                ),
            }
            result["state"] = (
                summary
                if all(summary[k] in values for k, values in allowed.items())
                else None
            )
        except (KeyError, TypeError, StopTrial):
            result["state"] = None
        # Powered-off devices may omit job/wind. Never invent those fields.
        if self.ctx.get("power_control") is True and result["state"] is None:
            try:
                current = power_config(state)
                result["state"] = {"power": current["operation"]["airPurifierOperationMode"],
                                   "job": None, "windStrength": None, "windStrengthDetail": None}
            except StopTrial:
                pass
        return result

    async def handle(self, request):
        backend = request.get("backend", "fixture")
        if self.ctx.get("backend", backend) != backend:
            self.ctx.pop("challenge", None)
            self.ctx["enabled"] = False
            self.ctx["uncertain"] = True
            raise StopTrial("backend_changed_requires_new_session")
        self.ctx["backend"] = backend
        if backend not in ("fixture", "live_read_only", "live_manual"):
            raise StopTrial("invalid_backend")
        op = request.get("op")
        allowed = {
            "op",
            "backend",
            "enabled",
            "value",
            "nonce",
            "approved",
            "physical_online_confirmed",
            "no_external_change",
            "auth_session_id",
        }
        if op == "reconcile" and set(request) - {"op", "backend", "approved"}:
            raise StopTrial("unexpected_request_fields")
        if set(request) - allowed:
            raise StopTrial("unexpected_request_fields")
        if backend == "live_read_only" and op != "read":
            raise StopTrial("read_only_operation_blocked")
        if op == "enable_fixture_post":
            if request.get("backend", "fixture") != "fixture":
                raise StopTrial("live_arming_forbidden")
            self.ctx["enabled"] = request.get("enabled") is True
            self.ctx.pop("challenge", None)
            return {
                "status": "fixture_post_enabled"
                if self.ctx["enabled"]
                else "post_disabled"
            }
        if op == "read":
            return await self.read()
        if op == "physical_confirmation":
            if request.get("approved") is not True or self.ctx.get(
                "verified"
            ) != request.get("value"):
                raise StopTrial("physical_confirmation_unavailable")
            self.ctx["physical"] = request["value"]
            return {
                "status": "user_attestation_recorded",
                "physical_confirmed": backend == "live_manual",
                "physical_evidence": "user_attestation"
                if backend == "live_manual"
                else "fixture_user_attestation_only",
            }
        if op == "reconcile":
            return await self.reconcile(request)
        if op not in ("prepare", "execute"):
            raise StopTrial("unsupported_operation")
        if not self.ctx["enabled"] or self.ctx["uncertain"]:
            raise StopTrial("post_disabled_or_outcome_uncertain")
        intents = self.ctx.get("intent_control") is True
        if op == "prepare":
            self.ctx.pop("challenge", None)
            value = request.get("value")
            if self.ctx.get("policy_link") and self.policy_approval is None and self.ctx.get("combined_manual") is not True:
                raise StopTrial("policy_handoff_required")
            power = value in ("POWER_ON", "POWER_OFF")
            if value not in ("LOW", "AUTO", "POWER_ON", "POWER_OFF") or (not intents and value in self.ctx["attempted"]):
                raise StopTrial("stage_forbidden")
            if power and self.ctx.get("power_control") is not True:
                raise StopTrial("power_control_disabled")
            last = self.ctx.get("last_post_at")
            if intents and last is not None and self.clock() - last < self.MIN_POST_INTERVAL:
                raise StopTrial("too_soon_after_previous_post")
            previous = self.ctx.get("verified")
            # The user's body check is recorded evidence. With intents it no longer gates the next intent:
            # the fresh Profile/State baseline check below already proves the device is where we think.
            if (not intents and (self.policy_approval is not None or power or previous in ("POWER_ON", "POWER_OFF"))
                    and previous and self.ctx.get("physical") != previous):
                raise StopTrial("previous_physical_confirmation_required")
            await self.read()
            if power:
                power_profile_check(self.latest_profile)
                baseline = power_config(self.latest_state)
                if baseline["operation"]["airPurifierOperationMode"] == value:
                    raise StopTrial("already_in_requested_state")
            else:
                profile_check(self.latest_profile)
                baseline = config(self.latest_state)
                expected = "AUTO" if value == "LOW" else "LOW"
                if self.latest_state["airFlow"]["windStrength"] != expected:
                    raise StopTrial("baseline_changed")
            if value == "AUTO" and self.policy_approval is None and not intents and (
                self.ctx.get("verified") != "LOW"
                or self.ctx.get("physical") != "LOW"
                or baseline != self.ctx.get("low_config")
            ):
                raise StopTrial("restore_not_authorized")
            nonce = os.urandom(24).hex()
            self.ctx["challenge"] = {
                "nonce": nonce,
                "value": value,
                "baseline": baseline,
                "deadline": self.clock() + 30,
                "created": self.clock(),
                "policy": copy.deepcopy(self.policy_approval),
            }
            return {
                "status": "approval_required",
                "nonce": nonce,
                "value": value,
                "approval_ttl_seconds": 30,
                "physical_confirmed": False,
            }
        challenge = self.ctx.pop("challenge", None)  # consume before any await or POST
        if (
            not challenge
            or request.get("nonce") != challenge["nonce"]
            or request.get("approved") is not True
            or request.get("physical_online_confirmed") is not True
            or request.get("no_external_change") is not True
            or not challenge["created"] <= self.clock() < challenge["deadline"]
        ):
            raise StopTrial("approval_invalid_or_expired")
        value = challenge["value"]
        power = value in ("POWER_ON", "POWER_OFF")
        if power and self.ctx.get("power_control") is not True:
            raise StopTrial("power_control_disabled")
        fresh = await self.read()  # Fresh profile AND state immediately before control.
        (power_profile_check if power else profile_check)(self.latest_profile)
        if (power_config if power else config)(self.latest_state) != challenge["baseline"]:
            raise StopTrial("external_change_detected")
        # A fresh GET can take longer than the approval/session lifetime.
        if not challenge["created"] <= self.clock() < challenge["deadline"] or not self.authorization_valid():
            raise StopTrial("approval_or_session_expired_before_post")
        if not self.before_post(challenge, fresh):
            raise StopTrial("policy_changed_or_live_post_blocked")
        self.ctx["attempted"].append(value)  # never replay after timeout/response loss
        self.ctx["last_post_at"] = self.clock()
        self.permit.enabled = True
        self.permit.policy_auto = bool(challenge.get("policy") and value == "AUTO")
        accepted = False
        failure = None
        try:
            await self.io.post(value)
            accepted = True
        except ApiFailure as error:
            self.ctx["uncertain"] = True
            failure = {
                "http_status": error.http_status,
                "code": error.code,
                "category": error_category(error),
            }
        # Separate GET even when the POST response was lost. Cloud state can lag the device, so with intents a
        # mismatch is re-read a few times (GET only). The POST itself is never repeated.
        delays = (0,) + (self.READBACK_DELAYS if self.ctx.get("intent_control") is True and accepted else ())
        matched, current, readback = False, None, {}
        for delay in delays:
            if delay:
                await self.sleep(delay)
            try:
                readback = await self.read()
                current = (power_config if power else config)(self.latest_state)
                if power:
                    # Confirm power only. ON never writes AUTO or assumes a restored mode.
                    matched = current["operation"]["airPurifierOperationMode"] == value
                else:
                    same_invariants = invariant(current) == invariant(challenge["baseline"])
                    matched = current["airFlow"]["windStrength"] == value and same_invariants
            except (ApiFailure, StopTrial):
                matched, current, readback = False, None, {}
            if matched:
                break
        if not accepted or not matched:
            self.ctx["uncertain"] = True
        if accepted and matched:
            self.ctx["verified"] = value
            if value == "LOW":
                self.ctx["low_config"] = current
        return {
            "status": "state_match" if accepted and matched else "outcome_unconfirmed",
            "api_accepted": accepted,
            "state_match": matched,
            "physical_confirmed": False,
            "automatic_resend": False,
            "value": value,
            "error": failure,
            "state": readback.get("state"),
            "fields": readback.get("fields", {}),
            "received_at": readback.get("received_at"),
            "measured_at": None,
        }

    async def reconcile(self, request):
        """After an uncertain outcome: one fresh GET the user has reviewed becomes the new known state.
        It unlocks new intents only; the uncertain command is not resent and its record is kept."""
        if self.ctx.get("intent_control") is not True or not self.ctx["uncertain"]:
            raise StopTrial("reconcile_requires_uncertain_outcome")
        if request.get("approved") is not True:
            raise StopTrial("reconcile_not_approved")
        self.ctx.pop("challenge", None)
        fresh = await self.read()
        state = fresh.get("state")
        if not isinstance(state, dict) or state.get("power") not in ("POWER_ON", "POWER_OFF"):
            raise StopTrial("reconcile_state_unreadable")
        self.ctx["uncertain"] = False
        self.ctx["enabled"] = self.ctx["control_capable"] is True
        self.ctx["verified"] = None
        self.ctx.setdefault("reconciled", []).append(state.get("power"))
        return {**fresh, "status": "reconciled", "physical_confirmed": False, "automatic_resend": False}

    async def safe(self, request):
        try:
            public = await self.handle(request)
        except ApiFailure as error:
            self.ctx.pop("challenge", None)
            public = {
                "status": "api_error",
                "http_status": error.http_status,
                "code": error.code,
                "category": error_category(error),
                "physical_confirmed": False,
            }
        except StopTrial as stop:
            # Fixed vocabulary only; no arbitrary exceptions or response bodies.
            public = {"status": "blocked", "error": "precondition_or_approval_failed"}
            reason = stop.args[0] if stop.args else None
            if reason in PUBLIC_REASONS:
                public["reason"] = reason
        except Exception:
            self.ctx.pop("challenge", None)
            public = {"status": "error", "error": "safe_internal_error"}
        public.update(
            backend=request.get("backend", "fixture"),
            post_enabled=self.ctx["enabled"]
            and not self.ctx["uncertain"]
            and request.get("backend", "fixture") in ("fixture", "live_manual"),
            automatic_control=False,
        )
        if hasattr(self.sdk, "wind"):
            self.ctx["wind"] = self.sdk.wind
        return {"public": public, "context": self.ctx}
