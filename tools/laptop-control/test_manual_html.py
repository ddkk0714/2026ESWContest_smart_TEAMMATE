"""Manual HTML boundaries and HTTP end-to-end with synthetic SDK only."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import re
import socket
import subprocess
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
from thinq_host import HostSession, Session, main, make_app
from poc.worker import FixtureSdk

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).parent / 'thinq_vendor/poc/fixture.json').read_text())


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ManualModeCliTests(unittest.TestCase):
    def test_manual_mode_reuses_id_and_binds_only_loopback(self):
        async def no_network(*args, **kwargs):
            self.assertEqual(kwargs['client_id'], 'SYNTHETIC-CLIENT')
            self.assertTrue(kwargs['allow_control'])
            self.assertTrue(kwargs['loopback'])
            self.assertEqual(kwargs['port'], 19092)
        with (patch('sys.argv', ['thinq_host.py', '--live-manual', '--port', '19092']),
              patch('sys.stdin.isatty', return_value=True),
              patch('importlib.metadata.version', return_value='1.0.14'),
              patch('thinq_host.load_or_create_client_id', return_value='SYNTHETIC-CLIENT') as load,
              patch('thinq_host.getpass.getpass', return_value='FAKE-TOKEN-NOT-REAL') as prompt,
              patch('thinq_host.serve', side_effect=no_network),
              contextlib.redirect_stdout(io.StringIO()) as output):
            main()
        load.assert_called_once_with(create=False)
        prompt.assert_called_once()
        self.assertNotIn('FAKE-TOKEN', output.getvalue())

    def test_conflicting_modes_stop_before_pat(self):
        for flag in ('--fixture', '--live-read-only', '--allow-control'):
            with patch('sys.argv', ['thinq_host.py', '--live-manual', flag]), patch('thinq_host.getpass.getpass') as prompt:
                with self.assertRaises(SystemExit):
                    main()
                prompt.assert_not_called()


class ManualHtmlHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sdk = FixtureSdk(FIXTURE)
        self.host = HostSession(Session(self.sdk, {'enabled': True, 'backend': 'live_manual'}))
        self.port, backend = free_port(), free_port()
        self.runner = web.AppRunner(make_app(self.host, port=backend), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, '127.0.0.1', backend).start()
        self.node = subprocess.Popen(['node', 'tools/laptop-control/server.mjs', '--mode', 'live-manual',
                                     '--port', str(self.port), '--thinq-port', str(backend)],
                                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.http = ClientSession()
        self.origin = f'http://127.0.0.1:{self.port}'
        for _ in range(60):
            try:
                response = await self.http.get(self.origin)
                html = await response.text()
                if response.status == 200:
                    break
            except OSError:
                pass
            await asyncio.sleep(.05)
        else:
            self.fail('temporary manual Node server did not start')
        self.key = re.search(r"const token='([^']+)'", html).group(1)
        self.assertIn('data-run-mode="live-manual"', html)
        self.assertIn('thinqTrialEvidence', html)
        self.assertNotIn(self.sdk.device_id, html)

    async def asyncTearDown(self):
        await self.http.close()
        self.node.terminate()
        await asyncio.to_thread(self.node.wait, timeout=5)
        await self.runner.cleanup()

    async def post(self, body, *, route='/api/thinq/call', authenticated=True):
        headers = {'Origin': self.origin, 'X-Deskmate-Key': self.key} if authenticated else {}
        response = await self.http.post(self.origin + route, json=body, headers=headers)
        return response.status, await response.json()

    async def connect(self):
        status, body = await self.post({}, route='/api/thinq/connect')
        self.assertEqual(status, 200)
        self.assertEqual((body['mode'], body['access']), ('live', 'manual_control'))
        self.assertEqual(self.sdk.calls, [])
        status, body = await self.post({'op': 'read'})
        self.assertEqual(body['status'], 'read_ok')

    async def test_http_manual_approvals_readback_physical_and_duplicate_boundaries(self):
        await self.connect()
        self.assertEqual((await self.post({'op': 'prepare', 'value': 'POWER_OFF'}))[1]['status'], 'blocked')
        status, challenge = await self.post({'op': 'prepare', 'value': 'LOW'})
        self.assertEqual(challenge['status'], 'approval_required')
        self.assertNotIn('post', self.sdk.calls)
        execute = {'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                   'physical_online_confirmed': True, 'no_external_change': True}
        self.assertEqual((await self.post(execute, authenticated=False))[0], 403)
        rejected = dict(execute, approved=False)
        self.assertEqual((await self.post(rejected))[1]['status'], 'blocked')
        self.assertNotIn('post', self.sdk.calls)
        _, challenge = await self.post({'op': 'prepare', 'value': 'LOW'})
        execute['nonce'] = challenge['nonce']
        _, reply = await self.post(execute)
        self.assertTrue(reply['api_accepted'])
        self.assertTrue(reply['state_match'])
        self.assertFalse(reply['physical_confirmed'])
        self.assertEqual(self.sdk.calls.count('post'), 1)
        self.assertEqual((await self.post(execute))[1]['status'], 'blocked')
        self.assertEqual((await self.post({'op': 'prepare', 'value': 'AUTO'}))[1]['status'], 'blocked')
        self.assertEqual(self.sdk.calls.count('post'), 1)
        _, physical = await self.post({'op': 'physical_confirmation', 'value': 'LOW', 'approved': True})
        self.assertTrue(physical['physical_confirmed'])
        _, challenge = await self.post({'op': 'prepare', 'value': 'AUTO'})
        self.assertEqual(challenge['status'], 'approval_required')
        execute['nonce'] = challenge['nonce']
        _, reply = await self.post(execute)
        self.assertTrue(reply['api_accepted'] and reply['state_match'])
        self.assertEqual(self.sdk.calls.count('post'), 2)
        self.assertFalse(reply['physical_confirmed'])
        self.assertEqual((await self.post(execute))[1]['status'], 'blocked')
        self.assertEqual((await self.post({'op': 'policy_fixture', 'scenario': 'presence_on'}, route='/api/thinq/policy-fixture'))[0], 403)
        self.assertNotIn(self.sdk.device_id, json.dumps(reply))

    async def test_uncertain_result_cannot_be_unlocked_by_get(self):
        self.sdk.response_loss = True
        await self.connect()
        _, challenge = await self.post({'op': 'prepare', 'value': 'LOW'})
        _, reply = await self.post({'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                                    'physical_online_confirmed': True, 'no_external_change': True})
        self.assertEqual(reply['status'], 'outcome_unconfirmed')
        self.assertFalse(reply['post_enabled'])
        self.assertEqual(self.sdk.calls.count('post'), 1)
        _, read = await self.post({'op': 'read'})
        self.assertFalse(read['post_enabled'])
        self.assertEqual((await self.post({'op': 'prepare', 'value': 'AUTO'}))[1]['status'], 'blocked')
        self.assertEqual(self.sdk.calls.count('post'), 1)


if __name__ == '__main__':
    unittest.main()
