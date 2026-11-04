"""Power trials use synthetic SDK/HTTP only; no PAT, Cloud, MQTT or boards."""
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from manual_control import POWER_APPROVAL, run_trial
from thinq_host import GuardedSdk, HostSession, Session
from poc.worker import FixtureSdk
from tools.thinq_connect.manual_trial import StopTrial, Transport, control_payload, power_profile_check

FIXTURE = json.loads((Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text())


class PowerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "fixture", "power_control": True}, clock=lambda: self.now), clock=lambda: self.now)

    async def call(self, op, **data):
        return await self.host.call(self.host.key, {"op": op, **data})

    async def execute(self, value):
        prepared = await self.call("prepare", value=value)
        self.assertEqual(prepared["status"], "approval_required")
        return await self.call("execute", nonce=prepared["nonce"], approved=True, physical_online_confirmed=True, no_external_change=True)

    async def test_fixture_off_on_separate_approvals_never_changes_wind(self):
        self.sdk.wind = "LOW"
        read = await self.call("read")
        self.assertTrue(read["power_profile_verified"])
        self.assertEqual(read["connection"], "unknown")
        for value in ("POWER_OFF", "POWER_ON"):
            result = await self.execute(value)
            self.assertEqual(result["status"], "state_match")
            self.assertTrue(result["api_accepted"])
            self.assertTrue(result["state_match"])
            self.assertFalse(result["physical_confirmed"])
            self.assertEqual(self.sdk.wind, "LOW")
            evidence = await self.call("physical_confirmation", value=value, approved=True)
            self.assertFalse(evidence["physical_confirmed"])
        self.assertEqual(self.sdk.calls.count("post"), 2)
        self.assertEqual((await self.call("prepare", value="POWER_OFF"))["status"], "blocked")

    async def test_power_disabled_by_default_and_read_only(self):
        for backend, enabled, power in (("fixture", True, False), ("live_read_only", False, True)):
            host = HostSession(Session(self.sdk, {"enabled": enabled, "backend": backend, "power_control": power}))
            if backend == "live_read_only":
                with self.assertRaisesRegex(ValueError, "live_read_only_operation_blocked"):
                    await host.call(host.key, {"op": "prepare", "value": "POWER_OFF"})
            else:
                self.assertEqual((await host.call(host.key, {"op": "prepare", "value": "POWER_OFF"}))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_forged_policy_accept_never_posts(self):
        with self.assertRaisesRegex(ValueError, "proposal_expired_or_invalid"):
            await self.call("policy_respond", proposal_id="synthetic", decision="accept")
        for scenario in ("presence_on", "absence_off"):
            view = await self.call("policy_fixture", scenario=scenario)
            result = await self.call("policy_respond", proposal_id=view["proposal"]["proposal_id"], decision="accept")
            self.assertFalse(result["result"]["transport_performed"])
            self.assertFalse(result["result"]["state_confirmed"])
        self.assertNotIn("post", self.sdk.calls)

    async def test_unknown_power_and_already_target_block(self):
        for power in ("UNKNOWN", "POWER_OFF"):
            self.sdk.power = power
            self.assertEqual((await self.call("prepare", value="POWER_OFF"))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_profile_requires_exact_power_read_write_support(self):
        for change in ("read_only", "missing_on", "missing", "malformed"):
            self.sdk.fixture = copy.deepcopy(FIXTURE)
            prop = self.sdk.fixture["profile"]["property"]["operation"]["airPurifierOperationMode"]
            if change == "read_only":
                prop["mode"] = ["r"]
            elif change == "missing_on":
                prop["value"]["w"] = ["POWER_OFF"]
            elif change == "malformed":
                prop["mode"] = "rw"
            else:
                del self.sdk.fixture["profile"]["property"]["operation"]
            self.assertEqual((await self.call("prepare", value="POWER_OFF"))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_fresh_profile_and_external_state_change_block(self):
        for kind in ("profile", "state"):
            self.sdk.fixture = copy.deepcopy(FIXTURE)
            prepared = await self.call("prepare", value="POWER_OFF")
            if kind == "profile":
                self.sdk.fixture["profile"]["property"]["operation"]["airPurifierOperationMode"]["mode"] = ["r"]
            else:
                self.sdk.wind = "LOW"
            result = await self.call("execute", nonce=prepared["nonce"], approved=True, physical_online_confirmed=True, no_external_change=True)
            self.assertEqual(result["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_nonce_consumed_and_each_confirmation_required(self):
        for missing in ("approved", "physical_online_confirmed", "no_external_change"):
            prepared = await self.call("prepare", value="POWER_OFF")
            args = dict(nonce=prepared["nonce"], approved=True, physical_online_confirmed=True, no_external_change=True)
            args[missing] = False
            self.assertEqual((await self.call("execute", **args))["status"], "blocked")
            args[missing] = True
            self.assertEqual((await self.call("execute", **args))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_expired_approval_and_session_block(self):
        prepared = await self.call("prepare", value="POWER_OFF")
        self.now = 31
        self.assertEqual((await self.call("execute", nonce=prepared["nonce"], approved=True, physical_online_confirmed=True, no_external_change=True))["status"], "blocked")
        self.now = 1201
        with self.assertRaises(PermissionError):
            await self.call("prepare", value="POWER_OFF")
        self.assertNotIn("post", self.sdk.calls)

    async def test_expiration_during_pre_post_get_blocks(self):
        prepared = await self.call("prepare", value="POWER_OFF")
        original = self.sdk.async_get_device_status
        async def delayed(*args, **kwargs):
            state = await original(*args, **kwargs)
            self.now = 31
            return state
        self.sdk.async_get_device_status = delayed
        self.assertEqual((await self.call("execute", nonce=prepared["nonce"], approved=True, physical_online_confirmed=True, no_external_change=True))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_cli_cancel_or_no_physical_confirmation_never_restores(self):
        for response, count in (("CANCEL", 0), (POWER_APPROVAL["POWER_OFF"], 1)):
            self.setUp()
            self.host.backend = self.host.session.ctx["backend"] = "live_manual"
            answers = iter(["set_power off", response, "UNKNOWN"])
            with contextlib.redirect_stdout(io.StringIO()):
                await run_trial(self.host, manual=True, power_control=True, confirm=lambda _: next(answers), emit=lambda _: None)
            self.assertEqual(self.sdk.calls.count("post"), count)
            self.assertTrue(self.host.revoked)

    async def test_post_timeout_locks_session_without_restore_or_resend(self):
        self.sdk.response_loss = True
        result = await self.execute("POWER_OFF")
        self.assertEqual(result["status"], "outcome_unconfirmed")
        self.assertFalse(result["api_accepted"])
        self.assertTrue(result["state_match"])
        await self.call("read")
        self.assertEqual((await self.call("prepare", value="POWER_ON"))["status"], "blocked")
        self.assertEqual(self.sdk.calls.count("post"), 1)
        self.assertEqual(self.sdk.power, "POWER_OFF")

    async def test_mismatch_locks_without_next_post(self):
        self.sdk.mismatch = True
        self.assertEqual((await self.execute("POWER_OFF"))["status"], "outcome_unconfirmed")
        self.assertEqual((await self.call("prepare", value="POWER_ON"))["status"], "blocked")
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_next_stage_requires_physical_attestation(self):
        await self.execute("POWER_OFF")
        self.assertEqual((await self.call("prepare", value="POWER_ON"))["status"], "blocked")
        self.assertEqual((await self.call("physical_confirmation", value="POWER_ON", approved=True))["status"], "blocked")
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_power_off_with_missing_job_wind_can_be_read_and_turned_on(self):
        self.sdk.power = "POWER_OFF"
        original = self.sdk.async_get_device_status
        async def missing(*args, **kwargs):
            state = await original(*args, **kwargs)
            if self.sdk.power == "POWER_OFF":
                state.pop("airFlow")
                state.pop("airPurifierJobMode")
            return state
        self.sdk.async_get_device_status = missing
        read = await self.call("read")
        self.assertEqual(read["state"]["power"], "POWER_OFF")
        self.assertIsNone(read["state"]["windStrength"])
        self.assertEqual((await self.execute("POWER_ON"))["status"], "state_match")

    async def test_cli_get_only_and_individually_approved_off_on(self):
        for manual in (False, True):
            self.setUp()
            self.host.backend = "live_manual" if manual else "live_read_only"
            self.host.session.ctx["backend"] = self.host.backend
            self.host.session.ctx["enabled"] = manual
            answers = iter(["set_power off", POWER_APPROVAL["POWER_OFF"], "PHYSICAL POWER_OFF CONFIRMED", "set_power on", POWER_APPROVAL["POWER_ON"], "PHYSICAL POWER_ON CONFIRMED"])
            records = []
            with contextlib.redirect_stdout(io.StringIO()):
                result = await run_trial(self.host, manual=manual, power_control=True, confirm=lambda _: next(answers), emit=records.append)
            self.assertEqual(result, 0)
            self.assertEqual(self.sdk.calls.count("post"), 2 if manual else 0)
            self.assertTrue(self.host.revoked)
            self.assertNotIn(self.sdk.device_id, json.dumps(records))

    async def test_final_http_guard_exact_payload_and_once(self):
        http = AsyncMock()
        http.request.return_value.status = 200
        transport = Transport(http, execute=True, power_control=True)
        transport.select("synthetic-target")
        for value in ("POWER_OFF", "POWER_ON"):
            transport.approve(value)
            with self.assertRaises(StopTrial):
                await transport.request("POST", "https://api-kic.lgthinq.com/devices/synthetic-target/control", json={**control_payload(value), "airFlow": {"windStrength": "AUTO"}})
            await transport.request("POST", "https://api-kic.lgthinq.com/devices/synthetic-target/control", json=control_payload(value))
            with self.assertRaises(StopTrial):
                transport.approve(value)
        self.assertEqual(http.request.await_count, 2)
        for call in http.request.await_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])

    async def test_guarded_sdk_requires_power_opt_in_and_exact_permit(self):
        sdk = AsyncMock()
        transport = Transport(AsyncMock(), execute=True)
        guarded = GuardedSdk(sdk, transport)
        guarded.permit = self.host.session.permit
        guarded.permit.approve("POWER_OFF")
        with self.assertRaises(StopTrial):
            await guarded.async_post_device_control("synthetic-target", control_payload("POWER_OFF"))
        sdk.async_post_device_control.assert_not_awaited()
        with self.assertRaises(StopTrial):
            transport.approve("POWER_OFF")
