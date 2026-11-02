"""All devices, HTTP responses, tokens and actions here are synthetic/offline."""

import asyncio
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from thinqconnect.thinq_api import ThinQApi

from tools.thinq_connect import manual_trial as m
from tools.thinq_connect import probe


def prop(values):
    return {"type": "enum", "mode": ["r", "w"], "value": {"r": values, "w": values}}


PROFILE = {
    "property": {
        "airFlow": {
            "windStrength": prop(["LOW", "MID", "HIGH", "POWER", "AUTO"]),
            "windStrengthDetail": prop(["OFF", "LOW", "MID", "HIGH", "POWER", "AUTO"]),
        }
    }
}
STATE = {
    "operation": {"airPurifierOperationMode": "POWER_ON"},
    "airPurifierJobMode": {"currentJobMode": "CLEAN"},
    "airFlow": {"windStrength": "AUTO", "windStrengthDetail": "AUTO"},
    "airQualitySensor": {"PM2": 12},
    "filterInfo": {"filterRemainPercent": 96},
}
TARGET = "SYNTHETIC-PURIFIER-ID"
DEVICES = [
    {
        "deviceId": TARGET,
        "deviceInfo": {
            "deviceType": "DEVICE_AIR_PURIFIER",
            "modelName": "AIR_910604_WW",
            "reportable": True,
            "alias": "PRIVATE-ALIAS",
            "accountId": "PRIVATE-ACCOUNT",
        },
    }
]


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.ok = payload, status, 200 <= status < 400

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.payload

    def release(self):
        pass


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class Session:
    """No sockets or actual HTTP; used behind the real SDK's request boundary."""

    def __init__(self):
        self.devices, self.profile, self.state = copy.deepcopy(
            (DEVICES, PROFILE, STATE)
        )
        self.calls, self.posts = [], []
        self.fault = None
        self.offline = False
        self.apply = True
        self.get_count = 0
        self.after_state = lambda: None

    async def request(self, method, url, **kwargs):
        path = str(url).split(".com", 1)[1]
        self.calls.append((method, path))
        assert kwargs.get("allow_redirects") is False
        if method == "POST":
            payload = kwargs["json"]
            self.posts.append(copy.deepcopy(payload))
            if self.apply:
                self.state["airFlow"]["windStrength"] = payload["airFlow"][
                    "windStrength"
                ]
                self.state["airFlow"]["windStrengthDetail"] = payload["airFlow"][
                    "windStrength"
                ]
            if self.fault == "timeout":
                raise asyncio.TimeoutError("SECRET timeout text must not escape")
            if self.fault == "network":
                raise OSError("SECRET network text must not escape")
            if self.fault == "api":
                return Response(
                    {"error": {"code": "1308", "message": "SECRET auth"}}, 403
                )
            return Response({"response": None}, 202)
        if path == "/devices":
            return Response({"response": self.devices})
        if path.endswith("/profile"):
            return Response({"response": self.profile})
        if path.endswith("/state"):
            if self.offline:
                return Response(
                    {"error": {"code": "1222", "message": "SECRET offline"}}, 400
                )
            self.get_count += 1
            result = copy.deepcopy(self.state)
            self.after_state()
            return Response({"response": result})
        raise AssertionError("Unexpected path")


def setup(execute=False, answers=()):
    session = Session()
    transport = m.Transport(session, execute=execute)
    sdk = ThinQApi(
        session=transport,
        access_token="SYNTHETIC-TOKEN",
        country_code="KR",
        client_id="SYNTHETIC-CLIENT",
    )
    device_io = m.DeviceIO(sdk, transport, probe)
    answer_iter = iter(answers)
    logs = []
    clock = Clock()
    trial = m.Trial(
        device_io,
        execute=execute,
        confirm=lambda prompt: next(answer_iter),
        emit=logs.append,
        sleep=clock.sleep,
        clock=clock,
    )
    return session, transport, trial, logs


class OfflineGuard:
    def setUp(self):
        super().setUp()
        self.enterContext(
            patch(
                "socket.socket.connect",
                side_effect=AssertionError("Offline test attempted network access"),
            )
        )
        self.enterContext(
            patch(
                "aiohttp.ClientSession._request",
                side_effect=AssertionError("Offline test attempted real HTTP"),
            )
        )
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))


