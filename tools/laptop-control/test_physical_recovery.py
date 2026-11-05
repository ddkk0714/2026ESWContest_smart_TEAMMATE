import copy
import unittest
from thinq_host import HostSession, Session
from poc.worker import FixtureSdk
import test_demo_absence as demo

class PhysicalRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_verified_operation_can_be_confirmed_without_more_cloud_io(self):
        sdk=FixtureSdk(copy.deepcopy(demo.FIXTURE))
        h=HostSession(Session(sdk,{'enabled':True,'backend':'fixture','power_control':True}))
        p=await h.call(h.key,{'op':'prepare','value':'POWER_OFF'})
        r=await h.call(h.key,{'op':'execute','nonce':p['nonce'],'approved':True,
            'physical_online_confirmed':True,'no_external_change':True})
        self.assertEqual(r['status'],'state_match')
        calls=list(sdk.calls)
        wrong=await h.call(h.key,{'op':'physical_confirmation','value':'POWER_ON','approved':True})
        self.assertEqual(wrong['status'],'blocked')
        self.assertIsNone(h.session.ctx.get('physical'))
        right=await h.call(h.key,{'op':'physical_confirmation','value':'POWER_OFF','approved':True})
        self.assertEqual(right['status'],'user_attestation_recorded')
        self.assertFalse(right['physical_confirmed'])
        self.assertEqual(h.session.ctx['physical'],'POWER_OFF')
        self.assertEqual(sdk.calls,calls)
        self.assertEqual(h.session.ctx['attempted'],['POWER_OFF'])
