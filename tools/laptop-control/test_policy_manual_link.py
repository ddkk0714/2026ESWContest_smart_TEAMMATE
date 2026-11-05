"""Suggestion -> separate manual approval, synthetic SDK only. No Cloud/boards."""
import asyncio
import copy
import contextlib
import io
import json
import unittest
import re
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch
from aiohttp import ClientSession, web

from thinq_host import GuardedSdk, HostSession, Session, Transport, policy_support
from poc.worker import FixtureSdk
from tools.thinq_connect.manual_trial import StopTrial
from thinq_host import make_app
from test_manual_html import free_port

FIXTURE = json.loads((Path(__file__).parent / 'thinq_vendor/poc/fixture.json').read_text())


class PolicyLinkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000.
        self.sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "fixture", "power_control": True,
                                                "policy_link": True}, clock=lambda: self.now), clock=lambda: self.now)

    async def call(self, op, **data):
        return await self.host.call(self.host.key, {"op": op, **data})

    async def scenario(self, name):
        view = await self.call("policy_fixture", scenario=name)
        return view["proposal"]["proposal_id"]

    async def accept(self, name="presence_on"):
        pid = await self.scenario(name)
        view = await self.call("policy_respond", proposal_id=pid, decision="accept")
        self.assertEqual(view["result"]["status"], "awaiting_manual_approval")
        self.assertFalse(view["result"]["transport_performed"])
        self.assertNotIn("post", self.sdk.calls)
        self.assertEqual(view["connection"], "unknown")
        self.assertEqual(view["manual_approval"]["status"], "approval_required")
        return pid, view["manual_approval"]

    async def execute(self, challenge, **changes):
        return await self.call("execute", **dict({"nonce": challenge["nonce"], "approved": True,
                                                "physical_online_confirmed": True, "no_external_change": True}, **changes))

    async def test_all_candidates_handoff_without_post_then_separate_approval_and_readback(self):
        for scenario, value in (("presence_on", "POWER_ON"), ("absence_off", "POWER_OFF"), ("pollution_auto", "AUTO")):
            self.setUp()
            pid, challenge = await self.accept(scenario)
            self.assertEqual(challenge["value"], value)
            self.assertFalse(challenge["physical_confirmed"])
            result = await self.execute(challenge)
            self.assertEqual(result["status"], "state_match")
            self.assertTrue(result["api_accepted"] and result["state_match"])
            self.assertFalse(result["physical_confirmed"])
            self.assertEqual(self.sdk.calls.count("post"), 1)
            self.assertEqual((await self.execute(challenge))["status"], "blocked")
            await self.call("physical_confirmation", value=value, approved=True)
            self.assertFalse(self.host.policy.result["physical_confirmed"])
            self.assertEqual(self.host.policy.result["physical_evidence"], "fixture_user_attestation_only")
            with self.assertRaises(ValueError):
                await self.call("policy_respond", proposal_id=pid, decision="accept")
            self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_reject_and_forged_proposal_never_get_or_post(self):
        pid = await self.scenario("absence_off")
        with self.assertRaises(ValueError):
            await self.call("policy_respond", proposal_id="synthetic-invalid", decision="accept")
        result = await self.call("policy_respond", proposal_id=pid, decision="reject")
        self.assertEqual(result["result"]["status"], "rejected")
        self.assertEqual(self.sdk.calls, [])
        self.assertNotIn("challenge", self.host.session.ctx)

    async def test_raw_prepare_and_unapproved_execute_cannot_bypass_handoff(self):
        self.assertEqual((await self.call("prepare", value="POWER_OFF"))["status"], "blocked")
        _, challenge = await self.accept()
        self.assertEqual((await self.execute(challenge, approved=False))["status"], "blocked")
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_each_manual_check_is_required(self):
        for missing in ("physical_online_confirmed", "no_external_change"):
            self.setUp()
            _, challenge = await self.accept()
            self.assertEqual((await self.execute(challenge, **{missing: False}))["status"], "blocked")
            self.assertNotIn("post", self.sdk.calls)

    async def test_expired_proposal_or_approval_never_posts(self):
        pid = await self.scenario("presence_on")
        self.now += 31
        with self.assertRaises(ValueError):
            await self.call("policy_respond", proposal_id=pid, decision="accept")
        self.setUp()
        _, challenge = await self.accept()
        self.now += 31
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_baseline_or_profile_changed_after_accept_blocks_post(self):
        for change in ("state", "profile"):
            self.setUp()
            _, challenge = await self.accept()
            if change == "state":
                self.sdk.wind = "HIGH"
            else:
                self.sdk.fixture["profile"]["property"]["operation"]["airPurifierOperationMode"]["mode"] = ["r"]
            self.assertEqual((await self.execute(challenge))["status"], "blocked")
            self.assertNotIn("post", self.sdk.calls)

    async def test_timeout_or_mismatch_locks_without_automatic_retry(self):
        for change in ("response_loss", "mismatch"):
            self.setUp()
            _, challenge = await self.accept()
            setattr(self.sdk, change, True)
            self.assertEqual((await self.execute(challenge))["status"], "outcome_unconfirmed")
            await self.call("read")
            self.assertEqual((await self.execute(challenge))["status"], "blocked")
            self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_live_default_post_block_survives_forged_enabled_ui(self):
        _, challenge = await self.accept()
        self.host.backend = self.host.session.ctx["backend"] = "live_manual"
        before = len(self.sdk.calls)
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertEqual(len(self.sdk.calls), before)
        self.assertNotIn("post", self.sdk.calls)

    async def test_separate_live_authorization_still_needs_explicit_approval(self):
        _, challenge = await self.accept()
        self.host.backend = self.host.session.ctx["backend"] = "live_manual"
        self.host.session.ctx["policy_live_post"] = True
        self.assertNotIn("post", self.sdk.calls)
        self.assertEqual((await self.execute(challenge))["status"], "state_match")
        self.assertEqual(self.sdk.calls.count("post"), 1)  # Synthetic SDK, not Cloud.

    async def test_on_only_trial_blocks_other_proposals_without_post(self):
        for scenario in ("absence_off", "pollution_auto"):
            self.setUp()
            self.host.session.ctx["policy_stage"] = "POWER_ON"
            pid = await self.scenario(scenario)
            with self.assertRaisesRegex(ValueError, "control_outside_authorized_trial"):
                await self.call("policy_respond", proposal_id=pid, decision="accept")
            self.assertNotIn("post", self.sdk.calls)
            self.assertNotIn("challenge", self.host.session.ctx)

    async def test_on_only_trial_preserves_single_explicit_on_and_readback(self):
        self.host.session.ctx["policy_stage"] = "POWER_ON"
        _, challenge = await self.accept()
        self.host.backend = self.host.session.ctx["backend"] = "live_manual"
        self.host.session.ctx["policy_live_post"] = True
        self.assertNotIn("post", self.sdk.calls)
        self.assertEqual((await self.execute(challenge))["status"], "state_match")
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_stage_is_rechecked_immediately_before_post(self):
        _, challenge = await self.accept("absence_off")
        self.host.session.ctx["policy_stage"] = "POWER_ON"
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    def off_phase(self, *, state="IDLE", present=False, reasons=None, age=0, connected=True):
        self.phase_seq += 1
        module, _ = policy_support()
        return {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "synthetic-off-trial",
                "seq": self.phase_seq, "data": {"source": "fsm", "fsm_state": state,
                "phase": module.STATE_PHASE[state], "present": present, "reasons": reasons or []}},
                "age_sec": age, "retained": False, "mqtt_connected": connected, "mqtt_epoch": "synthetic"}

    async def off_candidate(self):
        # Synthetic SDK and clock only; no live receiver, credentials or Cloud.
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "live_manual",
            "power_control": True, "policy_link": True, "policy_stage": "POWER_OFF"},
            clock=lambda: self.now), clock=lambda: self.now)
        self.phase_seq = 0
        await self.call("read")
        await self.call("policy", phase=self.off_phase(state="START", present=True))
        for elapsed in range(10, 610, 10):
            self.now = 1000. + elapsed
            view = await self.call("policy", phase=self.off_phase(state="FOCUS_NPC"))
            self.assertIsNone(view["proposal"])
        self.now = 1610.
        await self.call("read")  # Explicit synthetic GET; not automatic polling.
        view = await self.call("policy", phase=self.off_phase(reasons=["absent_timeout"]))
        self.assertEqual(view["proposal"]["value"], "off")
        self.assertFalse(view["automatic_power"] or view["post_enabled"])
        return view["proposal"]["proposal_id"]

    async def accept_off(self, proposal_id):
        result = await self.call("policy_respond", proposal_id=proposal_id, decision="accept", phase=self.off_phase())
        self.assertNotIn("post", self.sdk.calls)
        self.assertEqual(result["manual_approval"]["value"], "POWER_OFF")
        return result["manual_approval"]

    async def test_off_observation_mode_blocks_post_even_after_separate_ui_approval(self):
        challenge = await self.accept_off(await self.off_candidate())
        self.assertFalse(challenge["post_enabled"])
        self.assertEqual((await self.execute(challenge, phase=self.off_phase()))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_off_trial_explicit_mock_approval_posts_once_and_separates_physical_evidence(self):
        proposal_id = await self.off_candidate()
        self.host.session.ctx["policy_live_post"] = True
        challenge = await self.accept_off(proposal_id)
        result = await self.execute(challenge, phase=self.off_phase())
        self.assertTrue(result["api_accepted"] and result["state_match"])
        self.assertFalse(result["physical_confirmed"])
        self.assertEqual(self.sdk.calls.count("post"), 1)
        self.assertEqual((await self.execute(challenge, phase=self.off_phase()))["status"], "blocked")
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_off_returning_presence_before_accept_cancels_without_post(self):
        proposal_id = await self.off_candidate()
        with self.assertRaises(ValueError):
            await self.call("policy_respond", proposal_id=proposal_id, decision="accept",
                            phase=self.off_phase(present=True))
        self.assertNotIn("post", self.sdk.calls)
        self.assertNotIn("challenge", self.host.session.ctx)

    async def test_off_returning_presence_after_accept_blocks_before_post(self):
        proposal_id = await self.off_candidate()
        self.host.session.ctx["policy_live_post"] = True
        challenge = await self.accept_off(proposal_id)
        result = await self.execute(challenge, phase=self.off_phase(present=True))
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_off_expired_proposal_is_not_reissued_on_return_or_fresh_get(self):
        proposal_id = await self.off_candidate()
        self.now += 31
        with self.assertRaises(ValueError):
            await self.call("policy_respond", proposal_id=proposal_id, decision="accept", phase=self.off_phase())
        await self.call("read")
        view = await self.call("policy", phase=self.off_phase())
        self.assertIsNone(view["proposal"])
        self.assertNotIn("post", self.sdk.calls)

    async def test_off_disconnect_stale_or_expired_approval_blocks_before_post(self):
        for fault in ("disconnect", "stale", "expired"):
            self.setUp()
            proposal_id = await self.off_candidate()
            self.host.session.ctx["policy_live_post"] = True
            challenge = await self.accept_off(proposal_id)
            if fault == "expired":
                self.now += 31
            phase = self.off_phase(connected=fault != "disconnect", age=31 if fault == "stale" else 0)
            self.assertEqual((await self.execute(challenge, phase=phase))["status"], "blocked")
            self.assertNotIn("post", self.sdk.calls)

    async def test_presence_change_and_stale_hub_cancel_after_accept(self):
        for kind in ("presence", "stale"):
            self.setUp()
            _, challenge = await self.accept()
            self.host.fixture_policy_active = False
            self.host.policy_phase = {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "fixture",
                "seq": self.host.policy.seq + 1, "data": {"fsm_state": "IDLE", "present": False, "reasons": []}},
                "retained": False, "age_sec": 0 if kind == "presence" else 31}
            if kind == "stale":
                self.host.policy.hub_at = self.now - 31
            reply = await self.execute(challenge, phase=self.host.policy_phase)
            self.assertEqual(reply["status"], "blocked")
            self.assertNotIn("post", self.sdk.calls)

    async def test_pollution_improvement_during_fresh_get_blocks_auto(self):
        _, challenge = await self.accept("pollution_auto")
        self.sdk.fixture["state"]["airQualitySensor"]["totalPollutionLevel"] = None
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_hub_age_increases_while_waiting_for_pre_post_get(self):
        _, challenge = await self.accept()
        self.host.fixture_policy_active = False
        phase = {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "fixture",
            "seq": self.host.policy.seq + 1, "data": {"fsm_state": "FOCUS_PC", "present": True}},
            "retained": False, "age_sec": 20}
        original = self.sdk.async_get_device_status
        async def delayed(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.now += 11  # Approval still valid, but Hub receipt now 31 sec old.
            return result
        self.sdk.async_get_device_status = delayed
        self.assertEqual((await self.execute(challenge, phase=phase))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_independent_auto_requires_policy_at_http_boundary(self):
        transport = Transport(AsyncMock(), execute=True)
        with self.assertRaises(StopTrial):
            transport.approve("AUTO", policy_auto=True)
        transport = Transport(AsyncMock(), execute=True, policy_control=True)
        with self.assertRaises(StopTrial):
            transport.approve("AUTO")
        transport.approve("AUTO", policy_auto=True)
        self.assertEqual(transport.permit, "AUTO")

    async def test_guarded_sdk_passes_policy_auto_permit_once(self):
        transport = Transport(AsyncMock(), execute=True, policy_control=True)
        sdk = AsyncMock()
        guarded = GuardedSdk(sdk, transport)
        guarded.permit = self.host.session.permit
        guarded.permit.approve("AUTO")
        guarded.permit.policy_auto = True
        await guarded.async_post_device_control("synthetic-target", {"airFlow": {"windStrength": "AUTO"}})
        self.assertFalse(guarded.permit.policy_auto)
        self.assertEqual(sdk.async_post_device_control.await_count, 1)
        with self.assertRaises(StopTrial):
            await guarded.async_post_device_control("synthetic-target", {"airFlow": {"windStrength": "AUTO"}})

    async def prepare_live_phase_candidate(self):
        # Live-mode contracts, but still a synthetic SDK: no Cloud transport exists.
        self.sdk.power = "POWER_OFF"
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "live_manual", "power_control": True,
                                                "policy_link": True, "policy_live_post": True}, clock=lambda: self.now), clock=lambda: self.now)
        await self.call("read")
        self.phase_seq = 0
        for t in (1000., 1010., 1020., 1030.):
            self.now = t
            view = await self.call("policy", phase=self.live_phase("START" if t == 1000 else "FOCUS_PC"))
        self.assertEqual(view["proposal"]["value"], "on")
        self.assertEqual(view["connection"], "unknown")
        reply = await self.call("policy_respond", proposal_id=view["proposal"]["proposal_id"], decision="accept", phase=self.live_phase())
        self.assertNotIn("post", self.sdk.calls)
        return reply["manual_approval"]

    def live_phase(self, state="FOCUS_PC", present=True):
        self.phase_seq += 1
        return {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "synthetic-live-phase",
            "seq": self.phase_seq, "data": {"fsm_state": state, "present": present}}, "age_sec": 0, "retained": False}

    async def test_live_phase_based_candidate_links_to_separate_manual_approval(self):
        challenge = await self.prepare_live_phase_candidate()
        result = await self.execute(challenge, phase=self.live_phase())
        self.assertEqual(result["status"], "state_match")
        self.assertEqual(self.sdk.calls.count("post"), 1)
        self.assertFalse(result["physical_confirmed"])
        result = await self.call("physical_confirmation", value="POWER_ON", approved=True)
        self.assertTrue(result["physical_confirmed"])
        self.assertEqual(self.host.snapshot.view()["connection"], "unknown")

    async def test_live_phase_change_after_accept_blocks_without_post(self):
        challenge = await self.prepare_live_phase_candidate()
        result = await self.execute(challenge, phase=self.live_phase("IDLE", present=False))
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_40_minute_session_keeps_30_second_approval_and_default_post_lock(self):
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "fixture", "power_control": True,
                                                "policy_link": True}, clock=lambda: self.now), lifetime=2400, clock=lambda: self.now)
        claim = self.host.claim()
        self.assertEqual(claim["session_lifetime_seconds"], 2400)
        self.assertEqual(claim["expires_in_seconds"], 2400)
        self.assertFalse(claim["policy_post_enabled"])
        self.now += 1201
        self.assertTrue(self.host.active())
        self.assertEqual((await self.call("read"))["status"], "read_ok")
        _, challenge = await self.accept()
        self.assertEqual(challenge["approval_ttl_seconds"], 30)
        self.now += 31
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.now = 3400
        self.assertFalse(self.host.active())
        with self.assertRaises(PermissionError):
            await self.call("read")
        self.assertNotIn("post", self.sdk.calls)


