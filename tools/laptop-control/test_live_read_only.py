"""Offline live-read-only boundaries. No Cloud, PAT or LAN requests."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid
import unittest
from unittest.mock import patch

from thinq_host import HostSession, Session, load_or_create_client_id, main, make_app
from poc.worker import FixtureSdk
from aiohttp.test_utils import TestClient, TestServer

FIXTURE = json.loads(
    (Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text()
)


class LiveReadOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_even_enabled_context_cannot_control_or_export(self):
        sdk = FixtureSdk(FIXTURE)
        host = HostSession(Session(sdk, {"enabled": True, "backend": "live_read_only"}))
        claim = host.claim()
        self.assertEqual(claim["mode"], "live")
        self.assertEqual(claim["access"], "read_only")
        self.assertEqual(sdk.calls, [])
        read = await host.call(claim["key"], {"op": "read"})
        self.assertEqual(read["snapshot"]["mode"], "live")
        self.assertFalse(read["post_enabled"])
        count = len(sdk.calls)
        await host.call(claim["key"], {"op": "snapshot"})
        self.assertEqual(len(sdk.calls), count)
        for op in ["prepare", "execute", "physical_confirmation", "observation"]:
            with self.assertRaises(ValueError):
                await host.call(claim["key"], {"op": op, "value": "LOW"})
        self.assertEqual(len(sdk.calls), count)
        self.assertNotIn("post", sdk.calls)
        await host.call(claim["key"], {"op": "revoke"})
        with self.assertRaises(PermissionError):
            await host.call(claim["key"], {"op": "read"})

    async def test_http_port_binding_and_read_only_claim(self):
        sdk = FixtureSdk(FIXTURE)
        host = HostSession(
            Session(sdk, {"enabled": False, "backend": "live_read_only"})
        )
        client = TestClient(TestServer(make_app(host, port=18892)))
        await client.start_server()
        try:
            bad = await client.get(
                "/bootstrap",
                headers={"Host": "127.0.0.1:18792", "X-Deskmate-Bootstrap": "1"},
            )
            self.assertEqual(bad.status, 403)
            headers = {"Host": "127.0.0.1:18892", "X-Deskmate-Bootstrap": "1"}
            claim = await (await client.get("/bootstrap", headers=headers)).json()
            self.assertEqual(claim["access"], "read_only")
            headers["X-Deskmate-Session"] = claim["key"]
            result = await client.post("/call", headers=headers, json={"op": "execute"})
            self.assertEqual(result.status, 400)
            self.assertEqual(sdk.calls, [])
        finally:
            await client.close()


class LiveCliTests(unittest.TestCase):
    def test_reuses_fixed_client_id_and_never_arms_post(self):
        async def no_network(*args, **kwargs):
            self.assertEqual(kwargs["client_id"], "SYNTHETIC-STABLE-CLIENT")
            self.assertFalse(kwargs["allow_control"])
            self.assertTrue(kwargs["loopback"])
            self.assertEqual(kwargs["port"], 18892)

        with (
            patch("thinq_host.load_or_create_client_id", return_value="SYNTHETIC-STABLE-CLIENT") as load_id,
            patch("sys.argv", ["thinq_host.py", "--live-read-only", "--port", "18892"]),
            patch("sys.stdin.isatty", return_value=True),
            patch("importlib.metadata.version", return_value="1.0.14"),
            patch(
                "thinq_host.getpass.getpass", return_value="FAKE-TOKEN-NOT-REAL"
            ) as prompt,
            patch("thinq_host.serve", side_effect=no_network) as serve,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            main()
            main()
            self.assertEqual(serve.call_count, 2)
            self.assertEqual(load_id.call_count, 2)
            self.assertEqual(prompt.call_count, 2)
            self.assertNotIn("FAKE-TOKEN", output.getvalue())

    def test_conflicting_mode_fails_before_credentials(self):
        for flags in [("--fixture",), ("--allow-control",)]:
            with (
                patch("sys.argv", ["thinq_host.py", "--live-read-only", *flags]),
                patch("thinq_host.getpass.getpass") as prompt,
            ):
                with self.assertRaises(SystemExit):
                    main()
                prompt.assert_not_called()


class ClientIdPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "client-id"

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_one_private_uuid4_and_reuses_it(self):
        expected = uuid.UUID("00000000-0000-4000-8000-000000000001")
        with patch("thinq_host.uuid.uuid4", return_value=expected) as generate:
            first = load_or_create_client_id(self.path)
            second = load_or_create_client_id(self.path)
        self.assertEqual(first, str(expected))
        self.assertEqual(second, first)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_invalid_existing_file_fails_without_replacing_or_regenerating(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text("not-a-uuid\n", encoding="ascii")
        self.path.chmod(0o600)
        before = self.path.read_bytes()
        with patch("thinq_host.uuid.uuid4") as generate:
            with self.assertRaises(ValueError):
                load_or_create_client_id(self.path)
        generate.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_unsafe_directory_or_file_mode_fails_closed(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.parent.chmod(0o755)
        with patch("thinq_host.uuid.uuid4") as generate:
            with self.assertRaises(ValueError):
                load_or_create_client_id(self.path)
        generate.assert_not_called()
        self.path.parent.chmod(0o700)
        self.path.write_text("00000000-0000-4000-8000-000000000002\n", encoding="ascii")
        self.path.chmod(0o644)
        with patch("thinq_host.uuid.uuid4") as generate:
            with self.assertRaises(ValueError):
                load_or_create_client_id(self.path)
        generate.assert_not_called()
