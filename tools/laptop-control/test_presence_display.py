"""Observed absence is display data, independent of fast mode/PAT/control."""
import unittest

from thinq_observer import PhaseFeed


class PresenceDisplayTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.feed = PhaseFeed(clock=lambda: self.now)
        self.feed.set_connected(True)

    def sample(self, *, seq=1, present=False, boot="synthetic-display", retained=False):
        self.feed.observe('deskmate/state/phase', {
            "schema_version": "1.0", "node": "hub", "boot_id": boot, "seq": seq,
            "data": {"source": "fsm", "fsm_state": "FOCUS_NPC", "phase": "focus", "present": present,
                     "sensor_summary": {"demo_mode": {"speed": "normal", "enabled": True}}}}, retained=retained)

    def view(self):
        return self.feed.view()["presence_observation"]

    def test_absence_duration_advances_in_normal_mode_without_prior_presence(self):
        self.sample()
        self.now = 12
        self.assertEqual(self.view(), {"fresh": True, "present": False, "absence_elapsed_sec": 12})
        self.assertEqual(self.feed.view()["envelope"]["data"]["sensor_summary"]["demo_mode"]["speed"], "normal")

    def test_returning_presence_resets_display_and_new_absence_starts_from_zero(self):
        self.sample()
        self.now = 5
        self.sample(seq=2, present=True)
        self.assertEqual(self.view()["absence_elapsed_sec"], 0)
        self.now = 6
        self.sample(seq=3)
        self.now = 10
        self.assertEqual(self.view()["absence_elapsed_sec"], 4)

    def test_duplicate_reverse_and_retained_packets_cannot_renew_freshness(self):
        self.sample(seq=2)
        self.now = 20
        self.sample(seq=2)
        self.sample(seq=1)
        self.sample(seq=3, retained=True)
        self.assertEqual(self.feed.received, 0)
        self.now = self.feed.observation_max_age + 1
        self.assertFalse(self.view()["fresh"])
        self.assertIsNone(self.view()["present"])
        self.assertEqual(self.view()["absence_elapsed_sec"], 0)

    def test_disconnect_requires_a_new_packet_and_does_not_count_the_gap(self):
        self.sample()
        self.now = 5
        self.feed.set_connected(False)
        self.assertFalse(self.view()["fresh"])
        self.now = 6
        self.feed.set_connected(True)
        self.assertFalse(self.view()["fresh"])
        self.sample(seq=2)
        self.now = 10
        self.assertEqual(self.view()["absence_elapsed_sec"], 4)

    def test_stale_gap_and_hub_reboot_restart_observed_interval(self):
        self.sample()
        self.now = self.feed.observation_max_age + 1
        self.sample(seq=2)
        self.assertEqual(self.view()["absence_elapsed_sec"], 0)
        self.now += 4
        self.assertEqual(self.view()["absence_elapsed_sec"], 4)
        self.sample(seq=0, boot="synthetic-new-boot")
        self.assertEqual(self.view()["absence_elapsed_sec"], 0)
