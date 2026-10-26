import asyncio
import json
import unittest
from pathlib import Path

from thinq_host import GuardedSdk, HostSession, Session, StopTrial, make_app
from poc.worker import FixtureSdk
from aiohttp.test_utils import TestClient, TestServer


class HostTests(unittest.IsolatedAsyncioTestCase):
    def make_host(self):
        fixture = json.loads((Path(__file__).parent / 'thinq_vendor/poc/fixture.json').read_text())
        self.sdk = FixtureSdk(fixture)
        self.now = 10
        self.host = HostSession(Session(self.sdk, {'enabled': True, 'backend': 'live_manual'},
                                        clock=lambda: self.now), clock=lambda: self.now)
        self.key = self.host.claim()['key']

    async def test_full_trial_requires_fresh_approval_and_physical_confirmation(self):
        self.make_host()
        self.assertIsNone(self.host.claim())
        read = await self.host.call(self.key, {'op': 'read'})
        self.assertEqual(read['status'], 'read_ok')
        self.assertNotIn(self.sdk.device_id, json.dumps(read))
        self.assertNotIn('context', read)
        for value in ('LOW', 'AUTO'):
            challenge = await self.host.call(self.key, {'op': 'prepare', 'value': value})
            self.assertEqual(challenge['status'], 'approval_required')
            execute = {'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                       'physical_online_confirmed': True, 'no_external_change': True}
            result = await self.host.call(self.key, execute)
            self.assertEqual(result['status'], 'state_match')
            self.assertFalse(result['physical_confirmed'])
            self.assertEqual((await self.host.call(self.key, execute))['status'], 'blocked')
            if value == 'LOW':
                self.assertEqual((await self.host.call(self.key, {'op': 'prepare', 'value': 'AUTO'}))['status'], 'blocked')
            physical = await self.host.call(self.key, {'op': 'physical_confirmation', 'value': value, 'approved': True})
            self.assertTrue(physical['physical_confirmed'])
        self.assertEqual(self.sdk.calls.count('post'), 2)

    async def test_expiry_and_unknown_fields_block_without_post(self):
        self.make_host()
        with self.assertRaises(PermissionError):
            await self.host.call('wrong', {'op': 'read'})
        with self.assertRaises(ValueError):
            await self.host.call(self.key, {'op': 'read', 'access_token': 'secret'})
        challenge = await self.host.call(self.key, {'op': 'prepare', 'value': 'LOW'})
        self.now += 31
        result = await self.host.call(self.key, {'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                                               'physical_online_confirmed': True, 'no_external_change': True})
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('post', self.sdk.calls)
        self.now += 1200
        with self.assertRaises(PermissionError):
            await self.host.call(self.key, {'op': 'read'})

    async def test_response_loss_blocks_retry_and_revoke_ends_access(self):
        self.make_host()
        self.sdk.response_loss = True
        challenge = await self.host.call(self.key, {'op': 'prepare', 'value': 'LOW'})
        result = await self.host.call(self.key, {'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                                               'physical_online_confirmed': True, 'no_external_change': True})
        self.assertEqual(result['status'], 'outcome_unconfirmed')
        self.assertEqual((await self.host.call(self.key, {'op': 'prepare', 'value': 'LOW'}))['status'], 'blocked')
        self.assertEqual(self.sdk.calls.count('post'), 1)
        await self.host.call(self.key, {'op': 'revoke'})
        with self.assertRaises(PermissionError):
            await self.host.call(self.key, {'op': 'read'})

    async def test_http_bootstrap_host_and_key_boundaries(self):
        self.make_host()
        self.host.claimed = False
        client = TestClient(TestServer(make_app(self.host)))
        await client.start_server()
        try:
            headers = {'Host': '127.0.0.1:8792'}
            self.assertEqual((await client.get('/bootstrap', headers=headers)).status, 403)
            headers['X-Deskmate-Bootstrap'] = '1'
            response = await client.get('/bootstrap', headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual((await client.get('/bootstrap', headers=headers)).status, 409)
            self.assertEqual((await client.post('/call', headers=headers, json={'op': 'read'})).status, 401)
            headers['X-Deskmate-Session'] = self.key
            self.assertEqual((await client.post('/call', headers=headers, json={'op': 'read'})).status, 200)
            self.assertEqual((await client.post('/call', headers={**headers, 'Host': 'attacker.invalid'}, json={'op': 'read'})).status, 403)
        finally:
            await client.close()

    async def test_guard_only_consumes_matching_single_use_permit(self):
        class Transport:
            target = None
            def select(self, target): self.target = target
            def approve(self, value): self.value = value
        class Sdk:
            async def async_post_device_control(self, target, payload, **kwargs): return payload
        transport = Transport()
        guard = GuardedSdk(Sdk(), transport)
        from poc.session import Permit
        guard.permit = Permit(True)
        with self.assertRaises(StopTrial):
            await guard.async_post_device_control('device', {'airFlow': {'windStrength': 'LOW'}})
        guard.permit.approve('LOW')
        await guard.async_post_device_control('device', {'airFlow': {'windStrength': 'LOW'}})
        self.assertIsNone(guard.permit.value)
        with self.assertRaises(StopTrial):
            await guard.async_post_device_control('device', {'airFlow': {'windStrength': 'LOW'}})
        with self.assertRaises(StopTrial):
            await guard.async_post_device_control('other', {'airFlow': {'windStrength': 'AUTO'}})

    async def test_official_sdk_transport_and_approval_work_together(self):
        self.make_host()
        from thinqconnect.thinq_api import ThinQApi
        from thinq_host import Transport
        fixture_sdk = self.sdk
        requests = []

        class Response:
            status, ok = 200, True
            def __init__(self, body): self.body = body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self): return {'response': self.body}

        class Http:
            async def request(self, method, url, **kwargs):
                requests.append((method, str(url)))
                self.assert_no_redirect = kwargs['allow_redirects'] is False
                if method == 'POST':
                    fixture_sdk.wind = kwargs['json']['airFlow']['windStrength']
                    return Response({})
                if str(url).endswith('/devices'):
                    return Response(await fixture_sdk.async_get_device_list())
                if str(url).endswith('/profile'):
                    return Response(await fixture_sdk.async_get_device_profile('device'))
                return Response(await fixture_sdk.async_get_device_status('device'))

        http = Http()
        transport = Transport(http, execute=True)
        guarded = GuardedSdk(ThinQApi(session=transport, access_token='test-secret-never-public',
                                     country_code='KR', client_id='test-client'), transport)
        host = HostSession(Session(guarded, {'enabled': True, 'backend': 'live_manual'}))
        key = host.claim()['key']
        challenge = await host.call(key, {'op': 'prepare', 'value': 'LOW'})
        self.assertEqual(challenge['status'], 'approval_required')
        result = await host.call(key, {'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                                       'physical_online_confirmed': True, 'no_external_change': True})
        self.assertEqual(result['status'], 'state_match')
        self.assertNotIn('test-secret-never-public', json.dumps(result))
        self.assertEqual(sum(method == 'POST' for method, _ in requests), 1)
        self.assertTrue(all(url.startswith('https://api-kic.lgthinq.com/') for _, url in requests))
        self.assertTrue(http.assert_no_redirect)


if __name__ == '__main__':
    unittest.main()