class Tests(OfflineGuard, unittest.IsolatedAsyncioTestCase):
    async def test_preflight_real_sdk_no_write_and_private_output(self):
        session, transport, trial, logs = setup()
        await trial.run()
        self.assertFalse(session.posts)
        self.assertFalse(transport.attempted)
        output = json.dumps(logs)
        for secret in (
            TARGET,
            "PRIVATE-ALIAS",
            "PRIVATE-ACCOUNT",
            "SYNTHETIC-TOKEN",
            "SYNTHETIC-CLIENT",
        ):
            self.assertNotIn(secret, output)
        self.assertEqual(logs[-1]["post_count"], 0)

    async def test_other_appliance_ignored(self):
        session, _, trial, _ = setup()
        session.devices.append(
            {
                "deviceId": "OTHER-ID",
                "deviceInfo": {
                    "deviceType": "DEVICE_AIR_CONDITIONER",
                    "modelName": "OTHER",
                },
            }
        )
        await trial.run()
        self.assertEqual(
            [p for method, p in session.calls if p.endswith("/state")],
            [f"/devices/{TARGET}/state"],
        )

    async def test_zero_and_multiple_targets(self):
        for devices in ([], DEVICES * 2):
            session, _, trial, _ = setup(True)
            session.devices = devices
            with self.assertRaises(m.StopTrial):
                await trial.run()
            self.assertEqual(len(session.calls), 1)
            self.assertFalse(session.posts)

    async def test_wrong_or_missing_profile_values(self):
        for bad in (
            "readonly",
            "missing_LOW",
            "missing_AUTO",
            "missing_property",
            "detail_only",
        ):
            session, _, trial, _ = setup(True)
            target = session.profile["property"]["airFlow"]["windStrength"]
            if bad == "readonly":
                target["mode"] = ["r"]
            elif bad == "missing_LOW":
                target["value"]["w"].remove("LOW")
            elif bad == "missing_AUTO":
                target["value"]["w"].remove("AUTO")
            else:
                del session.profile["property"]["airFlow"]["windStrength"]
            with self.assertRaises(m.StopTrial):
                await trial.run()
            self.assertFalse(session.posts)

    async def test_baseline_not_on_clean_auto(self):
        for group, field, value in [
            ("operation", "airPurifierOperationMode", "POWER_OFF"),
            ("airPurifierJobMode", "currentJobMode", "SLEEP"),
            ("airFlow", "windStrength", "LOW"),
            ("airFlow", "windStrength", "HIGH"),
        ]:
            session, _, trial, _ = setup(True)
            session.state[group][field] = value
            with self.assertRaises(m.StopTrial):
                await trial.run()
            self.assertFalse(session.posts)

    async def test_offline_get_stops_without_post(self):
        session, _, trial, _ = setup(True)
        session.offline = True
        with self.assertRaises(m.ApiFailure) as caught:
            await trial.run()
        self.assertEqual(caught.exception.code, "1222")
        self.assertFalse(session.posts)

    async def test_denied_or_missing_explicit_approval(self):
        for answer in ("", "yes", "LOW", "APPROVE"):
            session, _, trial, _ = setup(True, [answer])
            with self.assertRaises(m.StopTrial):
                await trial.run()
            self.assertFalse(session.posts)

    async def test_configuration_change_during_low_approval(self):
        session, _, trial, _ = setup(True)

        def approve(prompt):
            session.state["airFlow"]["windStrength"] = "HIGH"
            return m.LOW_APPROVAL

        trial.confirm = approve
        with self.assertRaises(m.StopTrial):
            await trial.run()
        self.assertFalse(session.posts)

    async def test_profile_changed_during_low_approval(self):
        session, _, trial, _ = setup(True)

        def approve(prompt):
            session.profile["property"]["airFlow"]["windStrength"]["mode"] = ["r"]
            return m.LOW_APPROVAL

        trial.confirm = approve
        with self.assertRaises(m.StopTrial):
            await trial.run()
        self.assertFalse(session.posts)

    async def test_manual_roundtrip_exact_standard_payload_and_physical_confirmation(
        self,
    ):
        session, transport, trial, logs = setup(
            True, [m.LOW_APPROVAL, m.PHYSICAL_LOW, m.AUTO_APPROVAL, m.PHYSICAL_AUTO]
        )
        await trial.run()
        self.assertEqual(
            session.posts,
            [
                {"airFlow": {"windStrength": "LOW"}},
                {"airFlow": {"windStrength": "AUTO"}},
            ],
        )
        self.assertEqual(transport.attempted, {"LOW", "AUTO"})
        self.assertTrue(logs[-1]["full_success"])
        self.assertEqual(session.state["airFlow"]["windStrength"], "AUTO")

    async def test_state_success_does_not_imply_physical_success(self):
        session, _, trial, logs = setup(
            True, [m.LOW_APPROVAL, "UNKNOWN", m.AUTO_APPROVAL, "UNKNOWN"]
        )
        await trial.run()
        self.assertEqual(len(session.posts), 2)
        self.assertTrue(logs[-1]["state_restored_AUTO"])
        self.assertFalse(logs[-1]["full_success"])

    async def test_timeout_network_api_failure_never_resends_or_restores(self):
        for fault in ("timeout", "network", "api"):
            session, _, trial, logs = setup(True, [m.LOW_APPROVAL])
            session.fault = fault
            with self.assertRaises(m.StopTrial):
                await trial.run()
            self.assertEqual(session.posts, [{"airFlow": {"windStrength": "LOW"}}])
            self.assertEqual(session.state["airFlow"]["windStrength"], "LOW")
            self.assertNotIn("SECRET", json.dumps(logs))
            self.assertTrue(
                any(log["event"] == "POST_outcome_unconfirmed" for log in logs)
            )

    async def test_api_acceptance_without_matching_state_times_out(self):
        session, _, trial, logs = setup(True, [m.LOW_APPROVAL])
        session.apply = False
        with self.assertRaises(m.StopTrial):
            await trial.run()
        self.assertEqual(len(session.posts), 1)
        self.assertTrue(any(log["event"] == "API_request_accepted" for log in logs))
        self.assertFalse(any(log["event"] == "trial_complete" for log in logs))

    async def test_restore_denied_or_interrupted_has_no_cleanup_write(self):
        for last in ("no", "interrupt"):
            session, _, trial, _ = setup(True, [m.LOW_APPROVAL, m.PHYSICAL_LOW, "no"])
            if last == "interrupt":

                def confirm(prompt):
                    if m.LOW_APPROVAL in prompt:
                        return m.LOW_APPROVAL
                    raise EOFError()

                trial.confirm = confirm
            with self.assertRaises((m.StopTrial, EOFError)):
                await trial.run()
            self.assertEqual(session.posts, [{"airFlow": {"windStrength": "LOW"}}])

    async def test_external_change_or_offline_prevents_restore(self):
        for value in ("HIGH", "POWER_OFF", "offline", "timer"):
            session, _, trial, _ = setup(True)

            def confirm(prompt):
                if m.LOW_APPROVAL in prompt:
                    return m.LOW_APPROVAL
                if m.PHYSICAL_LOW in prompt:
                    return m.PHYSICAL_LOW
                if value == "POWER_OFF":
                    session.state["operation"]["airPurifierOperationMode"] = "POWER_OFF"
                elif value == "offline":
                    session.offline = True
                elif value == "timer":
                    session.state["timer"] = {"example": "changed"}
                else:
                    session.state["airFlow"]["windStrength"] = value
                return m.AUTO_APPROVAL

            trial.confirm = confirm
            with self.assertRaises((m.StopTrial, m.ApiFailure)):
                await trial.run()
            self.assertEqual(len(session.posts), 1)

    async def test_restore_response_loss_has_no_third_post(self):
        session, _, trial, logs = setup(True)

        def confirm(prompt):
            if m.LOW_APPROVAL in prompt:
                return m.LOW_APPROVAL
            if m.PHYSICAL_LOW in prompt:
                return m.PHYSICAL_LOW
            session.fault = "timeout"
            return m.AUTO_APPROVAL

        trial.confirm = confirm
        with self.assertRaises(m.StopTrial):
            await trial.run()
        self.assertEqual(len(session.posts), 2)
        self.assertFalse(any(log["event"] == "trial_complete" for log in logs))

    async def test_transport_default_write_blocked_even_with_injected_permit(self):
        session = Session()
        transport = m.Transport(session)
        transport.select(TARGET)
        transport.permit = "LOW"
        with self.assertRaises(m.StopTrial):
            await transport.request(
                "POST",
                f"https://api-kic.lgthinq.com/devices/{TARGET}/control",
                json={"airFlow": {"windStrength": "LOW"}},
            )
        self.assertFalse(session.calls)

    async def test_transport_target_payload_and_one_shot_guard(self):
        for path, payload in [
            (f"/devices/{TARGET}/control", {"airFlow": {"windStrengthDetail": "LOW"}}),
            ("/devices/OTHER/control", {"airFlow": {"windStrength": "LOW"}}),
            (
                f"/devices/{TARGET}/control",
                {"operation": {"airPurifierOperationMode": "POWER_OFF"}},
            ),
        ]:
            session = Session()
            transport = m.Transport(session, True)
            transport.select(TARGET)
            transport.approve("LOW")
            with self.assertRaises(m.StopTrial):
                await transport.request(
                    "POST", "https://api-kic.lgthinq.com" + path, json=payload
                )
            self.assertFalse(session.calls)
        session = Session()
        transport = m.Transport(session, True)
        transport.select(TARGET)
        with self.assertRaises(m.StopTrial):
            transport.approve("AUTO")
        transport.approve("LOW")
        await transport.request(
            "POST",
            f"https://api-kic.lgthinq.com/devices/{TARGET}/control",
            json={"airFlow": {"windStrength": "LOW"}},
        )
        with self.assertRaises(m.StopTrial):
            transport.approve("LOW")
        self.assertEqual(len(session.posts), 1)

    async def test_transport_rejects_redirects_foreign_hosts_and_other_methods(self):
        for method, url in [
            ("GET", "http://api-kic.lgthinq.com/devices"),
            ("GET", "https://foreign.invalid/devices"),
            ("DELETE", "https://api-kic.lgthinq.com/devices"),
            ("GET", "https://api-kic.lgthinq.com/route"),
        ]:
            session = Session()
            transport = m.Transport(session)
            with self.assertRaises(m.StopTrial):
                await transport.request(method, url)
            self.assertFalse(session.calls)

        class Redirect(Session):
            async def request(self, *args, **kwargs):
                return Response({}, 302)

        transport = m.Transport(Redirect())
        with self.assertRaises(m.ApiFailure):
            await transport.request("GET", "https://api-kic.lgthinq.com/devices")


