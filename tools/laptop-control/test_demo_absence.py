"""Short demo uses real-contract synthetic frames, never Cloud or board I/O."""
import copy
import json
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

from thinq_host import HostSession, Session, policy_support
from thinq_observer import PhaseFeed
from poc.worker import FixtureSdk

module, CONFIG = policy_support()
FIXTURE = json.loads((Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text())


class DemoAbsenceTests(unittest.TestCase):
    def setUp(self):
        self.now, self.seq = 0., 0
        self.policy = module.AirPurifierPolicy(CONFIG, clock=lambda: self.now,
                                              manual_review=True, demo_absence=True)

    def packet(self, t, *, present=False, connected=True, epoch="first", source="fsm", age=0,
               speed="fast", enabled=True):
        self.now = t
        self.seq += 1
        return {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "synthetic-demo",
                "seq": self.seq, "data": {"source": source, "fsm_state": "FOCUS_NPC", "phase": "focus",
                                           "present": present, "reasons": [],
                                           "sensor_summary": {"demo_mode": {"speed": speed, "enabled": enabled, "period_sec": 5}}}},
                "age_sec": age, "retained": False, "mqtt_connected": connected, "mqtt_epoch": epoch}

    def tick(self, t, *, power="POWER_ON", wind="AUTO", grade=None, **kwargs):
        phase = self.packet(t, **kwargs)
        snapshot = {"api_status": "ok", "receipt_stale": False, "connection": "unknown", "api_age_sec": 0,
                    "received_at": 10000 + t, "expires_at": 10120 + t,
                    "fields": {k: {"value": v, "valid": True, "status": "ok"} for k, v in
                               {"power": power, "wind_strength": wind, "total_pollution_level": grade}.items()}}
        return self.policy.observe(phase, snapshot)

    def candidate(self):
        self.tick(0, present=True)
        self.tick(1)
        self.tick(3)
        return self.tick(6)

    def test_demo_requires_explicit_opt_in_and_manual_review(self):
        with self.assertRaises(ValueError):
            module.AirPurifierPolicy(CONFIG, demo_absence=True)
        self.policy = module.AirPurifierPolicy(CONFIG, clock=lambda: self.now, manual_review=True)
        view = self.candidate()
        self.assertFalse(view["demo_absence_mode"])
        self.assertIsNone(view["proposal"])  # Production still needs the real IDLE boundary.

    def test_hub_fast_5_seconds_proposes_off_without_changing_fsm(self):
        view = self.candidate()
        self.assertEqual(CONFIG["demo_absence_off_sec"], 5)  # PR #114 fsm.demo_fast.absent_off_after_sec.
        self.assertEqual(view["fsm_state"], "FOCUS_NPC")
        self.assertEqual(view["absence_elapsed_sec"], 5)
        self.assertEqual(view["hub_demo_speed"], "fast")
        self.assertEqual(view["proposal"]["reason"], "demo_absence_elapsed")
        self.assertFalse(view["automatic_power"] or view["post_enabled"])
        result = self.policy.respond(view["proposal"]["proposal_id"], "accept", manual=True)["result"]
        self.assertEqual(result["status"], "awaiting_manual_approval")
        self.assertFalse(result["transport_performed"])

    def test_below_hub_fast_5_seconds_never_proposes(self):
        self.tick(0, present=True)
        self.tick(1)
        self.assertIsNone(self.tick(5.9)["proposal"])
        self.assertIsNotNone(self.tick(6)["proposal"])

    def test_frozen_false_or_duplicate_polling_cannot_advance_absence(self):
        self.tick(0, present=True)
        self.tick(1)
        self.now = 21
        view = self.policy.view()
        self.assertEqual(view["absence_elapsed_sec"], 0)
        self.assertIsNone(view["proposal"])
        self.now = 35
        self.assertIsNone(self.policy.view()["proposal"])

    def test_returning_presence_cancels_pending_and_manual_approval(self):
        for accept in (False, True):
            self.setUp()
            proposal = self.candidate()["proposal"]
            if accept:
                self.policy.respond(proposal["proposal_id"], "accept", manual=True)
            self.assertIsNone(self.tick(7, present=True)["proposal"])
            self.assertFalse(self.policy.manual_valid(proposal["proposal_id"]))
            with self.assertRaises(ValueError):
                self.policy.respond(proposal["proposal_id"], "accept", manual=True)

    def test_disconnect_and_epoch_change_require_new_presence_baseline(self):
        self.tick(0, present=True)
        self.tick(1)
        self.tick(3)
        self.tick(4, connected=False, epoch="lost")
        self.assertIsNone(self.tick(5, epoch="back")["proposal"])
        self.assertIsNone(self.tick(10, epoch="back")["proposal"])
        self.tick(11, present=True, epoch="back")
        self.tick(12, epoch="back")
        self.tick(14, epoch="back")
        self.assertIsNotNone(self.tick(17, epoch="back")["proposal"])

    def test_no_prior_presence_and_unproved_frames_cannot_start_demo(self):
        for t in (0, 10, 20, 30):
            self.assertIsNone(self.tick(t)["proposal"])
        self.assertEqual(self.policy.view()["reason"], "waiting_for_demo_presence_baseline")
        self.tick(31, present=True)
        self.tick(32)
        self.assertIsNone(self.tick(52, source="other")["proposal"])
        self.assertIsNone(self.tick(62)["proposal"])

    def test_stale_state_or_changed_power_remains_blocked(self):
        self.tick(0, present=True)
        self.tick(1)
        self.tick(3)
        self.assertIsNone(self.tick(6, age=31)["proposal"])
        self.setUp()
        self.tick(0, present=True)
        self.tick(1)
        self.tick(3)
        self.assertIsNone(self.tick(6, power="POWER_OFF")["proposal"])
        self.assertEqual(self.policy.view()["reason"], "manual_override")

    def test_explicit_laptop_demo_uses_same_threshold_without_requiring_hub_mode(self):
        for speed, enabled in (("normal", True), (None, True), ("fast", False)):
            self.setUp()
            for t in (0, 1, 3, 6):
                view = self.tick(t, present=t == 0, speed=speed, enabled=enabled)
            self.assertEqual(view["proposal"]["value"], "off")
            self.assertEqual(view["absence_required_sec"], 5)
            self.assertFalse(view["automatic_power"] or view["post_enabled"])

    def test_hub_mode_is_informational_and_does_not_reset_laptop_demo(self):
        proposal = self.candidate()["proposal"]
        self.assertEqual(self.tick(7, speed="normal")["proposal"]["proposal_id"], proposal["proposal_id"])
        self.policy.respond(proposal["proposal_id"], "reject", manual=True)
        self.assertIsNone(self.tick(8)["proposal"])
        self.assertIsNone(self.tick(13)["proposal"])

    def test_normal_hub_mode_preserves_existing_on_proposal(self):
        for t in (0, 10, 20, 30):
            view = self.tick(t, present=True, speed="normal", power="POWER_OFF")
        self.assertEqual(view["proposal"]["value"], "on")
        self.assertEqual(view["hub_demo_speed"], "normal")

    def test_normal_hub_mode_preserves_existing_auto_proposal(self):
        for t in (0, 20, 40, 60):
            view = self.tick(t, present=True, speed="normal", wind="LOW", grade="BAD")
        self.assertEqual(view["proposal"]["value"], "auto")
        self.assertEqual(view["hub_demo_speed"], "normal")

    def test_phase_feed_forwards_only_documented_demo_mode_fields(self):
        feed = PhaseFeed(clock=lambda: self.now)
        feed.set_connected(True)
        packet = self.packet(0, present=True)
        packet["envelope"]["data"]["sensor_summary"]["profile_id"] = "SYNTHETIC-PRIVATE"
        packet["envelope"]["data"]["sensor_summary"]["demo_mode"]["other"] = "SYNTHETIC-PRIVATE"
        feed.observe('deskmate/state/phase', packet["envelope"], retained=False)
        self.assertEqual(feed.view()["envelope"]["data"]["sensor_summary"],
                         {"demo_mode": {"speed": "fast", "enabled": True, "period_sec": 5}})
        self.assertNotIn("SYNTHETIC-PRIVATE", json.dumps(feed.view()))

    def test_config_threshold_is_validated(self):
        for value in (None, 0, -1, True, float("nan")):
            config = dict(CONFIG, demo_absence_off_sec=value)
            with self.assertRaises(ValueError):
                module.AirPurifierPolicy(config, manual_review=True, demo_absence=True)

    def test_demo_cli_is_explicit_and_keeps_live_post_disabled(self):
        from thinq_host import main
        async def no_cloud(*args, **kwargs):
            self.assertTrue(kwargs["demo_absence"])
            self.assertTrue(kwargs["policy_link"])
            self.assertFalse(kwargs["policy_live_post"])
        args = ['thinq_host.py', '--live-manual', '--power-control', '--policy-link', '--demo-absence',
                '--session-minutes', '40', '--port', '19592']
        with (patch('sys.argv', args), patch('sys.stdin.isatty', return_value=True),
              patch('importlib.metadata.version', return_value='1.0.14'),
              patch('thinq_host.load_or_create_client_id', return_value='SYNTHETIC-CLIENT'),
              patch('thinq_host.getpass.getpass', return_value='FAKE-TOKEN-NOT-REAL'),
              patch('thinq_host.serve', side_effect=no_cloud), contextlib.redirect_stdout(io.StringIO()) as output):
            main()
        self.assertNotIn('FAKE-TOKEN', output.getvalue())


class DemoHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, *, allow=False):
        self.frames = DemoAbsenceTests()
        self.frames.setUp()
        self.sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "live_manual", "power_control": True,
            "policy_link": True, "demo_absence": True, "policy_live_post": allow, "policy_stage": "POWER_OFF"},
            clock=lambda: self.frames.now), clock=lambda: self.frames.now)
        await self.host.call(self.host.key, {"op": "read"})
        for t in (0, 1, 3, 6):
            view = await self.host.call(self.host.key, {"op": "policy", "phase": self.frames.packet(t, present=t == 0)})
        proposal = view["proposal"]
        self.assertEqual(proposal["reason"], "demo_absence_elapsed")
        result = await self.host.call(self.host.key, {"op": "policy_respond", "proposal_id": proposal["proposal_id"],
            "decision": "accept", "phase": self.frames.packet(7)})
        self.assertNotIn("post", self.sdk.calls)
        return result["manual_approval"]

    async def execute(self, challenge, *, approved=True, present=False, speed="fast"):
        return await self.host.call(self.host.key, {"op": "execute", "nonce": challenge["nonce"], "approved": approved,
            "physical_online_confirmed": True, "no_external_change": True, "phase": self.frames.packet(8, present=present, speed=speed)})

    async def test_default_live_post_block_survives_demo_handoff(self):
        challenge = await self.prepare()
        self.assertFalse(challenge["post_enabled"])
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_authorized_synthetic_trial_still_needs_separate_approval(self):
        challenge = await self.prepare(allow=True)
        self.assertEqual((await self.execute(challenge, approved=False))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_synthetic_approved_off_is_once_with_readback_not_physical_evidence(self):
        challenge = await self.prepare(allow=True)
        result = await self.execute(challenge)
        self.assertTrue(result["api_accepted"] and result["state_match"])
        self.assertFalse(result["physical_confirmed"])
        self.assertEqual((await self.execute(challenge))["status"], "blocked")
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_return_before_post_cancels_even_authorized_demo(self):
        challenge = await self.prepare(allow=True)
        self.assertEqual((await self.execute(challenge, present=True))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_explicit_synthetic_approval_remains_required_with_normal_hub_mode(self):
        challenge = await self.prepare(allow=True)
        self.assertEqual((await self.execute(challenge, speed="normal", approved=False))["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)
