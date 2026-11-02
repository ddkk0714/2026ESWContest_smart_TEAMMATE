"""Synthetic Profile/State shapes only; no captured account/device responses.

The original range/level fixtures remain synthetic. A separate minimal fixture
reconstructs the user's GET observations, without identifiers or a raw response.
Unreported bounds, units and measurement periods are not inferred. I/O is blocked.
"""

import asyncio
import copy
import json
import logging
import unittest
from pathlib import Path
from unittest.mock import patch

from thinqconnect.thinq_api import ThinQApi

from tools.thinq_connect.air_quality import (
    FIELDS,
    AirQualityCollector,
    FieldSpec,
    _validate,
)
from tools.thinq_connect.manual_trial import Transport
from tools.thinq_connect.probe import FileMockApi

TARGET = "SYNTHETIC-PURIFIER-ID"
DEVICE = {
    "deviceId": TARGET,
    "deviceInfo": {
        "deviceType": "DEVICE_AIR_PURIFIER",
        "modelName": "AIR_910604_WW",
        "reportable": True,
        "controllable": None,
        "alias": "PRIVATE-ALIAS",
        "accountId": "PRIVATE-ACCOUNT",
    },
}


def fixture():
    profile, state = {"property": {}}, {}
    for name, spec in FIELDS.items():
        if spec.kind == "power":
            value, kind, allowed = "POWER_ON", "enum", ["POWER_ON", "POWER_OFF"]
        elif spec.kind == "mode":
            value, kind, allowed = "ON_WORKING", "enum", ["ON_WORKING", "ALWAYS"]
        elif spec.kind == "level":
            value, kind, allowed = 1, "enum", [1, 2, 3]
        else:
            value, kind, allowed = 12, "range", {"min": 0, "max": 100}
            if name == "filter_remain_percent":
                value = 96
        prop = {"mode": ["r"], "type": kind, "value": {"r": allowed}}
        profile["property"].setdefault(spec.resource, {})[spec.property] = prop
        state.setdefault(spec.resource, {})[spec.property] = value
    state["privateAccount"] = "PRIVATE-ACCOUNT"
    return {
        "devices": [copy.deepcopy(DEVICE)],
        "profiles": {TARGET: profile},
        "states": {TARGET: state},
    }


class Clock:
    wall = 1000.0
    mono = 0.0

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class Failure(Exception):
    def __init__(self, code=None):
        super().__init__("PRIVATE-ACCOUNT SYNTHETIC-TOKEN " + TARGET)
        self.code = code
        self.headers = {"Authorization": "SYNTHETIC-TOKEN"}


