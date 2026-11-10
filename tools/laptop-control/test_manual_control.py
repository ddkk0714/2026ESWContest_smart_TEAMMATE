"""Synthetic CLI trials only. No PAT, Cloud, broker or board access."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manual_control import main, run_trial
from thinq_host import HostSession, Session, load_or_create_client_id
from poc.worker import FixtureSdk
from tools.thinq_connect.manual_trial import AUTO_APPROVAL, LOW_APPROVAL, PHYSICAL_AUTO, PHYSICAL_LOW

FIXTURE = json.loads((Path(__file__).parent / 'thinq_vendor/poc/fixture.json').read_text())


class ManualTrialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sdk = FixtureSdk(copy.deepcopy(FIXTURE))
        self.now = 0.
        self.host = HostSession(Session(self.sdk, {"enabled": True, "backend": "live_manual"}, clock=lambda: self.now), clock=lambda: self.now)
        self.records = []

    async def run_commands(self, commands, *, manual=True):
        answers = iter(commands)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = await run_trial(self.host, manual=manual, confirm=lambda _: next(answers), emit=self.records.append)
        public = output.getvalue() + json.dumps(self.records)
        self.assertNotIn(self.sdk.device_id, public)
        self.assertNotIn('nonce', public)
        self.assertTrue(self.host.revoked)
        self.assertFalse(self.host.session.ctx['enabled'])
        return result

    async def test_default_get_only_never_prompts_or_posts(self):
        self.assertEqual(await self.run_commands([], manual=False), 0)
        self.assertEqual(self.sdk.calls, ['devices', 'profile', 'state'])
        self.assertFalse(self.records[0]['control_permission_verified'])
        self.assertFalse(self.records[0]['physical_online_confirmed'])

    async def test_separate_low_auto_approvals_and_physical_records(self):
        answers = ['LOW', LOW_APPROVAL, PHYSICAL_LOW, 'AUTO', AUTO_APPROVAL, PHYSICAL_AUTO]
        self.assertEqual(await self.run_commands(answers), 0)
        self.assertEqual(self.sdk.calls.count('post'), 2)
        self.assertEqual(self.sdk.wind, 'AUTO')
        post = [r for r in self.records if r['event'] == 'post_response']
        readback = [r for r in self.records if r['event'] == 'get_readback']
        physical = [r for r in self.records if r['event'] == 'physical_confirmation']
        self.assertEqual([r['value'] for r in post], ['LOW', 'AUTO'])
        self.assertTrue(all(r['api_accepted'] and not r['automatic_resend'] for r in post))
        self.assertTrue(all(r['state_match'] and not r['physical_confirmed'] for r in readback))
        self.assertTrue(all(r['confirmed'] and r['evidence'] == 'user_attestation' for r in physical))

    async def test_low_is_not_sent_without_exact_approval(self):
        self.assertEqual(await self.run_commands(['LOW', 'CANCEL']), 0)
        self.assertNotIn('post', self.sdk.calls)

    async def test_quit_after_low_never_restores_automatically(self):
        self.assertEqual(await self.run_commands(['LOW', LOW_APPROVAL, PHYSICAL_LOW, 'QUIT']), 0)
        self.assertEqual(self.sdk.calls.count('post'), 1)
        self.assertEqual(self.sdk.wind, 'LOW')

    async def test_unconfirmed_physical_low_blocks_auto_and_ends_session(self):
        self.assertEqual(await self.run_commands(['LOW', LOW_APPROVAL, 'UNKNOWN']), 1)
        self.assertEqual(self.sdk.calls.count('post'), 1)
        self.assertFalse(self.records[-2]['confirmed'])

    async def test_auto_before_verified_low_is_blocked(self):
        self.assertEqual(await self.run_commands(['AUTO']), 1)
        self.assertNotIn('post', self.sdk.calls)

    async def test_duplicate_low_cannot_send_twice(self):
        self.assertEqual(await self.run_commands(['LOW', LOW_APPROVAL, PHYSICAL_LOW, 'LOW']), 1)
        self.assertEqual(self.sdk.calls.count('post'), 1)

    async def test_response_loss_or_mismatch_stops_without_retry_or_restore(self):
        for field in ('response_loss', 'mismatch'):
            with self.subTest(field=field):
                self.setUp()
                setattr(self.sdk, field, True)
                self.assertEqual(await self.run_commands(['LOW', LOW_APPROVAL]), 1)
                self.assertEqual(self.sdk.calls.count('post'), 1)
                self.assertFalse(any(r['event'] == 'physical_confirmation' for r in self.records))

    async def test_timeout_after_post_never_retries_even_if_readback_matches(self):
        original = self.sdk.async_post_device_control
        async def timeout(*args, **kwargs):
            await original(*args, **kwargs)
            raise TimeoutError()
        self.sdk.async_post_device_control = timeout
        self.assertEqual(await self.run_commands(['LOW', LOW_APPROVAL]), 1)
        self.assertEqual(self.sdk.calls.count('post'), 1)
        post = next(r for r in self.records if r['event'] == 'post_response')
        self.assertFalse(post['api_accepted'])
        self.assertFalse(any(r['event'] == 'physical_confirmation' for r in self.records))

    async def test_expired_approval_never_posts(self):
        answers = iter(['LOW', LOW_APPROVAL])
        def confirm(_):
            answer = next(answers)
            if answer == LOW_APPROVAL:
                self.now += 31
            return answer
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await run_trial(self.host, manual=True, confirm=confirm, emit=self.records.append), 1)
        self.assertNotIn('post', self.sdk.calls)

    async def test_external_change_before_post_is_blocked(self):
        answers = iter(['LOW', LOW_APPROVAL])
        def confirm(_):
            answer = next(answers)
            if answer == LOW_APPROVAL:
                self.sdk.wind = 'LOW'
            return answer
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await run_trial(self.host, manual=True, confirm=confirm, emit=self.records.append), 1)
        self.assertNotIn('post', self.sdk.calls)

    async def test_preflight_failure_does_not_offer_control(self):
        self.sdk.fail = (403, '1308')
        self.assertEqual(await self.run_commands([]), 1)
        self.assertEqual(self.records[0]['status'], 'blocked')
        self.assertNotIn('post', self.sdk.calls)


class ClientSetupTests(unittest.TestCase):
    def test_bad_arguments_never_echo_a_pasted_secret(self):
        secret = 'FAKE-CLI-SECRET-NOT-REAL'
        with patch('sys.argv', ['manual_control.py', '--pat', secret]), contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit):
                main()
        self.assertNotIn(secret, output.getvalue())

    def test_missing_id_in_read_only_loader_never_creates_new_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'private' / 'client-id'
            with patch('thinq_host.uuid.uuid4') as generate:
                with self.assertRaises((OSError, ValueError)):
                    load_or_create_client_id(target, create=False)
            generate.assert_not_called()
            self.assertFalse(target.parent.exists())

    def test_unsafe_terminal_stops_before_loading_id_or_prompting_pat(self):
        with patch('sys.argv', ['manual_control.py', '--manual']), patch('sys.stdin.isatty', return_value=False), patch('manual_control.getpass.getpass') as prompt, patch('manual_control.load_or_create_client_id') as loader:
            with self.assertRaises(SystemExit):
                main()
            loader.assert_not_called()
            prompt.assert_not_called()


if __name__ == '__main__':
    unittest.main()
