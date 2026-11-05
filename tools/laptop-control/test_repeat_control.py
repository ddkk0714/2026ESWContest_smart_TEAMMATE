"""Repeated control in the unified console: new intents vs resends. Synthetic SDK only; no PAT, Cloud, MQTT or boards.

A repeated value is a new, separately approved intent (new nonce, at most one POST). An uncertain outcome still locks
every new intent until a fresh GET is reviewed (reconcile), and nothing is ever sent again automatically.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

from thinq_host import HostSession, Session
from poc.worker import FixtureSdk
from tools.thinq_connect.manual_trial import StopTrial, Transport

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hub" / "deskmate_hub" / "control"))
from air_purifier_policy import AirPurifierPolicy  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text())
POLICY = json.loads((Path(__file__).resolve().parents[2] / "hub/deskmate_hub/config/air_purifier_policy.yaml").read_text())


async def no_sleep(_):
    return None


class LaggingSdk(FixtureSdk):
    """Cloud state that shows the old value for `lag` GETs after a POST (device already changed)."""

    def __init__(self, fixture, lag=0):
        super().__init__(fixture)
        self.lag, self.stale = lag, 0
        self.shown_power = self.power

    async def async_post_device_control(self, target, payload, **kwargs):
        result = await super().async_post_device_control(target, payload, **kwargs)
        self.stale = self.lag
        return result

    async def async_get_device_status(self, target, **kwargs):
        state = await super().async_get_device_status(target, **kwargs)
        if self.stale:
            self.stale -= 1
            state["operation"]["airPurifierOperationMode"] = self.shown_power
        else:
            self.shown_power = self.power
        return state


class IntentTests(unittest.IsolatedAsyncioTestCase):
    def make(self, sdk=None, **ctx):
        self.now = 0.
        self.sdk = sdk or FixtureSdk(copy.deepcopy(FIXTURE))
        context = {"enabled": True, "backend": "fixture", "power_control": True, "combined_manual": True,
                   "intent_control": True, **ctx}
        self.host = HostSession(Session(self.sdk, context, clock=lambda: self.now, sleep=no_sleep),
                                clock=lambda: self.now)

    async def call(self, op, **data):
        return await self.host.call(self.host.key, {"op": op, **data})

    async def execute(self, value):
        prepared = await self.call("prepare", value=value)
        self.assertEqual(prepared["status"], "approval_required", prepared)
        result = await self.call("execute", nonce=prepared["nonce"], approved=True,
                                 physical_online_confirmed=True, no_external_change=True)
        self.now += 6   # past MIN_POST_INTERVAL
        return prepared, result

    async def test_on_off_on_in_one_session_each_a_new_intent(self):
        """Two-person script: A turns it on, leaves (off), B arrives (on) — one auth session, no body check needed."""
        self.make()
        self.sdk.power = "POWER_OFF"
        for value in ("POWER_ON", "POWER_OFF", "POWER_ON"):
            _, result = await self.execute(value)
            self.assertEqual(result["status"], "state_match", result)
        self.assertEqual(self.sdk.calls.count("post"), 3)
        self.assertEqual(self.sdk.power, "POWER_ON")

    async def test_nonce_is_single_use_never_a_second_post(self):
        self.make()
        prepared, result = await self.execute("POWER_OFF")
        self.assertEqual(result["status"], "state_match")
        again = await self.call("execute", nonce=prepared["nonce"], approved=True,
                                physical_online_confirmed=True, no_external_change=True)
        self.assertEqual((again["status"], again["reason"]), ("blocked", "approval_invalid_or_expired"))
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_rapid_repeat_waits_min_interval_with_a_public_reason(self):
        self.make()
        prepared = await self.call("prepare", value="POWER_OFF")
        await self.call("execute", nonce=prepared["nonce"], approved=True,
                        physical_online_confirmed=True, no_external_change=True)
        self.now += 1
        blocked = await self.call("prepare", value="POWER_ON")
        self.assertEqual((blocked["status"], blocked["reason"]), ("blocked", "too_soon_after_previous_post"))
        self.now += 5
        self.assertEqual((await self.call("prepare", value="POWER_ON"))["status"], "approval_required")

    async def test_manual_auto_from_low_without_a_low_in_this_session(self):
        self.make()
        self.sdk.wind = "LOW"
        _, result = await self.execute("AUTO")
        self.assertEqual(result["status"], "state_match")
        _, result = await self.execute("LOW")
        self.assertEqual(result["status"], "state_match")
        _, result = await self.execute("AUTO")
        self.assertEqual(result["status"], "state_match")

    async def test_cloud_lag_is_reread_with_gets_only(self):
        self.make(LaggingSdk(copy.deepcopy(FIXTURE), lag=2))
        _, result = await self.execute("POWER_OFF")
        self.assertEqual(result["status"], "state_match", result)
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_lag_longer_than_rereads_stays_uncertain_and_locked(self):
        self.make(LaggingSdk(copy.deepcopy(FIXTURE), lag=10))
        _, result = await self.execute("POWER_OFF")
        self.assertEqual(result["status"], "outcome_unconfirmed")
        blocked = await self.call("prepare", value="POWER_ON")
        self.assertEqual((blocked["status"], blocked["reason"]), ("blocked", "post_disabled_or_outcome_uncertain"))
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_reconcile_needs_approval_rereads_and_never_resends(self):
        self.make()
        self.sdk.response_loss = True
        _, result = await self.execute("POWER_OFF")
        self.assertEqual(result["status"], "outcome_unconfirmed")
        self.sdk.response_loss = False
        refused = await self.call("reconcile")
        self.assertEqual((refused["status"], refused["reason"]), ("blocked", "reconcile_not_approved"))
        posts = self.sdk.calls.count("post")
        done = await self.call("reconcile", approved=True)
        self.assertEqual(done["status"], "reconciled")
        self.assertTrue(done["post_enabled"])
        self.assertEqual(done["state"]["power"], "POWER_OFF")
        self.assertEqual(self.sdk.calls.count("post"), posts)          # a GET, not a resend
        _, result = await self.execute("POWER_ON")                      # new intent allowed again
        self.assertEqual(result["status"], "state_match")

    async def test_reconcile_with_unreadable_state_keeps_the_lock(self):
        self.make()
        self.sdk.response_loss = True
        await self.execute("POWER_OFF")
        self.sdk.fail = (500, None)
        failed = await self.call("reconcile", approved=True)
        self.assertNotEqual(failed["status"], "reconciled")
        self.sdk.fail = None
        self.assertEqual((await self.call("prepare", value="POWER_ON"))["reason"], "post_disabled_or_outcome_uncertain")

    async def test_reconcile_only_after_an_uncertain_outcome(self):
        self.make()
        refused = await self.call("reconcile", approved=True)
        self.assertEqual(refused["reason"], "reconcile_requires_uncertain_outcome")

    async def test_trial_mode_keeps_one_post_per_value(self):
        """Without intent_control (CLI/manual trial) nothing changes: one POST per value and body check gating."""
        self.make(intent_control=False, combined_manual=False)
        await self.execute("POWER_OFF")
        blocked = await self.call("prepare", value="POWER_ON")
        self.assertEqual((blocked["status"], blocked["reason"]), ("blocked", "previous_physical_confirmation_required"))
        await self.call("physical_confirmation", value="POWER_OFF", approved=True)
        await self.execute("POWER_ON")
        await self.call("physical_confirmation", value="POWER_ON", approved=True)
        blocked = await self.call("prepare", value="POWER_OFF")
        self.assertEqual(blocked["reason"], "stage_forbidden")
        self.assertEqual(self.sdk.calls.count("post"), 2)


class TransportTests(unittest.TestCase):
    def test_intent_control_allows_a_repeated_value_one_permit_at_a_time(self):
        for intents in (False, True):
            t = Transport(None, execute=True, power_control=True, intent_control=intents)
            t.approve("POWER_ON")
            with self.assertRaises(StopTrial):
                t.approve("POWER_OFF")                       # never two permits in flight
            t.permit, _ = None, t.attempted.add("POWER_ON")  # what request() does before I/O
            if intents:
                t.approve("POWER_ON")
                self.assertEqual(t.permit, "POWER_ON")
            else:
                with self.assertRaises(StopTrial):
                    t.approve("POWER_ON")

    def test_auto_needs_prior_low_only_in_trial_mode(self):
        with self.assertRaises(StopTrial):
            Transport(None, execute=True).approve("AUTO")
        t = Transport(None, execute=True, intent_control=True)
        t.approve("AUTO")
        self.assertEqual(t.permit, "AUTO")


def phase(state, present, seq):
    ph = {"START": "start", "IDLE": "idle"}.get(state, "focus")
    return {"mqtt_connected": True, "mqtt_epoch": "e1", "retained": False, "age_sec": 0,
            "envelope": {"schema_version": "1.0", "node": "hub", "boot_id": "b1", "seq": seq,
                         "data": {"fsm_state": state, "present": present, "source": "fsm", "phase": ph, "reasons": []}}}


def snap(power, at):
    return {"api_status": "ok", "receipt_stale": False, "connection": "unknown", "received_at": at, "api_age_sec": 0,
            "expires_at": at + 120,
            "fields": {"power": {"value": power, "valid": True, "status": "ok"},
                       "wind_strength": {"value": "AUTO", "valid": True, "status": "ok"},
                       "total_pollution_level": {"value": "GOOD", "valid": True, "status": "ok"}}}


class PolicyOwnCommandTests(unittest.TestCase):
    def setUp(self):
        self.t, self.seq = 1000.0, 0
        self.p = AirPurifierPolicy(POLICY, clock=lambda: self.t, manual_review=True, demo_absence=True)

    def tick(self, state, present, power, dt=5, new_get=True):
        self.t += dt
        self.seq += 1
        return self.p.observe(phase(state, present, self.seq), snap(power, self.t if new_get else 1000.0))

    def accept(self, view, value):
        self.assertIsNotNone(view["proposal"], view["reason"])
        self.assertEqual(view["proposal"]["value"], value)
        self.p.respond(view["proposal"]["proposal_id"], "accept", manual=True)

    def sit_until_on_proposal(self, power="POWER_OFF"):
        view = None
        for _ in range(8):
            view = self.tick("FOCUS_PC", True, power)
        return view

    def test_own_on_is_not_manual_and_absence_then_proposes_off(self):
        self.tick("START", True, "POWER_OFF")
        self.accept(self.sit_until_on_proposal(), "on")
        self.p.expect("POWER_ON")                          # host: execute → state_match
        view = self.tick("FOCUS_PC", True, "POWER_ON")
        self.assertNotEqual(view["reason"], "manual_override")
        self.assertLess(self.p.manual_until, self.t)
        for _ in range(3):
            view = self.tick("FOCUS_PC", False, "POWER_ON")
        self.accept(view, "off")

    def test_after_own_off_the_next_person_gets_a_new_on_proposal(self):
        self.tick("START", True, "POWER_OFF")
        self.accept(self.sit_until_on_proposal(), "on")
        self.p.expect("POWER_ON")
        self.tick("FOCUS_PC", True, "POWER_ON")
        for _ in range(3):
            view = self.tick("FOCUS_PC", False, "POWER_ON")
        self.accept(view, "off")
        self.p.expect("POWER_OFF")
        self.tick("FOCUS_PC", False, "POWER_OFF")
        self.accept(self.sit_until_on_proposal(), "on")     # same FSM session, new stay

    def test_unexplained_change_still_holds_as_manual(self):
        self.tick("START", True, "POWER_OFF")
        self.sit_until_on_proposal()
        self.tick("FOCUS_PC", True, "POWER_ON")            # someone else turned it on (pending proposal dropped)
        self.assertGreater(self.p.manual_until, self.t)
        view = self.tick("FOCUS_PC", True, "POWER_ON")
        self.assertEqual(view["reason"], "manual_override")


if __name__ == "__main__":
    unittest.main()


class SelectionReasonTests(unittest.IsolatedAsyncioTestCase):
    async def read_reason(self, devices):
        sdk = FixtureSdk(copy.deepcopy(FIXTURE))

        async def device_list(**kwargs):
            return devices
        sdk.async_get_device_list = device_list
        session = Session(sdk, {"enabled": False, "backend": "fixture"})
        return (await session.safe({"op": "read", "backend": "fixture"}))["public"].get("reason")

    async def test_device_selection_stops_have_public_codes(self):
        purifier = {"deviceId": "x", "deviceInfo": {"deviceType": "DEVICE_AIR_PURIFIER", "modelName": "AIR_910604_WW",
                                                    "reportable": True}}
        other = {"deviceId": "y", "deviceInfo": {"deviceType": "DEVICE_AIR_PURIFIER", "modelName": "OTHER",
                                                 "reportable": True}}
        washer = {"deviceId": "z", "deviceInfo": {"deviceType": "DEVICE_WASHER", "modelName": "W", "reportable": True}}
        self.assertEqual(await self.read_reason([]), "device_list_empty")
        self.assertEqual(await self.read_reason([washer]), "purifier_not_in_account")
        self.assertEqual(await self.read_reason([other]), "purifier_model_differs")
        self.assertEqual(await self.read_reason([purifier, dict(purifier, deviceId="w")]), "purifier_multiple")
        silent = {"deviceId": "x", "deviceInfo": dict(purifier["deviceInfo"], reportable=False)}
        self.assertEqual(await self.read_reason([silent]), "purifier_not_reporting")


class ConsoleCommandPolicyTests(PolicyOwnCommandTests):
    def test_console_on_then_absence_still_proposes_off(self):
        """User turned it on with the console button (not a proposal), then left: absence OFF is not held."""
        self.tick("START", True, "POWER_OFF")
        self.p.expect("POWER_ON", manual=True)
        self.tick("FOCUS_PC", True, "POWER_ON")
        for _ in range(3):
            view = self.tick("FOCUS_PC", False, "POWER_ON")
        self.accept(view, "off")

    def test_console_off_while_present_is_respected(self):
        self.tick("START", True, "POWER_ON")
        self.p.expect("POWER_OFF", manual=True)
        view = self.sit_until_on_proposal()
        self.assertIsNone(view["proposal"])
        self.assertEqual(view["reason"], "manual_override")


class UnifiedAutoOffTests(unittest.IsolatedAsyncioTestCase):
    """Unified console (combined manual, intent_control): absence OFF reads ThinQ itself and may be re-armed."""

    async def asyncSetUp(self):
        import test_demo_absence as demo
        self.frames = demo.DemoAbsenceTests()
        self.frames.setUp()
        self.sdk = FixtureSdk(copy.deepcopy(demo.FIXTURE))
        self.host = HostSession(Session(self.sdk, {
            'enabled': True, 'backend': 'fixture', 'power_control': True, 'policy_link': True, 'demo_absence': True,
            'policy_live_post': True, 'combined_manual': True, 'intent_control': True, 'automatic_off': True},
            clock=lambda: self.frames.now, sleep=no_sleep), clock=lambda: self.frames.now)
        await self.host.call(self.host.key, {'op': 'read'})

    async def call(self, action, t=0, *, present=True, **kwargs):
        return await self.host.call(self.host.key, {'op': 'auto_off', 'action': action,
                                                    'phase': self.frames.packet(t, present=present), **kwargs})

    async def arm(self, t=0):
        return await self.call('arm', t, approved=True, physical_online_confirmed=True, no_external_change=True)

    async def test_stale_snapshot_is_refreshed_by_one_get_then_off(self):
        self.assertTrue((await self.arm())['armed'])
        for t in range(10, 200, 10):                   # sat 3+ minutes after the last manual read (hub keeps publishing)
            await self.call('tick', t, present=True)
        reads_before = self.sdk.calls.count('state')
        for t in (200, 202, 206):                      # then left
            r = await self.call('tick', t, present=False)
        self.assertEqual(r['result']['status'], 'state_match', r)
        self.assertGreater(self.sdk.calls.count('state'), reads_before)
        self.assertEqual(self.sdk.calls.count('post'), 1)
        self.assertEqual(self.sdk.power, 'POWER_OFF')

    async def test_one_consent_runs_off_then_on_then_off_again(self):
        """Demo auto mode: no question per proposal; one POST each; consent stands through return and re-absence."""
        await self.arm()
        for t in (1, 3, 6):
            r = await self.call('tick', t, present=False)
        self.assertEqual((r['result']['status'], r['result']['value']), ('state_match', 'POWER_OFF'), r)
        self.assertTrue(r['armed'])
        for t in range(8, 50, 2):                      # comes back and sits 30 s+
            r = await self.call('tick', t, present=True)
        self.assertEqual(self.sdk.power, 'POWER_ON', r)
        self.assertEqual(r['result']['value'], 'POWER_ON')
        for t in (52, 54, 58):                         # leaves again
            r = await self.call('tick', t, present=False)
        self.assertEqual(self.sdk.power, 'POWER_OFF', r)
        self.assertEqual(self.sdk.calls.count('post'), 3)
        self.assertEqual(r['runs'], 3)
        for t in range(60, 80, 2):                     # still away: nothing more is sent
            await self.call('tick', t, present=False)
        self.assertEqual(self.sdk.calls.count('post'), 3)

    async def test_off_when_the_hub_fsm_ends_the_session_on_absence(self):
        """Real hub in fast demo: leaving goes FSM -> IDLE (absent_timeout), which ends the policy session."""
        await self.arm()

        def idle(t):
            p = self.frames.packet(t, present=False)
            p['envelope']['data'].update(fsm_state='IDLE', phase='idle', reasons=['absent_timeout'])
            return p
        for t in (2, 4, 6):
            r = await self.host.call(self.host.key, {'op': 'auto_off', 'action': 'tick', 'phase': idle(t)})
        self.assertEqual((r['result']['status'], r['result']['value']), ('state_match', 'POWER_OFF'), r)
        self.assertEqual(self.sdk.calls.count('post'), 1)

    async def test_console_button_does_not_drop_the_standing_consent(self):
        await self.arm()
        prepared = await self.host.call(self.host.key, {'op': 'prepare', 'value': 'POWER_OFF'})
        self.assertEqual(prepared['status'], 'approval_required')
        self.assertTrue(self.host.auto_off.view()['armed'])

    async def test_disarm_stops_the_demo_auto_mode(self):
        await self.arm()
        await self.host.call(self.host.key, {'op': 'auto_off', 'action': 'disarm'})
        for t in (1, 3, 6, 9):
            r = await self.call('tick', t, present=False)
        self.assertFalse(r['armed'])
        self.assertEqual(self.sdk.calls.count('post'), 0)


class AbsenceAgainTests(PolicyOwnCommandTests):
    def test_ignored_off_proposal_comes_back_on_the_next_absence(self):
        self.tick("START", True, "POWER_ON")
        self.tick("FOCUS_PC", True, "POWER_ON")
        for _ in range(3):
            view = self.tick("FOCUS_PC", False, "POWER_ON")
        self.assertEqual(view["proposal"]["value"], "off")
        for _ in range(8):                             # proposal expires unanswered (TTL 30 s), still away
            view = self.tick("FOCUS_PC", False, "POWER_ON")
        self.assertEqual(view["reason"], "already_handled_this_session")
        self.tick("FOCUS_PC", True, "POWER_ON")        # comes back, then leaves again
        for _ in range(3):
            view = self.tick("FOCUS_PC", False, "POWER_ON")
        self.assertIsNotNone(view["proposal"], view["reason"])
        self.assertEqual(view["proposal"]["value"], "off")


class AutoWindDetailTests(IntentTests):
    async def test_power_intent_ignores_auto_wind_detail_drift(self):
        """In AUTO the device changes windStrengthDetail by itself between prepare and execute."""
        self.make()
        sdk = self.sdk
        original = sdk.async_get_device_status
        reads = {"n": 0}

        async def drifting(target, **kwargs):
            state = await original(target, **kwargs)
            reads["n"] += 1
            state["airFlow"]["windStrengthDetail"] = "LOW" if reads["n"] % 2 else "HIGH"
            return state
        sdk.async_get_device_status = drifting
        _, result = await self.execute("POWER_OFF")
        self.assertEqual(result["status"], "state_match", result)

    async def test_power_change_between_prepare_and_execute_still_blocks(self):
        self.make()
        prepared = await self.call("prepare", value="POWER_OFF")
        self.sdk.power = "POWER_OFF"                    # someone else turned it off in between
        result = await self.call("execute", nonce=prepared["nonce"], approved=True,
                                 physical_online_confirmed=True, no_external_change=True)
        self.assertEqual((result["status"], result["reason"]), ("blocked", "external_change_detected"))
        self.assertEqual(self.sdk.calls.count("post"), 0)