class CliTests(OfflineGuard, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(m.logging, "disable"))
        self.enterContext(warnings.catch_warnings())
        self.hidden_prompt = self.enterContext(
            patch.object(m.getpass, "getpass", return_value="SYNTHETIC-PROMPT-TOKEN")
        )
        self.enterContext(patch.object(m.sys.stdin, "isatty", return_value=True))
        self.enterContext(
            patch.dict(os.environ, {"THINQ_CLIENT_ID": "SYNTHETIC-CLIENT"}, clear=True)
        )

    def test_check_mode_needs_no_credentials_and_no_requests(self):
        with (
            patch.object(sys, "argv", ["manual_trial", "--check"]),
            patch.dict(os.environ, {}, clear=True),
        ):
            m.main()
        self.hidden_prompt.assert_not_called()

    def test_default_cli_is_preflight_not_execute(self):
        modes = []

        async def no_requests(trial):
            modes.append(trial.execute)

        with (
            patch.object(sys, "argv", ["manual_trial"]),
            patch.object(m.Trial, "run", no_requests),
        ):
            m.main()
        self.assertEqual(modes, [False])

    def test_cli_rejects_conflicting_modes_before_pat(self):
        with patch.object(sys, "argv", ["manual_trial", "--preflight", "--execute"]):
            with self.assertRaises(SystemExit) as caught:
                with contextlib.redirect_stderr(io.StringIO()):
                    m.main()
        self.assertEqual(caught.exception.code, 2)
        self.hidden_prompt.assert_not_called()

    def test_cli_unknown_secret_argument_is_not_echoed(self):
        error = io.StringIO()
        with patch.object(
            sys, "argv", ["manual_trial", "--token", "SYNTHETIC-ARGUMENT-SECRET"]
        ):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(error):
                m.main()
        self.assertNotIn("SYNTHETIC-ARGUMENT-SECRET", error.getvalue())
        self.hidden_prompt.assert_not_called()

    def test_cli_requires_tty_before_pat(self):
        with (
            patch.object(sys, "argv", ["manual_trial", "--preflight"]),
            patch.object(m.sys.stdin, "isatty", return_value=False),
        ):
            with self.assertRaises(m.StopTrial):
                m.main()
        self.hidden_prompt.assert_not_called()

    def test_cli_requires_client_id_before_pat(self):
        with (
            patch.object(sys, "argv", ["manual_trial", "--preflight"]),
            patch.dict(os.environ, {}, clear=True),
        ):
            with self.assertRaises(m.StopTrial):
                m.main()
        self.hidden_prompt.assert_not_called()

    def test_cli_ignores_environment_pat(self):
        captured = []
        real_sdk = ThinQApi

        def capture_sdk(**kwargs):
            captured.append(kwargs["access_token"])
            return real_sdk(**kwargs)

        async def no_requests(trial):
            pass

        with (
            patch.object(sys, "argv", ["manual_trial", "--preflight"]),
            patch.dict(os.environ, {"THINQ_PAT": "SYNTHETIC-ENV-SECRET"}),
            patch("thinqconnect.thinq_api.ThinQApi", capture_sdk),
            patch.object(m.Trial, "run", no_requests),
        ):
            m.main()
        self.hidden_prompt.assert_called_once()
        self.assertEqual(captured, ["SYNTHETIC-PROMPT-TOKEN"])

    def test_module_check_works_outside_worktree(self):
        repository = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(repository), PYTHONDONTWRITEBYTECODE="1")
        with tempfile.TemporaryDirectory() as outside:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tools.thinq_connect.manual_trial",
                    "--check",
                ],
                cwd=outside,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("repository probe.py loaded", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