class Api(FileMockApi):
    def __init__(self):
        self.data = fixture()
        super().__init__(self.data)
        self.error = None
        self.posts = 0
        self.calls = []
        self.delay = 0

    async def async_get_device_list(self):
        self.calls.append("devices")
        if self.error:
            raise self.error
        return await super().async_get_device_list()

    async def async_get_device_profile(self, device_id):
        self.calls.append("profile")
        if self.error:
            raise self.error
        return await super().async_get_device_profile(device_id)

    async def async_get_device_status(self, device_id):
        self.calls.append("state")
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return await super().async_get_device_status(device_id)

    async def async_post_device_control(self, *args, **kwargs):
        self.posts += 1
        raise AssertionError("No writes allowed")


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(
            patch("socket.socket.connect", side_effect=AssertionError("No real I/O"))
        )
        self.enterContext(
            patch(
                "aiohttp.ClientSession._request",
                side_effect=AssertionError("No real HTTP"),
            )
        )
        self.api = Api()
        self.clock = Clock()
        self.reader = AirQualityCollector(
            self.api,
            max_receipt_age_sec=30,
            timeout_sec=0.05,
            wall_clock=lambda: self.clock.wall,
            monotonic_clock=lambda: self.clock.mono,
        )

    async def test_normal_all_fields_and_independent_groups(self):
        s = await self.reader.poll()
        self.assertTrue(all(f.valid for f in s.fields.values()))
        self.assertEqual(self.api.calls, ["devices", "profile", "state"])
        self.assertEqual(s.fields["filter_remain_percent"].value, 96)
        d = s.to_dict()
        self.assertEqual(
            d["measurements"]["pm2"]["source_property"], "airQualitySensor.PM2"
        )
        self.assertEqual(d["device_state"]["power"]["value"], "POWER_ON")
        self.assertEqual(d["device_state"]["monitoring_mode"]["value"], "ON_WORKING")
        self.assertIn("filter_remain_percent", d["maintenance"])
        self.assertEqual(s.api_status, "ok")
        self.assertEqual(s.connection, "unknown")
        self.assertEqual(self.api.posts, 0)

    async def test_no_measurement_time_units_or_online_invented(self):
        s = await self.reader.poll()
        for reading in s.fields.values():
            self.assertIsNone(reading.measured_at)
            self.assertIsNone(reading.unit)
            self.assertEqual(reading.measurement_freshness, "unknown")
        self.assertEqual(s.received_at, 1000)
        self.assertEqual(s.to_dict()["freshness_basis"], "api_receipt_only")

    async def test_each_missing_field_does_not_invalidate_other_fields(self):
        for name, spec in FIELDS.items():
            for missing in (False, True):
                self.api.data = fixture()
                self.api._payload = self.api.data
                state = self.api.data["states"][TARGET][spec.resource]
                if missing:
                    del state[spec.property]
                else:
                    state[spec.property] = None
                with self.subTest(name=name, missing=missing):
                    s = await self.reader.poll()
                    self.assertEqual(s.fields[name].status, "missing")
                    self.assertIsNone(s.fields[name].value)
                    self.assertTrue(
                        all(f.valid for n, f in s.fields.items() if n != name)
                    )

    async def test_invalid_numbers_strings_bools_nan_inf_and_large_int(self):
        for value in (True, "12", {}, [], float("nan"), float("inf"), 10**1000):
            self.api.data["states"][TARGET]["airQualitySensor"]["PM2"] = value
            s = await self.reader.poll()
            self.assertEqual(s.fields["pm2"].status, "invalid_value")
            self.assertTrue(s.fields["pm1"].valid)
            json.dumps(s.to_dict(), allow_nan=False)

    async def test_bounds_from_profile_and_percent_semantics(self):
        state = self.api.data["states"][TARGET]
        state["airQualitySensor"]["PM1"] = -1
        state["airQualitySensor"]["PM2"] = 101
        state["filterInfo"]["filterRemainPercent"] = 101
        s = await self.reader.poll()
        for name in ("pm1", "pm2", "filter_remain_percent"):
            self.assertEqual(s.fields[name].status, "out_of_range")
        self.assertTrue(s.fields["pm10"].valid)

    async def test_zero_int_double_are_not_missing_and_false_is_not_a_mode(self):
        state = self.api.data["states"][TARGET]["airQualitySensor"]
        state["PM1"], state["PM2"] = 0, 12.5
        s = await self.reader.poll()
        self.assertEqual(s.fields["pm1"].value, 0)
        self.assertEqual(s.fields["pm2"].value, 12.5)
        self.assertTrue(s.fields["monitoring_mode"].valid)
        state["monitoringEnabled"] = False
        s = await self.reader.poll()
        self.assertEqual(s.fields["monitoring_mode"].status, "invalid_value")
        self.assertTrue(s.fields["pm1"].valid)

    async def test_profile_readability_unknown_shape_and_enum_membership(self):
        profile = self.api.data["profiles"][TARGET]["property"]["airQualitySensor"]
        profile["PM1"]["mode"] = ["w"]
        profile["PM2"]["type"] = "unverified-type"
        del profile["PM10"]
        profile["PM1Level"]["value"]["r"] = [2, 3]
        profile["PM2Level"]["value"]["r"] = ["SYNTHETIC_LEVEL"]
        self.api.data["states"][TARGET]["airQualitySensor"]["PM2Level"] = (
            "SYNTHETIC_LEVEL"
        )
        s = await self.reader.poll()
        for name, status in (
            ("pm1", "not_readable"),
            ("pm2", "unsupported_profile"),
            ("pm10", "unsupported"),
            ("pm1_level", "out_of_range"),
        ):
            self.assertEqual(s.fields[name].status, status)
        self.assertTrue(s.fields["pm2_level"].valid)
        self.assertTrue(s.fields["odor"].valid)

    async def test_partial_invalid_profile_and_nested_state(self):
        self.api.data["profiles"][TARGET]["property"]["airQualitySensor"]["PM1"] = []
        self.api.data["states"][TARGET]["filterInfo"] = []
        s = await self.reader.poll()
        self.assertEqual(s.fields["pm1"].status, "unsupported_profile")
        self.assertEqual(s.fields["filter_remain_percent"].status, "missing")
        self.assertTrue(s.fields["pm2"].valid)

    async def test_last_good_is_not_used_as_current_on_partial_loss(self):
        first = await self.reader.poll()
        self.clock.advance(5)
        del self.api.data["states"][TARGET]["airQualitySensor"]["PM2"]
        s = await self.reader.poll()
        r = s.fields["pm2"]
        self.assertFalse(r.valid)
        self.assertIsNone(r.value)
        self.assertEqual(r.last_good.value, first.fields["pm2"].value)
        self.assertEqual(r.last_good.api_age_sec, 5)
        self.assertEqual(s.fields["pm1"].last_good.api_age_sec, 0)

    async def test_stale_without_io_and_wall_clock_rollback(self):
        await self.reader.poll()
        self.clock.advance(29.999)
        self.clock.wall -= 500
        self.assertTrue(self.reader.snapshot().fields["pm2"].valid)
        self.clock.advance(0.001)
        s = self.reader.snapshot()
        self.assertEqual(s.fields["pm2"].status, "stale")
        self.assertIsNone(s.fields["pm2"].value)
        self.assertEqual(s.fields["pm2"].last_good.value, 12)
        self.assertEqual(len(self.api.calls), 3)

    async def test_equal_response_updates_receipt_not_measurement_time(self):
        await self.reader.poll()
        self.clock.advance(31)
        s = await self.reader.poll()
        self.assertTrue(s.fields["pm2"].valid)
        self.assertEqual(s.fields["pm2"].received_at, 1031)
        self.assertIsNone(s.fields["pm2"].measured_at)
        self.assertEqual(s.fields["pm2"].measurement_freshness, "unknown")

    async def test_monotonic_clock_reverse_invalidates_current(self):
        await self.reader.poll()
        self.clock.mono -= 1
        self.assertEqual(self.reader.snapshot().fields["pm2"].status, "clock_error")

    async def test_failures_are_safe_and_recover_without_losing_other_cache(self):
        for error, expected in (
            (Failure("1222"), "offline"),
            (Failure("1308"), "authentication_or_authority"),
            (Failure("1306"), "rate_limited"),
            (Failure(), "api_error"),
            (OSError("SYNTHETIC-TOKEN"), "network_error"),
            (asyncio.TimeoutError(), "network_timeout"),
        ):
            self.api.error = None
            await self.reader.poll()
            self.clock.advance(2)
            self.api.error = error
            s = await self.reader.poll()
            self.assertEqual(s.api_status, expected)
            self.assertEqual(
                s.connection, "offline" if expected == "offline" else "unknown"
            )
            self.assertIsNone(s.received_at)
            for r in s.fields.values():
                self.assertFalse(r.valid)
                self.assertIsNone(r.value)
                self.assertIsNotNone(r.last_good)
            self.assertNotIn("SYNTHETIC-TOKEN", json.dumps(s.to_dict()))
        self.api.error = None
        self.api.data["states"][TARGET]["airQualitySensor"]["PM2"] = 17
        s = await self.reader.poll()
        self.assertTrue(s.fields["pm2"].valid)
        self.assertEqual(s.fields["pm2"].value, 17)
        self.assertEqual(s.connection, "unknown")
        self.assertEqual(self.api.posts, 0)

    async def test_timeout_is_bounded_and_no_auto_retry(self):
        await self.reader.poll()
        self.api.delay = 1
        s = await self.reader.poll()
        self.assertEqual(s.api_status, "network_timeout")
        self.assertEqual(self.api.calls.count("state"), 2)

    async def test_cancellation_propagates_and_lock_is_released(self):
        await self.reader.poll()
        self.api.delay = 10
        task = asyncio.create_task(self.reader.poll())
        await asyncio.sleep(0.005)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.api.delay = 0
        self.assertEqual((await self.reader.poll()).api_status, "ok")

    async def test_invalid_response_shapes_and_initial_failure(self):
        for section, invalid, expected in (
            ("profiles", None, "invalid_profile"),
            ("profiles", {"property": []}, "invalid_profile"),
            ("states", None, "invalid_state"),
            ("states", {}, "invalid_state"),
            ("states", [], "invalid_state"),
        ):
            self.api.data[section][TARGET] = invalid
            s = await self.reader.poll()
            self.assertEqual(s.api_status, expected)
            self.assertFalse(any(r.valid for r in s.fields.values()))
            self.api.data = fixture()
            self.api._payload = self.api.data

    async def test_target_selection_strict_no_fallback_and_no_id_in_output(self):
        for devices, expected in (
            ([], "target_not_unique"),
            ([DEVICE, DEVICE], "target_not_unique"),
            (None, "invalid_device_list"),
        ):
            self.api.data["devices"] = devices
            self.assertEqual((await self.reader.poll()).api_status, expected)
            self.assertEqual(self.api.calls[-1], "devices")
        self.api.data["devices"] = [copy.deepcopy(DEVICE)]
        self.api.data["devices"][0]["deviceInfo"]["reportable"] = False
        self.assertEqual((await self.reader.poll()).api_status, "reporting_unavailable")
        self.api.data["devices"] = [copy.deepcopy(DEVICE)]
        self.api.data["devices"].append({"deviceId": "OTHER", "deviceType": "OTHER"})
        text = json.dumps((await self.reader.poll()).to_dict())
        for secret in (TARGET, "PRIVATE-ALIAS", "PRIVATE-ACCOUNT", "SYNTHETIC-TOKEN"):
            self.assertNotIn(secret, text)

    async def test_power_off_is_not_offline_and_no_control_prerequisite(self):
        self.api.data["states"][TARGET]["operation"]["airPurifierOperationMode"] = (
            "POWER_OFF"
        )
        s = await self.reader.poll()
        self.assertEqual(s.fields["power"].value, "POWER_OFF")
        self.assertEqual(s.connection, "unknown")
        self.assertTrue(s.fields["pm2"].valid)
        self.assertEqual(s.fields["pm2"].measurement_freshness, "unknown")

    async def test_explicit_timing_config_and_initial_snapshot(self):
        for bad in (0, -1, float("nan"), float("inf"), True, "30"):
            with self.assertRaises(ValueError):
                AirQualityCollector(self.api, max_receipt_age_sec=bad)
        s = self.reader.snapshot()
        self.assertEqual(s.api_status, "not_polled")
        self.assertFalse(any(r.valid for r in s.fields.values()))
        self.assertEqual(self.api.calls, [])
        with self.assertRaises(TypeError):
            s.fields["pm2"] = s.fields["pm1"]

    async def test_official_sdk_with_get_only_transport_and_fake_http(self):
        from tools.thinq_connect.test_manual_trial import Response

        payload = fixture()
        methods = []

        class Session:
            async def request(self, method, url, **kwargs):
                methods.append(method)
                assert method == "GET"
                assert kwargs["allow_redirects"] is False
                assert kwargs["headers"]["x-country"] == "KR"
                path = str(url).split(".com", 1)[1]
                if path == "/devices":
                    return Response({"response": payload["devices"]})
                if path == f"/devices/{TARGET}/profile":
                    return Response({"response": payload["profiles"][TARGET]})
                assert path == f"/devices/{TARGET}/state"
                return Response({"response": payload["states"][TARGET]})

        transport = Transport(Session())  # default execute=False blocks all POSTs
        sdk = ThinQApi(
            session=transport,
            access_token="SYNTHETIC-TOKEN",
            country_code="KR",
            client_id="SYNTHETIC-CLIENT",
        )

        class ReadApi:
            async def async_get_device_list(self):
                return await sdk.async_get_device_list()

            async def async_get_device_profile(self, device_id):
                transport.select(device_id)
                return await sdk.async_get_device_profile(device_id)

            async def async_get_device_status(self, device_id):
                return await sdk.async_get_device_status(device_id)

        reader = AirQualityCollector(ReadApi(), max_receipt_age_sec=30)
        self.assertEqual((await reader.poll()).api_status, "ok")
        self.assertEqual(methods, ["GET", "GET", "GET"])
        self.assertFalse(transport.attempted)

    async def test_sdk_debug_url_is_not_logged_and_filter_is_cleaned_up(self):
        logger = logging.getLogger("thinqconnect.thinq_api")
        before = list(logger.filters)
        original = self.api.async_get_device_list

        async def noisy():
            logger.warning("SYNTHETIC-TOKEN %s", TARGET)
            return await original()

        self.api.async_get_device_list = noisy
        with self.assertLogs(logger, level="DEBUG") as captured:
            await self.reader.poll()
            logger.warning("outside_collection")
        self.assertEqual(len(captured.output), 1)
        self.assertIn("outside_collection", captured.output[0])
        self.assertEqual(logger.filters, before)

    def use_observed_fixture(self):
        observed = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "as183hwwa_air_quality_observed_20261022.json"
            ).read_text()
        )
        self.api.data["profiles"][TARGET] = observed["profile"]
        self.api.data["states"][TARGET] = observed["state"]
        return observed["profile"], observed["state"]

    async def test_user_reported_get_shape_parses_without_invented_metadata(self):
        self.use_observed_fixture()
        snapshot = await self.reader.poll()
        expected = {
            "pm1": 8,
            "pm2": 8,
            "pm10": 10,
            "odor": 1,
            "total_pollution": 1,
            "filter_remain_percent": 96,
            "monitoring_mode": "ON_WORKING",
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                reading = snapshot.fields[name]
                self.assertEqual(reading.value, value)
                self.assertTrue(reading.valid)
                self.assertEqual(reading.status, "ok")
                self.assertIsNone(reading.measured_at)
                self.assertIsNone(reading.unit)
                self.assertEqual(reading.measurement_freshness, "unknown")
        self.assertEqual(snapshot.fields["pm1_level"].status, "unsupported")
        self.assertEqual(self.api.posts, 0)

    async def test_number_allows_missing_or_null_read_range(self):
        for definition in (
            {"type": "number", "mode": ["r"]},
            {"type": "number", "mode": ["r"], "value": {}},
            {"type": "number", "mode": ["r"], "value": None},
            {"type": "number", "mode": ["r"], "value": {"r": None}},
        ):
            profile, _ = self.use_observed_fixture()
            profile["property"]["airQualitySensor"]["PM2"] = definition
            snapshot = await self.reader.poll()
            self.assertEqual(snapshot.fields["pm2"].value, 8)
            self.assertTrue(snapshot.fields["pm2"].valid)

    async def test_number_accepts_finite_real_and_preserves_read_requirement(self):
        profile, state = self.use_observed_fixture()
        state["airQualitySensor"]["PM2"] = 8.25  # synthetic variant
        snapshot = await self.reader.poll()
        self.assertEqual(snapshot.fields["pm2"].value, 8.25)
        profile["property"]["airQualitySensor"]["PM2"]["mode"] = ["w"]
        self.assertEqual(
            (await self.reader.poll()).fields["pm2"].status, "not_readable"
        )

    async def test_number_explicit_range_is_enforced_per_field(self):
        profile, state = self.use_observed_fixture()
        profile["property"]["airQualitySensor"]["PM2"]["value"]["r"] = {
            "min": 2,
            "max": 9,
        }  # synthetic bounds, not a claim about this model
        for value, status in (
            (1, "out_of_range"),
            (2, "ok"),
            (9, "ok"),
            (10, "out_of_range"),
        ):
            state["airQualitySensor"]["PM2"] = value
            snapshot = await self.reader.poll()
            self.assertEqual(snapshot.fields["pm2"].status, status)
            self.assertTrue(snapshot.fields["pm1"].valid)

    async def test_number_malformed_ranges_are_not_treated_as_absent(self):
        for readable in (
            {},
            {"min": 0},
            {"min": 0, "max": "9"},
            {"min": 10, "max": 0},
            {"min": False, "max": 9},
            {"min": 0, "max": float("inf")},
            [],
            "0..9",
        ):
            profile, _ = self.use_observed_fixture()
            profile["property"]["airQualitySensor"]["PM2"]["value"]["r"] = readable
            snapshot = await self.reader.poll()
            self.assertEqual(snapshot.fields["pm2"].status, "unsupported_profile")
            self.assertTrue(snapshot.fields["pm1"].valid)
        profile["property"]["airQualitySensor"]["PM2"]["value"] = []
        self.assertEqual(
            (await self.reader.poll()).fields["pm2"].status, "unsupported_profile"
        )

    async def test_number_without_range_rejects_invalid_types_and_nonfinite(self):
        _, state = self.use_observed_fixture()
        for value in (
            True,
            False,
            "8",
            {},
            [],
            float("nan"),
            float("inf"),
            -float("inf"),
            10**1000,
        ):
            state["airQualitySensor"]["PM2"] = value
            snapshot = await self.reader.poll()
            self.assertEqual(snapshot.fields["pm2"].status, "invalid_value")
            self.assertIsNone(snapshot.fields["pm2"].value)
            self.assertTrue(snapshot.fields["pm1"].valid)
            self.assertTrue(snapshot.fields["filter_remain_percent"].valid)
            json.dumps(snapshot.to_dict(), allow_nan=False)

    async def test_number_semantic_bounds_remain_without_profile_range(self):
        _, state = self.use_observed_fixture()
        state["airQualitySensor"]["PM2"] = -1
        state["filterInfo"]["filterRemainPercent"] = 101
        snapshot = await self.reader.poll()
        self.assertEqual(snapshot.fields["pm2"].status, "out_of_range")
        self.assertEqual(
            snapshot.fields["filter_remain_percent"].status, "out_of_range"
        )
        self.assertTrue(snapshot.fields["monitoring_mode"].valid)

    async def test_monitoring_mode_is_exact_profile_enum_not_boolean(self):
        profile, state = self.use_observed_fixture()
        for value, status in (
            ("ON_WORKING", "ok"),
            ("ALWAYS", "ok"),
            ("UNKNOWN", "out_of_range"),
            (True, "invalid_value"),
            (1, "invalid_value"),
            (None, "missing"),
        ):
            state["airQualitySensor"]["monitoringEnabled"] = value
            snapshot = await self.reader.poll()
            self.assertEqual(snapshot.fields["monitoring_mode"].status, status)
            self.assertTrue(snapshot.fields["pm2"].valid)
        state["airQualitySensor"]["monitoringEnabled"] = "ON_WORKING"
        profile["property"]["airQualitySensor"]["monitoringEnabled"]["value"]["r"] = [
            "ALWAYS"
        ]
        self.assertEqual(
            (await self.reader.poll()).fields["monitoring_mode"].status, "out_of_range"
        )

    async def test_monitoring_mode_name_and_original_api_path(self):
        self.use_observed_fixture()
        snapshot = await self.reader.poll()
        device_state = snapshot.to_dict()["device_state"]
        self.assertNotIn("monitoring_enabled", device_state)
        self.assertNotIn("monitoring_enabled", snapshot.fields)
        self.assertEqual(device_state["monitoring_mode"]["value"], "ON_WORKING")
        self.assertEqual(
            device_state["monitoring_mode"]["source_property"],
            "airQualitySensor.monitoringEnabled",
        )

    async def test_observed_number_and_mode_cache_stale_failure_and_recovery(self):
        _, state = self.use_observed_fixture()
        await self.reader.poll()
        self.clock.advance(30)
        for name in ("pm2", "monitoring_mode"):
            reading = self.reader.snapshot().fields[name]
            self.assertEqual(reading.status, "stale")
            self.assertIsNone(reading.value)
            self.assertIsNotNone(reading.last_good)
        state["airQualitySensor"]["PM2"] = None
        state["airQualitySensor"]["monitoringEnabled"] = "UNKNOWN"
        lost = await self.reader.poll()
        self.assertEqual(lost.fields["pm2"].last_good.value, 8)
        self.assertEqual(lost.fields["monitoring_mode"].last_good.value, "ON_WORKING")
        self.assertFalse(lost.fields["pm2"].valid)
        self.assertFalse(lost.fields["monitoring_mode"].valid)
        self.assertTrue(lost.fields["pm1"].valid)
        self.clock.advance(1)
        state["airQualitySensor"]["PM2"] = 9
        state["airQualitySensor"]["monitoringEnabled"] = "ALWAYS"
        recovered = await self.reader.poll()
        self.assertEqual(recovered.fields["pm2"].value, 9)
        self.assertEqual(recovered.fields["pm2"].last_good.value, 9)
        self.assertEqual(recovered.fields["monitoring_mode"].value, "ALWAYS")
        self.assertEqual(recovered.fields["pm2"].last_good.api_age_sec, 0)

    def other_appliance_fixture(self):
        return json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "synthetic_other_appliance_profile.json"
            ).read_text()
        )

    def test_other_mapping_accepts_negative_number_only_when_profile_allows(self):
        other = self.other_appliance_fixture()
        spec = FieldSpec("measurements", "syntheticTelemetry", "temperature", "number")
        for readable in (None, {"min": -10, "max": 10}):
            other["profile"]["property"]["syntheticTelemetry"]["temperature"]["value"][
                "r"
            ] = readable
            self.assertEqual(
                _validate(spec, other["profile"], other["state"]), (-5.5, "ok")
            )
        other["profile"]["property"]["syntheticTelemetry"]["temperature"]["value"][
            "r"
        ] = {"min": 0, "max": 10}
        self.assertEqual(
            _validate(spec, other["profile"], other["state"]), (None, "out_of_range")
        )

    def test_other_enum_mapping_uses_its_profile_and_fails_closed(self):
        other = self.other_appliance_fixture()
        spec = FieldSpec("device_state", "syntheticOperation", "fanMode", "mode")
        self.assertEqual(
            _validate(spec, other["profile"], other["state"]), ("BOOST", "ok")
        )
        other["state"]["syntheticOperation"]["fanMode"] = "ON_WORKING"
        self.assertEqual(
            _validate(spec, other["profile"], other["state"]), (None, "out_of_range")
        )
        other["profile"]["property"]["syntheticOperation"]["fanMode"]["mode"] = ["w"]
        self.assertEqual(
            _validate(spec, other["profile"], other["state"]), (None, "not_readable")
        )
        missing = FieldSpec(
            "device_state", "syntheticOperation", "unsupportedMode", "mode"
        )
        self.assertEqual(
            _validate(missing, other["profile"], other["state"]), (None, "unsupported")
        )

    async def test_independent_collectors_keep_other_good_data_on_failure(self):
        # Isolated instances only, not a claim of multi-device runtime routing.
        other_api = Api()
        other = AirQualityCollector(other_api, max_receipt_age_sec=30)
        await self.reader.poll()
        await other.poll()
        self.api.error = Failure("1222")
        failed = await self.reader.poll()
        good = other.snapshot()
        self.assertEqual(failed.connection, "offline")
        self.assertIsNone(failed.fields["pm2"].value)
        self.assertTrue(good.fields["pm2"].valid)
        self.assertEqual(good.fields["pm2"].value, 12)
        self.assertEqual((await other.poll()).api_status, "ok")

    async def test_poc_does_not_auto_select_other_appliance_from_valid_profile(self):
        self.api.data["devices"][0]["deviceInfo"]["deviceType"] = (
            "SYNTHETIC_OTHER_APPLIANCE"
        )
        self.api.data["devices"][0]["deviceInfo"]["modelName"] = (
            "SYNTHETIC-NOT-A-REAL-MODEL"
        )
        snapshot = await self.reader.poll()
        self.assertEqual(snapshot.api_status, "target_not_unique")
        self.assertEqual(self.api.calls, ["devices"])
        self.assertEqual(self.api.posts, 0)


if __name__ == "__main__":
    unittest.main()
