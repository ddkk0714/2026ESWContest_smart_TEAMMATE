import copy
import unittest

from thinq_host import HostSession, Session
import test_demo_absence as demo
from poc.worker import FixtureSdk


class UnifiedConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.frames = demo.DemoAbsenceTests()
        self.frames.setUp()
        self.sdk = FixtureSdk(copy.deepcopy(demo.FIXTURE))
        self.host = HostSession(Session(self.sdk, {'enabled':True,'backend':'fixture',
            'power_control':True,'policy_link':True,'policy_live_post':True,
            'combined_manual':True,'demo_absence':True,'automatic_off':True},
            clock=lambda:self.frames.now),clock=lambda:self.frames.now)

    async def call(self, op, **body):
        return await self.host.call(self.host.key, {'op':op,**body})

    async def test_all_four_manual_actions_reuse_individual_approval_and_readback(self):
        await self.call('read')
        for value in ('LOW','AUTO','POWER_OFF','POWER_ON'):
            before=self.sdk.calls.count('post')
            prepared=await self.call('prepare',value=value)
            self.assertEqual(prepared['status'],'approval_required')
            self.assertEqual(self.sdk.calls.count('post'),before)
            result=await self.call('execute',nonce=prepared['nonce'],approved=True,
                physical_online_confirmed=True,no_external_change=True)
            self.assertTrue(result['api_accepted'] and result['state_match'])
            self.assertEqual(self.sdk.calls.count('post'),before+1)
            await self.call('physical_confirmation',value=value,approved=True)
        self.assertEqual(self.host.session.ctx['attempted'],['LOW','AUTO','POWER_OFF','POWER_ON'])

    async def test_unapproved_manual_request_cannot_post(self):
        p=await self.call('prepare',value='LOW')
        r=await self.call('execute',nonce=p['nonce'],approved=False,
            physical_online_confirmed=True,no_external_change=True)
        self.assertEqual(r['status'],'blocked')
        self.assertNotIn('post',self.sdk.calls)

    async def test_old_policy_only_mode_still_blocks_direct_manual_prepare(self):
        self.host.session.ctx['combined_manual']=False
        self.assertEqual((await self.call('prepare',value='LOW'))['status'],'blocked')
        self.assertNotIn('post',self.sdk.calls)

    async def test_auto_permission_is_still_off_only_in_combined_mode(self):
        self.assertTrue(self.host.auto_off.enabled())
        self.assertFalse(self.host.check_policy_post({'automatic_off':True,'value':'POWER_ON'},{}))

    async def test_manual_prepare_cancels_automatic_consent(self):
        self.host.auto_off.consent={'synthetic':True}
        await self.call('prepare',value='LOW')
        self.assertIsNone(self.host.auto_off.consent)

    async def test_untrusted_request_cannot_turn_on_combined_mode(self):
        self.host.session.ctx['combined_manual']=False
        with self.assertRaises(ValueError):
            await self.call('prepare',value='LOW',combined_manual=True)
        self.assertNotIn('post',self.sdk.calls)
