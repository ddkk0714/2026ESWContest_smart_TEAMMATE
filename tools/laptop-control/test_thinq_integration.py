# ruff: noqa: E402 -- repository source paths are set before test imports
"""Offline HTML bridge contracts. No PAT, Cloud, LAN or actual appliance."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
from thinq_host import HostSession, Session, FIELDS
from thinq_bridge import ReadSnapshot
from thinq_observer import CommandObserver
from poc.worker import FixtureSdk
from deskmate_hub.ingest.cache import SensorCache, Sample
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message

FIXTURE = json.loads(
    (Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text()
)


class Clock:
    wall = 1000.0
    mono = 0.0

    def advance(self, amount):
        self.wall += amount
        self.mono += amount


def read_reply(clock):
    async def run():
        reply = (
            await Session(FixtureSdk(FIXTURE)).safe(
                {"op": "read", "backend": "fixture"}
            )
        )["public"]
        reply["received_at"] = clock.wall
        return reply

    return asyncio.run(run())


def command(clock, **changes):
    body = {
        "schema_version": "1.0",
        "node": "hub",
        "ts": clock.wall,
        "data": {
            "command_id": "command-one",
            "target_id": "air_purifier",
            "operation": "set_mode",
            "value": "low",
            "gate": "suggest",
            "requires_confirmation": False,
            "expires_ts_ms": (clock.wall + 15) * 1000,
        },
    }
    body["data"].update(changes)
    return body


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        from deskmate_hub.ingest.appliance import ApplianceCache

        self.cache = SensorCache(
            appliances=ApplianceCache(
                wall_clock=lambda: self.clock.wall,
                monotonic_clock=lambda: self.clock.mono,
            )
        )
        self.cache.put(
            "env", Sample(1000, 1000, 1, {"co2_ppm": 700, "co2_valid": True})
        )
        self.snapshot = ReadSnapshot(
            FIELDS,
            mode="fixture",
            wall=lambda: self.clock.wall,
            monotonic=lambda: self.clock.mono,
        )
        self.clock.advance(1)

    def test_read_to_actual_hub_cache_no_esp32_or_fsm_overwrite(self):
        self.snapshot.update(read_reply(self.clock))
        packet = self.snapshot.export()
        before = self.cache.env
        self.assertEqual(
            route_mqtt_message(
                self.cache,
                packet["topic"],
                json.dumps(packet["envelope"]).encode(),
                self.clock.wall,
            ),
            "appliance",
        )
        item = next(iter(self.cache.appliances.snapshot().values()))
        self.assertEqual(item["observations"]["pm2"]["value"], 8)
        self.assertEqual(item["connection"], "unknown")
        self.assertIs(self.cache.env, before)
        self.assertFalse(packet["published"])
        self.assertFalse(packet["hub_fsm_enabled"])
        self.assertIsNone(item["observations"]["pm2"]["unit"])

    def test_repeated_export_never_refreshes_receipt_and_duplicates_are_rejected(self):
        reply = read_reply(self.clock)
        self.snapshot.update(reply)
        first = self.snapshot.export()
        route_mqtt_message(
            self.cache,
            first["topic"],
            json.dumps(first["envelope"]).encode(),
            self.clock.wall,
        )
        self.clock.advance(2)
        self.snapshot.update(reply)
        self.assertEqual(first, self.snapshot.export())
        self.assertIsNone(
            route_mqtt_message(
                self.cache,
                first["topic"],
                json.dumps(first["envelope"]).encode(),
                self.clock.wall,
            )
        )

    def test_partial_missing_preserves_other_fields_and_separate_last_good(self):
        self.snapshot.update(read_reply(self.clock))
        self.clock.advance(1)
        reply = read_reply(self.clock)
        reply["fields"]["pm2"] = {"value": True, "valid": True, "status": "ok"}
        self.snapshot.update(reply)
        fields = self.snapshot.view()["fields"]
        self.assertIsNone(fields["pm2"]["value"])
        self.assertEqual(fields["pm2"]["last_good"]["value"], 8)
        self.assertTrue(fields["pm1"]["valid"])

    def test_failure_expiry_recovery_and_clock_regression_fail_closed(self):
        self.snapshot.update(read_reply(self.clock))
        last_success = self.snapshot.view()["last_successful_received_at"]
        self.snapshot.update({"status": "api_error"})
        view = self.snapshot.view()
        self.assertEqual(view["last_successful_received_at"], last_success)
        self.assertIsNone(view["fields"]["pm2"]["value"])
        self.assertEqual(view["connection"], "unknown")
        self.clock.advance(1)
        self.snapshot.update(read_reply(self.clock))
        self.clock.advance(120)
        self.assertTrue(self.snapshot.view()["receipt_stale"])
        with self.assertRaises(ValueError):
            self.snapshot.export()
        self.clock.advance(1)
        self.snapshot.update(read_reply(self.clock))
        self.clock.wall -= 956
        self.assertTrue(self.snapshot.view()["receipt_stale"])

    def test_restart_requires_post_start_get_and_private_fields_cannot_escape(self):
        self.snapshot.update(read_reply(self.clock))
        packet = self.snapshot.export()
        from deskmate_hub.ingest.appliance import ApplianceCache

        self.clock.advance(1)
        restarted = ApplianceCache(
            wall_clock=lambda: self.clock.wall, monotonic_clock=lambda: self.clock.mono
        )
        self.assertFalse(
            restarted.ingest(packet["topic"], json.dumps(packet["envelope"]).encode())
        )
        self.clock.advance(1)
        reply = read_reply(self.clock)
        reply["device_id"] = "SYNTHETIC-NOT-A-DEVICE"
        reply["access_token"] = "FAKE-SECRET-NOT-REAL"
        self.snapshot.update(reply)
        payload = json.dumps(self.snapshot.export())
        self.assertNotIn("SYNTHETIC-NOT-A-DEVICE", payload)
        self.assertNotIn("FAKE-SECRET-NOT-REAL", payload)

    def test_latest_hub_projection_preserves_fsm_and_registry(self):
        import io
        from deskmate_hub.live import LiveHub
        from deskmate_hub.control.environment_policy import (
            load_environment_control_config,
        )

        baseline = SensorCache()
        baseline.put("env", self.cache.env)

        def hub(cache, output):
            return LiveHub(
                cache,
                publish=output.append,
                profile="",
                control_cfg={"enabled": False},
                environment_control_cfg=dict(
                    load_environment_control_config(), enabled=False
                ),
                personalization_cfg={"enabled": False},
                privacy_cfg={"enabled": False},
                ondevice_cfg={"enabled": False},
                personalize_cfg={"enabled": False},
                learning_cfg={"enabled": False},
                fatigue_cfg={"enabled": False},
                co2_window_cfg={"enabled": False},
                out=io.StringIO(),
            )

        sent, original = [], []
        actual, expected = hub(self.cache, sent), hub(baseline, original)
        registry = repr(actual.environment_policy.registry.devices)
        self.snapshot.update(read_reply(self.clock))
        packet = self.snapshot.export()
        route_mqtt_message(
            self.cache,
            packet["topic"],
            json.dumps(packet["envelope"]).encode(),
            self.clock.wall,
        )
        actual.tick_once(self.clock.wall)
        expected.tick_once(self.clock.wall)
        data = copy.deepcopy(sent[-1]["data"])
        appliances = data["sensor_summary"].pop("appliances")
        self.assertEqual(data, original[-1]["data"])
        self.assertEqual(appliances[0]["observations"]["pm2"]["value"], 8)
        self.assertEqual(repr(actual.environment_policy.registry.devices), registry)
        self.assertIsNone(actual.control)

    def test_missing_reverse_retained_and_expired_observations_keep_esp32(self):
        self.snapshot.update(read_reply(self.clock))
        first = self.snapshot.export()
        first_bytes = json.dumps(first["envelope"]).encode()
        route_mqtt_message(self.cache, first["topic"], first_bytes, self.clock.wall)
        self.clock.advance(1)
        reply = read_reply(self.clock)
        del reply["fields"]["pm2"]
        self.snapshot.update(reply)
        second = self.snapshot.export()
        self.assertEqual(
            route_mqtt_message(
                self.cache,
                second["topic"],
                json.dumps(second["envelope"]).encode(),
                self.clock.wall,
            ),
            "appliance",
        )
        item = next(iter(self.cache.appliances.snapshot().values()))
        self.assertFalse(item["observations"]["pm2"]["valid"])
        self.assertTrue(item["observations"]["pm1"]["valid"])
        self.assertIsNone(
            route_mqtt_message(self.cache, first["topic"], first_bytes, self.clock.wall)
        )
        self.assertIsNone(
            route_mqtt_message(
                self.cache,
                second["topic"],
                json.dumps(second["envelope"]).encode(),
                self.clock.wall,
                retained=True,
            )
        )
        self.clock.advance(120)
        self.assertFalse(
            next(iter(self.cache.appliances.snapshot().values()))["available"]
        )
        self.assertEqual(self.cache.env.data["co2_ppm"], 700)


class ObserverTests(unittest.TestCase):
    def test_valid_command_is_observed_not_authorized_or_executed(self):
        clock = Clock()
        observer = CommandObserver(clock=lambda: clock.wall)
        item = observer.observe("deskmate/control/cmd", command(clock))
        self.assertEqual(item["reason"], "approval_unverified_observe_only")
        self.assertFalse(item["post_attempted"])
        self.assertFalse(item["approval_verified"])
        self.assertFalse(item["result_published"])
        self.assertIsNone(
            observer.observe(
                "deskmate/control/cmd", command(clock, target_id="desk_lamp")
            )
        )

    def test_retained_expired_unsupported_and_automatic_commands_are_blocked(self):
        clock = Clock()
        cases = [
            ({"value": "high"}, False, "unsupported_operation"),
            ({"gate": "auto"}, False, "automatic_or_undo_not_enabled"),
            (
                {"expires_ts_ms": clock.wall * 1000 - 1},
                False,
                "invalid_or_unsynchronized_time",
            ),
            ({}, True, "retained_rejected"),
        ]
        for changes, retained, reason in cases:
            with self.subTest(reason=reason):
                item = CommandObserver(clock=lambda: clock.wall).observe(
                    "deskmate/control/cmd", command(clock, **changes), retained=retained
                )
                self.assertEqual(item["reason"], reason)
                self.assertFalse(item["post_attempted"])

    def test_duplicates_conflicts_capacity_and_clock_reversal(self):
        clock = Clock()
        observer = CommandObserver(clock=lambda: clock.wall, max_commands=1)
        observer.observe("deskmate/control/cmd", command(clock))
        self.assertEqual(
            observer.observe("deskmate/control/cmd", command(clock))["duplicates"], 1
        )
        self.assertEqual(
            observer.observe("deskmate/control/cmd", command(clock, value="auto"))[
                "reason"
            ],
            "conflicting_command_id",
        )
        self.assertIsNone(
            observer.observe("deskmate/control/cmd", command(clock, command_id="other"))
        )
        other = CommandObserver(clock=lambda: clock.wall)
        clock.wall -= 1
        body = command(clock)
        self.assertEqual(
            other.observe("deskmate/control/cmd", body)["reason"], "clock_regressed"
        )

    def test_devview_preserves_monitoring_and_propagates_retained_bit(self):
        spec = importlib.util.spec_from_file_location(
            "thinq_test_devview", ROOT / "hub/atlas-tof/pc/devview/devview.py"
        )
        dv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dv)
        store = dv.Store()
        clock = Clock()
        store.on_mqtt(
            dv.TOPIC_PHASE, json.dumps({"data": {"fsm_state": "FOCUS_PC"}}).encode()
        )
        before = store.snapshot()["phase"]
        store.on_mqtt(dv.TOPIC_CMD, json.dumps(command(clock)).encode(), retained=True)
        self.assertEqual(store.snapshot()["phase"], before)
        item = store.snapshot()["thinq_commands"]["commands"][0]
        self.assertEqual(item["reason"], "retained_rejected")
        self.assertFalse(item["post_attempted"])


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    def make_host(self, lifetime=1200):
        self.now = 10
        self.sdk = FixtureSdk(FIXTURE)
        self.host = HostSession(
            Session(
                self.sdk,
                {"enabled": True, "backend": "fixture"},
                clock=lambda: self.now,
            ),
            clock=lambda: self.now,
            lifetime=lifetime,
        )
        self.key = self.host.claim()["key"]

    async def test_approval_that_expires_during_pre_post_get_never_writes(self):
        self.make_host()
        p = await self.host.call(self.key, {"op": "prepare", "value": "LOW"})
        original = self.sdk.async_get_device_status

        async def delayed(*a, **kw):
            self.now += 31
            return await original(*a, **kw)

        self.sdk.async_get_device_status = delayed
        result = await self.host.call(
            self.key,
            {
                "op": "execute",
                "nonce": p["nonce"],
                "approved": True,
                "physical_online_confirmed": True,
                "no_external_change": True,
            },
        )
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_session_expiry_during_pre_post_get_never_writes(self):
        self.make_host(lifetime=5)
        p = await self.host.call(self.key, {"op": "prepare", "value": "LOW"})
        original = self.sdk.async_get_device_status

        async def delayed(*a, **kw):
            self.now += 6
            return await original(*a, **kw)

        self.sdk.async_get_device_status = delayed
        result = await self.host.call(
            self.key,
            {
                "op": "execute",
                "nonce": p["nonce"],
                "approved": True,
                "physical_online_confirmed": True,
                "no_external_change": True,
            },
        )
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_unexpected_post_error_locks_session_and_redacts_error(self):
        self.make_host()
        p = await self.host.call(self.key, {"op": "prepare", "value": "LOW"})

        async def fail(*a, **kw):
            self.sdk.calls.append("post")
            raise RuntimeError("FAKE-SECRET-IN-EXCEPTION")

        self.sdk.async_post_device_control = fail
        result = await self.host.call(
            self.key,
            {
                "op": "execute",
                "nonce": p["nonce"],
                "approved": True,
                "physical_online_confirmed": True,
                "no_external_change": True,
            },
        )
        self.assertFalse(result["post_enabled"])
        self.assertNotIn("FAKE-SECRET-IN-EXCEPTION", json.dumps(result))
        await self.host.call(self.key, {"op": "prepare", "value": "LOW"})
        self.assertEqual(self.sdk.calls.count("post"), 1)

    async def test_unsupported_wind_profile_does_not_discard_valid_pm(self):
        self.make_host()
        del self.sdk.fixture["profile"]["property"]["airFlow"]
        result = await self.host.call(self.key, {"op": "read"})
        self.assertTrue(result["fields"]["pm2"]["valid"])
        self.assertFalse(result["fields"]["wind_strength"]["valid"])
        self.assertIsNone(result["snapshot"]["fields"]["wind_strength"]["value"])
        blocked = await self.host.call(self.key, {"op": "prepare", "value": "LOW"})
        self.assertEqual(blocked["status"], "blocked")
        self.assertNotIn("post", self.sdk.calls)

    async def test_cloud_401_is_distinct_from_device_offline_and_has_no_current_values(
        self,
    ):
        self.make_host()
        await self.host.call(self.key, {"op": "read"})
        self.sdk.fail = (401, None)
        result = await self.host.call(self.key, {"op": "read"})
        self.assertEqual(result["status"], "api_error")
        self.assertEqual(result["http_status"], 401)
        self.assertEqual(result["snapshot"]["connection"], "unknown")
        self.assertIsNone(result["snapshot"]["fields"]["pm2"]["value"])
        self.assertEqual(result["snapshot"]["fields"]["pm2"]["last_good"]["value"], 8)
        self.assertNotIn(self.sdk.device_id, json.dumps(result))

    async def test_fixture_cache_reads_do_not_call_sdk_and_expired_session_cannot_export(
        self,
    ):
        self.make_host()
        result = await self.host.call(self.key, {"op": "read"})
        count = len(self.sdk.calls)
        await self.host.call(self.key, {"op": "snapshot"})
        packet = await self.host.call(self.key, {"op": "observation"})
        self.assertEqual(packet["envelope"]["data"]["mode"], "fixture")
        self.assertEqual(len(self.sdk.calls), count)
        self.now += 1201
        with self.assertRaises(PermissionError):
            await self.host.call(self.key, {"op": "observation"})
        self.assertFalse(result["automatic_control"])
