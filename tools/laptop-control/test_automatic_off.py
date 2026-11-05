import copy
import unittest

from thinq_host import HostSession, Session
import test_demo_absence as demo
from poc.worker import FixtureSdk


class AutomaticOffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.frames = demo.DemoAbsenceTests()
        self.frames.setUp()
        self.sdk = FixtureSdk(copy.deepcopy(demo.FIXTURE))
        self.host = HostSession(Session(self.sdk, {'enabled': True, 'backend': 'fixture',
            'power_control': True, 'policy_link': True, 'demo_absence': True,
            'policy_live_post': True, 'policy_stage': 'POWER_OFF', 'automatic_off': True},
            clock=lambda: self.frames.now), clock=lambda: self.frames.now)
        await self.host.call(self.host.key, {'op': 'read'})

    async def call(self, action, t=0, *, present=True, **kwargs):
        return await self.host.call(self.host.key, {'op': 'auto_off', 'action': action,
            **({'phase': self.frames.packet(t, present=present)} if action != 'disarm' else {}), **kwargs})

    async def arm(self):
        return await self.call('arm', approved=True, physical_online_confirmed=True, no_external_change=True)

    async def test_no_consent_never_calls_cloud_or_post(self):
        before = list(self.sdk.calls)
        for t in (1, 3, 6):
            await self.call('tick', t, present=False)
        self.assertEqual(self.sdk.calls, before)
        with self.assertRaises(ValueError):
            await self.call('arm')

    async def test_explicit_consent_one_off_with_readback_never_on_or_retry(self):
        self.assertTrue((await self.arm())['armed'])
        for t in (1, 3, 6):
            r = await self.call('tick', t, present=False)
        self.assertFalse(r['armed'])
        self.assertTrue(r['result']['api_accepted'])
        self.assertTrue(r['result']['state_match'])
        self.assertFalse(r['result']['physical_confirmed'])
        await self.call('tick', 8, present=False)
        self.assertEqual(self.sdk.calls.count('post'), 1)
        self.assertEqual(self.host.session.ctx['attempted'], ['POWER_OFF'])

    async def test_return_after_absence_cancels(self):
        await self.arm()
        await self.call('tick', 1, present=False)
        r = await self.call('tick', 3, present=True)
        self.assertFalse(r['armed'])
        await self.call('tick', 10, present=False)
        self.assertNotIn('post', self.sdk.calls)

    async def test_stale_or_disconnect_cancels_consent(self):
        for disconnected in (False, True):
            await self.asyncSetUp()
            await self.arm()
            self.frames.now = 31
            phase = self.frames.packet(31, present=False, connected=not disconnected, age=31)
            r = await self.host.call(self.host.key, {'op': 'auto_off', 'action': 'tick', 'phase': phase})
            self.assertFalse(r['armed'])
            self.assertNotIn('post', self.sdk.calls)

    async def test_cancel_prevents_off(self):
        await self.arm()
        await self.call('disarm')
        for t in (1, 6, 12):
            await self.call('tick', t, present=False)
        self.assertNotIn('post', self.sdk.calls)

    async def test_target_change_cancels(self):
        await self.arm()
        self.host.session.ctx['private_target'] = 'synthetic-different-target'
        r = await self.call('tick', 1, present=False)
        self.assertFalse(r['armed'])
        self.assertNotIn('post', self.sdk.calls)

    async def test_expired_session_cannot_send(self):
        await self.arm()
        self.frames.now = 1300
        with self.assertRaises(PermissionError):
            await self.call('tick', 1300, present=False)
        self.assertNotIn('post', self.sdk.calls)

    async def test_capability_required_and_other_operations_never_enabled(self):
        self.host.session.ctx['automatic_off'] = False
        with self.assertRaises(ValueError):
            await self.arm()
        self.assertNotIn('post', self.sdk.calls)

    async def test_post_error_is_single_attempt(self):
        await self.arm()
        async def fail(*args, **kwargs):
            raise TimeoutError('synthetic timeout')
        self.sdk.async_post_device_control = fail
        for t in (1, 3, 6, 8):
            await self.call('tick', t, present=False)
        self.assertEqual(self.host.session.ctx['attempted'], ['POWER_OFF'])

    async def test_cancellation_during_fresh_cloud_recheck_prevents_post(self):
        await self.arm()
        original = self.host.session.read
        async def cancelling_read():
            if self.host.auto_off.inflight:
                self.host.auto_off.consent = None
            return await original()
        self.host.session.read = cancelling_read
        for t in (1, 3, 6):
            await self.call('tick', t, present=False)
        self.assertNotIn('post', self.sdk.calls)
        self.assertFalse(self.host.auto_off.view()['armed'])

    async def test_live_prepost_refresh_blocks_returning_presence(self):
        import json
        from unittest.mock import patch, MagicMock
        await self.arm()
        await self.call('tick', 1, present=False)
        self.host.backend = 'live_manual'
        self.host.auto_off.inflight = True
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        frame = self.frames.packet(2, present=True)
        connection.getresponse.return_value.read.return_value = json.dumps({'thinq_phase': frame}).encode()
        with patch('http.client.HTTPConnection', return_value=connection):
            self.host.auto_off.refresh_source()
        self.assertFalse(self.host.auto_off.valid())
        self.assertNotIn('post', self.sdk.calls)
