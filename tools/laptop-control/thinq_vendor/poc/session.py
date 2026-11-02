"""Token-free worker session; native owns credentials, mode authorization and HTTPS."""

import copy
import os
import time

from tools.thinq_connect import probe
from tools.thinq_connect.air_quality import FIELDS, _validate
from tools.thinq_connect.manual_trial import (
    ApiFailure,
    DeviceIO,
    StopTrial,
    config,
    invariant,
    profile_check,
    public_state,
)


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
    def __init__(self, enabled):
        self.enabled = enabled
        self.target = None
        self.value = None

    def select(self, target):
        if self.target is not None:
            raise StopTrial("target_changed")
        self.target = target

    def approve(self, value):
        if not self.enabled or value not in ("LOW", "AUTO") or self.value is not None:
            raise StopTrial("post_disabled")
        self.value = value


class Session:
    def __init__(self, sdk, context=None, clock=time.monotonic):
        self.sdk, self.clock = sdk, clock
        self.ctx = copy.deepcopy(context or {})
        self.ctx.setdefault("enabled", False)
        self.ctx.setdefault("attempted", [])
        self.ctx.setdefault("uncertain", False)
        self.ctx.setdefault("wind", "AUTO")
        self.permit = Permit(self.ctx["enabled"])
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
        for name, spec in FIELDS.items():
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
        result = {
            "status": "read_ok",
            "fields": fields,
            "received_at": time.time(),
            "measured_at": None,
            "connection": "unknown",
            "pm2_meaning": "unverified",
            "wind_profile_verified": wind_profile_verified,
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
        if op not in ("prepare", "execute"):
            raise StopTrial("unsupported_operation")
        if not self.ctx["enabled"] or self.ctx["uncertain"]:
            raise StopTrial("post_disabled_or_outcome_uncertain")
        if op == "prepare":
            value = request.get("value")
            if value not in ("LOW", "AUTO") or value in self.ctx["attempted"]:
                raise StopTrial("stage_forbidden")
            await self.read()
            profile_check(self.latest_profile)
            baseline = config(self.latest_state)
            expected = "AUTO" if value == "LOW" else "LOW"
            if self.latest_state["airFlow"]["windStrength"] != expected:
                raise StopTrial("baseline_changed")
            if value == "AUTO" and (
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
        await self.read()  # Fresh profile AND state immediately before control.
        profile_check(self.latest_profile)
        if config(self.latest_state) != challenge["baseline"]:
            raise StopTrial("external_change_detected")
        self.ctx["attempted"].append(value)  # never replay after timeout/response loss
        self.permit.enabled = True
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
        try:
            readback = (
                await self.read()
            )  # separate GET even when POST response was lost
            current = config(self.latest_state)
            same_invariants = invariant(current) == invariant(challenge["baseline"])
            matched = current["airFlow"]["windStrength"] == value and same_invariants
        except (ApiFailure, StopTrial):
            matched = False
            current = None
            readback = {}
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
        except StopTrial:
            # Fixed vocabulary only; no arbitrary exceptions or response bodies.
            public = {"status": "blocked", "error": "precondition_or_approval_failed"}
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