class ManualReviewPolicyTests(unittest.TestCase):
    def test_live_cli_selects_40_minutes_without_arming_post_or_calling_cloud(self):
        from thinq_host import main
        async def no_cloud(*args, **kwargs):
            self.assertEqual(kwargs["lifetime"], 2400)
            self.assertTrue(kwargs["policy_link"])
            self.assertTrue(kwargs["loopback"])
            self.assertFalse(kwargs["policy_live_post"])
        with (patch('sys.argv', ['thinq_host.py', '--live-manual', '--power-control', '--policy-link', '--session-minutes', '40', '--port', '19392']),
              patch('sys.stdin.isatty', return_value=True),
              patch('importlib.metadata.version', return_value='1.0.14'),
              patch('thinq_host.load_or_create_client_id', return_value='SYNTHETIC-CLIENT') as load,
              patch('thinq_host.getpass.getpass', return_value='FAKE-TOKEN-NOT-REAL'),
              patch('thinq_host.serve', side_effect=no_cloud), contextlib.redirect_stdout(io.StringIO()) as output):
            main()
        load.assert_called_once_with(create=False)
        self.assertNotIn('FAKE-TOKEN', output.getvalue())

    def test_unknown_allows_manual_candidates_only_never_online_or_automatic(self):
        module, config = policy_support()
        now = [1000.]
        p = module.fixture_policy(config, "presence_on", clock=lambda: now[0])
        p.snapshot["connection"] = "unknown"
        self.assertIsNone(p.view()["proposal"])
        p = module.fixture_policy(config, "presence_on", clock=lambda: now[0])
        p.manual_review = True
        p.snapshot["connection"] = "unknown"
        view = p.view()
        self.assertIsNotNone(view["proposal"])
        self.assertEqual(p.snapshot["connection"], "unknown")
        self.assertFalse(view["post_enabled"] or view["automatic_power"])
        p.snapshot["connection"] = "offline"
        self.assertIsNone(p.view()["proposal"])


class PolicyHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_node_python_fixture_handoff_and_separate_execution(self):
        root = Path(__file__).resolve().parents[2]
        sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        host = HostSession(Session(sdk, {"enabled": True, "backend": "fixture", "power_control": True, "policy_link": True}), lifetime=2400)
        port, backend = free_port(), free_port()
        runner = web.AppRunner(make_app(host, port=backend), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', backend).start()
        node = subprocess.Popen(['node', 'tools/laptop-control/server.mjs', '--mode', 'fixture', '--policy-link', 'true',
                                 '--power-control', 'true', '--session-minutes', '40', '--port', str(port), '--thinq-port', str(backend),
                                 '--sensor-port', str(free_port())], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            async with ClientSession() as http:
                origin = f'http://127.0.0.1:{port}'
                for _ in range(60):
                    try:
                        async with http.get(origin) as response:
                            html = await response.text()
                            break
                    except OSError:
                        await asyncio.sleep(.05)
                else:
                    self.fail('Fixture Node did not start')
                self.assertIn('data-policy-link="true"', html)
                self.assertIn('인증은 40분 동안 유지돼요.', html)
                key = re.search(r"const token='([^']+)'", html).group(1)
                headers = {'Origin': origin, 'X-Deskmate-Key': key}
                async def post(route, body):
                    async with http.post(origin + route, json=body, headers=headers) as response:
                        return response.status, await response.json()
                self.assertEqual((await post('/api/thinq/connect', {}))[0], 200)
                _, scenario = await post('/api/thinq/policy-fixture', {'scenario': 'presence_on'})
                pid = scenario['proposal']['proposal_id']
                self.assertEqual((await post('/api/thinq/policy-response', {'proposal_id': pid, 'decision': 'accept', 'phase': {}}))[0], 400)
                status, response = await post('/api/thinq/policy-response', {'proposal_id': pid, 'decision': 'accept'})
                self.assertEqual(status, 200)
                self.assertEqual(response['manual_approval']['value'], 'POWER_ON')
                self.assertNotIn('post', sdk.calls)
                nonce = response['manual_approval']['nonce']
                execute = {'op': 'execute', 'nonce': nonce, 'approved': True, 'physical_online_confirmed': True, 'no_external_change': True}
                self.assertEqual((await post('/api/thinq/call', {**execute, 'phase': {}}))[0], 400)
                _, result = await post('/api/thinq/call', execute)
                self.assertEqual(result['status'], 'state_match')
                self.assertEqual(sdk.calls.count('post'), 1)
                self.assertEqual((await post('/api/thinq/call', execute))[1]['status'], 'blocked')
                _, result = await post('/api/thinq/call', {'op': 'physical_confirmation', 'value': 'POWER_ON', 'approved': True})
                self.assertFalse(result['physical_confirmed'])
                self.assertNotIn(sdk.device_id, json.dumps(result))
        finally:
            node.terminate()
            await asyncio.to_thread(node.wait, timeout=5)
            await runner.cleanup()
