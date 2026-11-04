"""Authoritative FSM attachment, local continuity and dedup; no I/O."""
import copy
import unittest

from thinq_host import policy_support
from thinq_observer import PhaseFeed

module, CONFIG = policy_support()


class SessionAttachTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.seq = 0
        self.policy = module.AirPurifierPolicy(CONFIG, clock=lambda: self.now, manual_review=True)

    def snapshot(self, power="POWER_OFF"):
        return {"api_status": "ok", "receipt_stale": False, "connection": "unknown", "api_age_sec": 0,
                "received_at": 10000 + self.now, "expires_at": 10120 + self.now,
                "fields": {k: {"value": v, "valid": True, "status": "ok"} for k, v in
                           {"power": power, "wind_strength": "LOW", "total_pollution_level": "BAD"}.items()}}

    def tick(self, t, *, state="FOCUS_NPC", present=True, boot="test", epoch="first",
             connected=True, age=0, retained=False, proof=True, source="fsm", phase=None, reasons=None, power="POWER_OFF"):
        self.now = t
        self.seq += 1
        data = {"fsm_state": state, "present": present, "reasons": reasons or []}
        if proof:
            data.update(source=source, phase=module.STATE_PHASE.get(state) if phase is None else phase)
        packet = {"envelope": {"schema_version": "1.0", "node": "hub", "boot_id": boot, "seq": self.seq,
                               "data": data}, "age_sec": age, "retained": retained,
                  "mqtt_connected": connected, "mqtt_epoch": epoch}
        return self.policy.observe(packet, self.snapshot(power))

    def candidate(self):
        view = None
        for t in (0, 10, 20, 30):
            view = self.tick(t)
        self.assertEqual(view["proposal"]["operation"], "set_power")
        self.assertEqual(view["proposal"]["value"], "on")
        return view["proposal"]

    def test_mapping_matches_hub_authoritative_projection(self):
        from deskmate_hub.presentation import _PHASE_BY_STATE
        self.assertEqual(module.STATE_PHASE, {state.value: phase for state, phase in _PHASE_BY_STATE.items()})

    def test_idle_start_path_is_preserved(self):
        self.assertIsNone(self.tick(0, state="IDLE")["proposal"])
        self.assertIsNone(self.tick(1, state="START")["proposal"])
        self.assertEqual(self.policy.session_origin, "start_observed")
        for t in (11, 21):
            self.assertIsNone(self.tick(t)["proposal"])
        self.assertIsNotNone(self.tick(31)["proposal"])

    def test_mid_focus_attach_requires_new_30_seconds_not_cached_age(self):
        self.assertIsNone(self.tick(0, age=20)["proposal"])
        self.assertEqual(self.policy.state, "FOCUS_NPC")  # No synthetic START.
        self.assertEqual(self.policy.session_origin, "attached")
        self.assertEqual(self.policy.present_since, 0)
        for t in (5, 15, 25, 29):
            self.assertIsNone(self.tick(t)["proposal"])
        self.assertIsNotNone(self.tick(30)["proposal"])

    def test_presence_false_restarts_30_second_timer(self):
        self.tick(0)
        self.tick(20, present=False)
        self.assertIsNone(self.tick(21)["proposal"])
        self.assertIsNone(self.tick(50)["proposal"])
        self.assertIsNotNone(self.tick(51)["proposal"])

    def test_stale_retained_and_unproved_frames_cannot_attach(self):
        for changes in ({"age": 30}, {"retained": True}, {"proof": False}, {"source": "other"},
                        {"phase": "idle"}, {"state": "IDLE"}, {"state": "END"}, {"connected": False}):
            self.setUp()
            for t in (0, 10, 20, 30, 40):
                view = self.tick(t, **changes)
                self.assertIsNone(view["proposal"])
            self.assertFalse(self.policy.active_session)

    def test_disconnect_requires_fresh_message_and_new_continuous_30_seconds(self):
        self.tick(0)
        self.tick(20, connected=False, epoch="lost")
        self.assertEqual(self.policy.view()["reason"], "hub_disconnected")
        session = self.policy.session
        self.assertIsNone(self.tick(21, epoch="back")["proposal"])
        self.assertEqual(self.policy.session, session)
        self.assertIsNone(self.tick(50, epoch="back")["proposal"])
        self.assertIsNotNone(self.tick(51, epoch="back")["proposal"])

    def test_short_disconnect_not_observed_by_policy_still_resets_timer_by_transport_epoch(self):
        self.tick(0)
        self.assertIsNone(self.tick(20, epoch="changed")["proposal"])
        self.assertIsNone(self.tick(30, epoch="changed")["proposal"])
        self.assertIsNone(self.tick(49, epoch="changed")["proposal"])
        self.assertIsNotNone(self.tick(50, epoch="changed")["proposal"])

    def test_reconnect_preserves_handled_on_for_same_observed_session(self):
        p = self.candidate()
        self.policy.respond(p["proposal_id"], "reject")
        session = self.policy.session
        self.tick(31, connected=False, epoch="lost")
        for t in (32, 42, 52, 62):
            self.assertIsNone(self.tick(t, epoch="back")["proposal"])
        self.assertEqual(self.policy.session, session)
        self.assertEqual(self.policy.view()["reason"], "already_handled_this_session")

    def test_gap_without_connection_status_keeps_dedup_and_does_not_invent_new_session(self):
        p = self.candidate()
        self.policy.respond(p["proposal_id"], "reject")
        session = self.policy.session
        self.now = 61
        self.assertEqual(self.policy.view()["reason"], "hub_missing_or_stale")
        for t in (62, 72, 82, 92):
            self.assertIsNone(self.tick(t)["proposal"])
        self.assertEqual(self.policy.session, session)

    def test_observed_idle_boundary_allows_a_new_genuine_session(self):
        p = self.candidate()
        self.policy.respond(p["proposal_id"], "reject")
        old = self.policy.session
        self.tick(31, state="IDLE", present=False)
        self.tick(32, state="START")
        self.tick(42)
        self.tick(52)
        self.assertIsNotNone(self.tick(62)["proposal"])
        self.assertGreater(self.policy.session, old)

    def test_end_invalidates_pending_and_never_proposes_on(self):
        p = self.candidate()
        self.assertIsNone(self.tick(31, state="END")["proposal"])
        with self.assertRaises(ValueError):
            self.policy.respond(p["proposal_id"], "accept", manual=True)
        self.assertFalse(self.policy.active_session)

    def test_contract_change_after_attach_invalidates_qualification(self):
        self.tick(0)
        self.assertIsNone(self.tick(20, phase="idle")["proposal"])
        self.assertFalse(self.policy.active_session)
        self.assertIsNone(self.tick(21)["proposal"])
        self.assertIsNone(self.tick(50)["proposal"])
        self.assertIsNotNone(self.tick(51)["proposal"])

    def test_off_after_observed_absence_boundary_is_preserved_after_attach(self):
        self.tick(0, power="POWER_ON")
        for t in range(10, 610, 10):
            self.assertIsNone(self.tick(t, present=False, power="POWER_ON")["proposal"])
        proposal = self.tick(610, state="IDLE", present=False, reasons=["absent_timeout"], power="POWER_ON")["proposal"]
        self.assertEqual((proposal["operation"], proposal["value"]), ("set_power", "off"))

    def test_auto_bad_persistence_is_preserved_after_attach(self):
        for t in range(0, 60, 10):
            self.assertIsNone(self.tick(t, power="POWER_ON")["proposal"])
        proposal = self.tick(60, power="POWER_ON")["proposal"]
        self.assertEqual((proposal["operation"], proposal["value"]), ("set_mode", "auto"))

    def test_old_boot_and_reversed_sequence_cannot_renew_presence(self):
        self.tick(0)
        self.tick(10, boot="new")
        self.assertIsNone(self.tick(40, boot="test")["proposal"])
        self.assertEqual(self.policy.boot, "new")
        self.assertEqual(self.policy.view()["reason"], "hub_missing_or_stale")


class FeedAttachEvidenceTests(unittest.TestCase):
    def test_feed_preserves_only_safe_active_session_evidence_and_local_transport_status(self):
        feed = PhaseFeed(clock=lambda: 0)
        message = {"schema_version": "1.0", "node": "hub", "boot_id": "test", "seq": 1,
                   "data": {"fsm_state": "FOCUS_NPC", "phase": "focus", "source": "fsm", "present": True,
                            "private_account": "not-for-ui"}}
        feed.set_connected(True)
        feed.observe("deskmate/state/phase", message)
        view = feed.view()
        self.assertEqual(view["envelope"]["data"]["source"], "fsm")
        self.assertEqual(view["envelope"]["data"]["phase"], "focus")
        self.assertNotIn("private_account", view["envelope"]["data"])
        epoch = view["mqtt_epoch"]
        feed.set_connected(False)
        self.assertFalse(feed.view()["mqtt_connected"])
        self.assertNotEqual(feed.view()["mqtt_epoch"], epoch)
        feed.set_connected(True)
        self.assertNotEqual(feed.view()["mqtt_epoch"], epoch)
        feed.observe("deskmate/state/phase", copy.deepcopy(message), retained=True)
        self.assertEqual(feed.view()["envelope"]["seq"], 1)
