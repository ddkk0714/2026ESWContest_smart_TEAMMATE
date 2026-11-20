"""Offline V1.1 decisions. No Cloud, board, broker, or real control calls."""
import copy
import unittest

from thinq_host import HostSession, Session, policy_support
from thinq_observer import PhaseFeed
from poc.worker import FixtureSdk
from test_live_read_only import FIXTURE

module, CONFIG = policy_support()


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.policy = module.AirPurifierPolicy(CONFIG, clock=lambda: self.now)
        self.seq = 0

    def snapshot(self, *, power="POWER_OFF", wind="LOW", grade="BAD", connection="online"):
        return {"api_status": "ok", "receipt_stale": False, "connection": connection,
                "received_at": self.now + 10000, "api_age_sec": 0, "expires_at": self.now + 10120,
                "fields": {k: {"value": v, "valid": True, "status": "ok"} for k, v in {
                    "power": power, "wind_strength": wind, "total_pollution_level": grade}.items()}}

    def tick(self, t, *, state="FOCUS_PC", present=True, reasons=None, snap=None, boot="test"):
        self.now = t
        self.seq += 1
        phase = {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": boot, "seq": self.seq,
                              "data": {"fsm_state": state, "present": present, "reasons": reasons or []}},
                 "age_sec": 0, "retained": False}
        return self.policy.observe(phase, self.snapshot() if snap is None else snap)

    def on_candidate(self):
        self.tick(0, state="START")
        for t in (10, 20):
            self.assertIsNone(self.tick(t)["proposal"])
        return self.tick(30)

    def test_present_30_seconds_on_mock_does_not_force_auto_or_confirm_state(self):
        p = self.on_candidate()["proposal"]
        self.assertEqual((p["operation"], p["value"]), ("set_power", "on"))
        result = self.policy.respond(p["proposal_id"], "accept")["result"]
        self.assertEqual(result["status"], "mock_succeeded")
        self.assertFalse(result["transport_performed"])
        self.assertFalse(result["state_confirmed"])
        self.assertIsNone(result["actual_value"])
        with self.assertRaises(ValueError):
            self.policy.respond(p["proposal_id"], "accept")
        self.assertIsNone(self.tick(40)["proposal"])

    def test_presence_glitch_restarts_continuous_timer(self):
        self.tick(0, state="START")
        self.tick(20, present=False)
        self.tick(21)
        self.assertIsNone(self.tick(40)["proposal"])
        self.assertIsNotNone(self.tick(51)["proposal"])

    def test_idle_without_absent_timeout_never_off_even_after_long_absence(self):
        self.tick(0, state="START", snap=self.snapshot(power="POWER_ON", wind="AUTO"))
        for t in range(10, 620, 10):
            self.assertIsNone(self.tick(t, present=False, snap=self.snapshot(power="POWER_ON", wind="AUTO"))["proposal"])
        self.assertIsNone(self.tick(620, state="IDLE", present=False, snap=self.snapshot(power="POWER_ON"))["proposal"])

    def test_off_uses_fsm_idle_boundary_and_remains_pending_until_response(self):
        self.tick(0, state="START", snap=self.snapshot(power="POWER_ON", wind="AUTO"))
        for t in range(10, 610, 10):
            self.tick(t, present=False, snap=self.snapshot(power="POWER_ON", wind="AUTO"))
        view = self.tick(610, state="IDLE", present=False, reasons=["absent_timeout"], snap=self.snapshot(power="POWER_ON", wind="AUTO"))
        p = view["proposal"]
        self.assertEqual((p["operation"], p["value"]), ("set_power", "off"))
        again = self.tick(620, state="IDLE", present=False, snap=self.snapshot(power="POWER_ON", wind="AUTO"))
        self.assertEqual(again["proposal"]["proposal_id"], p["proposal_id"])
        self.assertEqual(self.policy.respond(p["proposal_id"], "reject")["result"]["status"], "rejected")
        self.assertIsNone(self.policy.view()["proposal"])

    def test_real_fsm_600_second_absence_supplies_off_boundary_without_new_detector(self):
        from deskmate_hub.inference import FSMEngine, State
        from deskmate_hub.presentation import state_envelope
        from test_fsm_transitions import frame
        engine = FSMEngine()
        seq = 0
        def consume(f):
            nonlocal seq
            self.now = f.now
            result = engine.tick(f)
            env = state_envelope(result, boot_id="real-fsm-fixture", seq=seq, ts=f.now, present=f.present)
            seq += 1
            view = self.policy.observe({"envelope": env, "age_sec": 0, "retained": False}, self.snapshot(power="POWER_ON", wind="AUTO"))
            return result, view
        consume(frame(0, touch=True))
        for t in range(10, 310, 10):
            consume(frame(t))
        result, _ = consume(frame(302))
        self.assertEqual(result.state, State.FOCUS_PC)
        for t in range(310, 910, 10):
            result, view = consume(frame(t, present=False))
            self.assertIsNone(view["proposal"])
        result, view = consume(frame(910, present=False))
        self.assertEqual(result.state, State.IDLE)
        self.assertIn("absent_timeout", result.actions)
        self.assertEqual((view["proposal"]["operation"], view["proposal"]["value"]), ("set_power", "off"))

    def test_bad_low_needs_distinct_api_reports_and_hold_not_co2(self):
        s = self.snapshot(power="POWER_ON")
        self.tick(0, state="START", snap=s)
        for t in range(10, 70, 10):
            self.assertIsNone(self.tick(t, snap=s)["proposal"])
        view = self.tick(70, snap=self.snapshot(power="POWER_ON"))
        self.assertEqual(view["proposal"]["value"], "auto")
        self.assertEqual(view["persistence_basis"], "api_reports_not_sensor_measurements")

    def test_already_auto_and_unverified_grade_do_not_propose(self):
        for grade, wind in [("BAD", "AUTO"), ("UNKNOWN", "LOW"), (None, "LOW")]:
            p = module.fixture_policy(CONFIG, "pollution_auto", clock=lambda: 1000.)
            p.snapshot["fields"]["total_pollution_level"]["value"] = grade
            p.snapshot["fields"]["wind_strength"]["value"] = wind
            self.assertIsNone(p.view()["proposal"])

    def test_unknown_connection_stale_missing_and_expired_approval_block(self):
        for change in ({"connection": "unknown"}, {"receipt_stale": True}, {"api_status": "api_error"}):
            p = module.fixture_policy(CONFIG, "presence_on", clock=lambda: 1000.)
            old = p.view()["proposal"]["proposal_id"]
            p.snapshot.update(change)
            with self.assertRaises(ValueError):
                p.respond(old, "accept")
        proposal = self.on_candidate()["proposal"]
        self.now = 61
        with self.assertRaises(ValueError):
            self.policy.respond(proposal["proposal_id"], "accept")

    def test_manual_change_wins_and_cancels_old_proposal(self):
        old = self.on_candidate()["proposal"]["proposal_id"]
        self.now = 31
        view = self.tick(31, snap=self.snapshot(power="POWER_ON"))
        self.assertEqual(view["reason"], "proposal_invalidated_or_expired")
        with self.assertRaises(ValueError):
            self.policy.respond(old, "accept")
        self.assertEqual(self.policy.view()["reason"], "manual_override")

    def test_mid_session_attach_restart_duplicate_and_retained_cannot_start_session(self):
        for t in (0, 10, 20, 30, 40):
            self.assertIsNone(self.tick(t)["proposal"])
        self.tick(50, state="START")
        self.tick(60)
        self.tick(70, boot="restart")
        self.assertIsNone(self.tick(80, boot="restart")["proposal"])

    def test_unknown_connection_reports_never_build_bad_persistence(self):
        for t in range(0, 80, 10):
            self.now = t
            self.tick(t, state="START" if t == 0 else "FOCUS_PC", snap=self.snapshot(power="POWER_ON", connection="unknown"))
        self.now = 80
        view = self.tick(80, snap=self.snapshot(power="POWER_ON"))
        self.assertIsNone(view["proposal"])
        self.assertEqual(view["reason"], "waiting_for_api_bad_persistence")

    def test_unsupported_external_wind_still_sets_manual_hold(self):
        self.tick(0, state="START", snap=self.snapshot(power="POWER_ON"))
        self.now = 10
        view = self.tick(10, snap=self.snapshot(power="POWER_ON", wind="HIGH"))
        self.assertIsNone(view["proposal"])
        self.assertEqual(view["reason"], "manual_override")

    def test_duplicate_phase_does_not_renew_receipt(self):
        self.tick(0, state="START")
        phase = {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "test", "seq": self.seq,
                             "data": {"fsm_state": "START", "present": True}}, "age_sec": 0, "retained": False}
        self.now = 31
        self.assertEqual(self.policy.observe(phase, self.snapshot())["reason"], "hub_missing_or_stale")

    def test_all_fixture_scenarios_are_mock_and_rejection_is_single_use(self):
        for scenario, operation, value in [("presence_on", "set_power", "on"), ("absence_off", "set_power", "off"), ("pollution_auto", "set_mode", "auto")]:
            p = module.fixture_policy(CONFIG, scenario, clock=lambda: 1000.)
            candidate = p.view()["proposal"]
            self.assertEqual((candidate["operation"], candidate["value"]), (operation, value))
            p.respond(candidate["proposal_id"], "reject")
            self.assertIsNone(p.view()["proposal"])

    def test_phase_feed_ignores_retained_duplicate_reverse_and_private_fields(self):
        clock = [0.]
        feed = PhaseFeed(clock=lambda: clock[0])
        env = {"schema_version": "1.0", "node": "hub", "boot_id": "fake", "seq": 1,
               "data": {"present": True, "fsm_state": "START", "account": "not-public"}}
        feed.observe("deskmate/state/phase", env, retained=True)
        self.assertIsNone(feed.view())
        feed.observe("deskmate/state/phase", env)
        clock[0] = 20
        feed.observe("deskmate/state/phase", env)
        self.assertEqual(feed.view()["age_sec"], 20)
        self.assertNotIn("account", feed.view()["envelope"]["data"])
        env["seq"] = 0
        feed.observe("deskmate/state/phase", env)
        self.assertEqual(feed.view()["envelope"]["seq"], 1)


class HostPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_policy_response_never_calls_sdk_or_changes_thinq_readings(self):
        sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        host = HostSession(Session(sdk, {"enabled": True, "backend": "fixture"}))
        key = host.claim()["key"]
        before = host.snapshot.view()
        for scenario in ("presence_on", "absence_off", "pollution_auto"):
            result = await host.call(key, {"op": "policy_fixture", "scenario": scenario})
            self.assertTrue(result["fixture_timeline"])
            reply = await host.call(key, {"op": "policy_respond", "proposal_id": result["proposal"]["proposal_id"], "decision": "accept"})
            self.assertEqual(reply["result"]["status"], "mock_succeeded")
        self.assertEqual(sdk.calls, [])
        self.assertEqual(host.snapshot.view()["fields"], before["fields"])

    async def test_live_read_only_policy_is_read_only_and_does_not_get_or_post(self):
        sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        host = HostSession(Session(sdk, {"enabled": False, "backend": "live_read_only"}))
        key = host.claim()["key"]
        result = await host.call(key, {"op": "policy", "phase": None})
        self.assertFalse(result["post_enabled"])
        for op in ("policy_fixture", "policy_respond"):
            with self.assertRaises(ValueError):
                await host.call(key, {"op": op})
        self.assertEqual(sdk.calls, [])
