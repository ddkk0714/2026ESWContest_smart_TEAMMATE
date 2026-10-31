#!/usr/bin/env python3
"""One purifier, manual AUTO->LOW->AUTO trial. Default is GET-only.

No credentials, identifiers, snapshots or responses are written to disk.
--execute is for a separately authorized future session, never implicit.
"""

import argparse
import asyncio
import copy
import getpass
import json
import logging
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from importlib.metadata import version

from . import probe

LOW_APPROVAL = "APPROVE AS183HWWA LOW ONLINE NO OTHER CONTROL"
AUTO_APPROVAL = "APPROVE AS183HWWA RESTORE AUTO NO EXTERNAL CHANGE"
PHYSICAL_LOW = "PHYSICAL LOW CONFIRMED"
PHYSICAL_AUTO = "PHYSICAL AUTO CONFIRMED"
CONFIG_GROUPS = ("operation", "airPurifierJobMode", "airFlow", "timer", "sleepTimer")


class StopTrial(Exception):
    """Only developer-controlled, non-sensitive reasons are accepted here."""


class ApiFailure(Exception):
    def __init__(self, http_status=None, code=None):
        self.http_status = http_status
        self.code = (
            code if isinstance(code, str) and re.fullmatch(r"[0-9]{4}", code) else None
        )


def profile_check(profile):
    try:
        prop = profile["property"]["airFlow"]["windStrength"]
        valid = (
            prop["type"] == "enum"
            and isinstance(prop["mode"], list)
            and {"r", "w"} <= set(prop["mode"])
            and all(
                isinstance(prop["value"][mode], list)
                and {"LOW", "AUTO"} <= set(prop["value"][mode])
                for mode in ("r", "w")
            )
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise StopTrial("windStrength LOW/AUTO read/write support not confirmed")


def config(state):
    if not isinstance(state, dict):
        raise StopTrial("State missing; do not control")
    try:
        power = state["operation"]["airPurifierOperationMode"]
        job = state["airPurifierJobMode"]["currentJobMode"]
        wind = state["airFlow"]["windStrength"]
    except (KeyError, TypeError):
        raise StopTrial("Required State properties missing") from None
    if power != "POWER_ON" or job != "CLEAN":
        raise StopTrial("POWER_ON / CLEAN prerequisite changed")
    if wind not in ("AUTO", "LOW"):
        raise StopTrial("Unexpected windStrength; possible external operation")
    return {key: copy.deepcopy(state[key]) for key in CONFIG_GROUPS if key in state}


def invariant(configuration):
    result = copy.deepcopy(configuration)
    result["airFlow"].pop("windStrength", None)
    result["airFlow"].pop("windStrengthDetail", None)
    return result


def public_state(state):
    # Fixed fields only; no arbitrary state objects or device identifiers.
    return {
        "power": state["operation"]["airPurifierOperationMode"],
        "job": state["airPurifierJobMode"]["currentJobMode"],
        "windStrength": state["airFlow"]["windStrength"],
        "windStrengthDetail": (
            state["airFlow"].get("windStrengthDetail")
            if state["airFlow"].get("windStrengthDetail")
            in (None, "OFF", "LOW", "MID", "HIGH", "POWER", "AUTO")
            else "[withheld]"
        ),
    }


class Transport:
    """Last network boundary: no redirects, one exact POST per approved stage."""

    def __init__(self, session, execute=False):
        self.session, self.execute = session, execute
        self.target = None
        self.permit = None
        self.attempted = set()
        self.last_status = None

    def select(self, device_id):
        if (
            self.target is not None
            or not isinstance(device_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", device_id)
        ):
            raise StopTrial("Invalid or repeated target selection")
        self.target = device_id

    def approve(self, value):
        if (
            not self.execute
            or value not in ("LOW", "AUTO")
            or value in self.attempted
            or self.permit is not None
        ):
            raise StopTrial("POST permit denied")
        if value == "AUTO" and "LOW" not in self.attempted:
            raise StopTrial("AUTO restore cannot precede LOW attempt")
        self.permit = value

    async def request(self, method, url, **kwargs):
        from yarl import URL

        parsed = URL(url)
        if (
            parsed.scheme != "https"
            or parsed.host != "api-kic.lgthinq.com"
            or parsed.port != 443
            or parsed.user is not None
            or parsed.query
            or parsed.fragment
        ):
            raise StopTrial("Unapproved API destination")
        get_paths = {"/devices"}
        if self.target is not None:
            get_paths |= {
                f"/devices/{self.target}/profile",
                f"/devices/{self.target}/state",
            }
        if method == "GET":
            if parsed.path not in get_paths:
                raise StopTrial("Unapproved GET endpoint")
        elif method == "POST":
            value = self.permit
            if (
                not self.execute
                or self.target is None
                or value is None
                or value in self.attempted
                or parsed.path != f"/devices/{self.target}/control"
                or kwargs.get("json") != {"airFlow": {"windStrength": value}}
            ):
                raise StopTrial("Unapproved POST endpoint, payload or stage")
            self.permit = None
            self.attempted.add(value)  # consume before I/O; never automatically resend
        else:
            raise StopTrial("Only selected GETs and approved trial POSTs are allowed")
        kwargs["allow_redirects"] = False
        self.last_status = None
        response = await self.session.request(method, url, **kwargs)
        self.last_status = response.status
        if not (200 <= response.status < 300 or 400 <= response.status < 600):
            response.release()
            raise ApiFailure(http_status=self.last_status)
        return response


class DeviceIO:
    def __init__(self, sdk, transport, probe):
        self.sdk, self.transport, self.probe = sdk, transport, probe
        self.target = None

    async def call(self, method, *args, timeout=15):
        self.transport.last_status = None
        try:
            return await getattr(self.sdk, method)(*args, timeout=timeout)
        except (StopTrial, ApiFailure):
            raise
        except Exception as error:
            raise ApiFailure(
                self.transport.last_status, getattr(error, "code", None)
            ) from None

    async def select(self):
        devices = await self.call("async_get_device_list")
        if not isinstance(devices, list) or not all(
            isinstance(item, dict) for item in devices
        ):
            raise StopTrial("Invalid device list")
        matches = [
            item
            for item in devices
            if self.probe.summarize_device(item)["device_type"] == "DEVICE_AIR_PURIFIER"
            and self.probe.summarize_device(item)["model_name"] == "AIR_910604_WW"
        ]
        if len(matches) != 1:
            raise StopTrial("Exactly one matching purifier is required")
        selected = matches[0]
        info = selected.get("deviceInfo")
        info = info if isinstance(info, dict) else {}
        if self.probe.summarize_device(selected)["reportable"] is not True:
            raise StopTrial("Device reporting unavailable")
        self.target = selected.get("deviceId") or info.get("deviceId")
        self.transport.select(self.target)

    async def profile(self):
        return await self.call("async_get_device_profile", self.target)

    async def state(self, timeout=15):
        return await self.call("async_get_device_status", self.target, timeout=timeout)

    async def post(self, value):
        self.transport.approve(value)
        await self.call(
            "async_post_device_control",
            self.target,
            {"airFlow": {"windStrength": value}},
        )


class Trial:
    def __init__(
        self,
        io,
        execute=False,
        confirm=input,
        emit=None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self.io, self.execute, self.confirm = io, execute, confirm
        self.emit = emit or (
            lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
        )
        self.sleep, self.clock = sleep, clock
        self.accepted = set()

    def report_state(self, event, state, **extra):
        self.emit(
            {
                "event": event,
                "received_at_utc": datetime.now(timezone.utc).isoformat(),
                "state": public_state(state),
                **extra,
            }
        )

    async def preflight(self):
        await self.io.select()
        profile_check(await self.io.profile())
        state = await self.io.state()
        baseline = config(state)
        if state["airFlow"]["windStrength"] != "AUTO":
            raise StopTrial("Baseline is not AUTO; no change permitted")
        self.report_state(
            "preflight_passed",
            state,
            api_state_available=True,
            physical_online_confirmation_required=True,
            post_enabled=self.execute,
        )
        return baseline

    async def observe(self, value, baseline):
        deadline = self.clock() + 60
        for index in range(7):
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            try:
                state = await self.io.state(timeout=min(15, remaining))
            except ApiFailure as error:
                self.emit(
                    {
                        "event": "GET_unavailable",
                        "http_status": error.http_status,
                        "error_code": error.code,
                    }
                )
                # Known offline/auth/rate-limit failures end the trial immediately.
                if error.code in (
                    "1103",
                    "1218",
                    "1302",
                    "1308",
                    "1222",
                    "1306",
                ) or error.http_status in (401, 403, 429):
                    raise StopTrial(
                        "Offline, authentication, authority or rate-limit error"
                    ) from None
            else:
                current = config(state)
                if invariant(current) != invariant(baseline):
                    raise StopTrial(
                        "Unrelated configuration changed; no further control"
                    )
                detail = state["airFlow"].get("windStrengthDetail")
                if detail not in (
                    baseline["airFlow"].get("windStrengthDetail"),
                    "LOW",
                    "AUTO",
                ):
                    raise StopTrial(
                        "Unexpected detailed wind setting; no further control"
                    )
                self.report_state("state_observed", state, expected_wind=value)
                if state["airFlow"]["windStrength"] == value:
                    return current
            if index < 6:
                await self.sleep(min(10, max(0, deadline - self.clock())))
        raise StopTrial("State confirmation timed out; no further POST")

    async def send_once(self, value, baseline):
        try:
            await self.io.post(value)
        except ApiFailure as error:
            self.emit(
                {
                    "event": "POST_outcome_unconfirmed",
                    "http_status": error.http_status,
                    "error_code": error.code,
                    "automatic_resend": False,
                    "instruction": "GET reconciliation only; inspect app and restore manually if necessary",
                }
            )
            await self.observe(value, baseline)
            raise StopTrial(
                "POST outcome uncertain; observed State does not authorize another POST"
            ) from None
        self.accepted.add(value)
        self.emit(
            {
                "event": "API_request_accepted",
                "windStrength": value,
                "state_confirmed": False,
                "physical_confirmed": False,
            }
        )
        return await self.observe(value, baseline)

    async def run(self):
        baseline = await self.preflight()
        if not self.execute:
            self.emit({"event": "GET_only_complete", "post_count": 0})
            return
        print(
            "Check the physical purifier is online, POWER_ON / CLEAN / AUTO, and nobody/schedule/automation will operate it."
        )
        if self.confirm("Type exactly: " + LOW_APPROVAL + "\n> ") != LOW_APPROVAL:
            raise StopTrial("LOW was not approved; no POST")
        profile_check(await self.io.profile())
        fresh = await self.io.state()
        if config(fresh) != baseline:
            raise StopTrial("Configuration changed after preflight; no POST")
        low = await self.send_once("LOW", baseline)
        physical_low = (
            self.confirm(
                "Inspect display/app and airflow. Type "
                + PHYSICAL_LOW
                + " only if physically confirmed; otherwise UNKNOWN:\n> "
            )
            == PHYSICAL_LOW
        )
        self.emit({"event": "physical_LOW_confirmation", "confirmed": physical_low})
        print(
            "AUTO restore is a separate write. If anyone operated the purifier, do not approve. No automatic rollback is attempted."
        )
        if self.confirm("Type exactly: " + AUTO_APPROVAL + "\n> ") != AUTO_APPROVAL:
            raise StopTrial(
                "AUTO restore not approved; inspect app and restore manually if needed"
            )
        if "LOW" not in self.accepted:
            raise StopTrial("No accepted LOW request; AUTO restore forbidden")
        profile_check(await self.io.profile())
        fresh = await self.io.state()
        if config(fresh) != low or fresh["airFlow"]["windStrength"] != "LOW":
            raise StopTrial("LOW state changed; AUTO restore forbidden")
        await self.send_once("AUTO", baseline)
        physical_auto = (
            self.confirm(
                "Inspect AUTO restore. Type "
                + PHYSICAL_AUTO
                + " only if physically confirmed; otherwise UNKNOWN:\n> "
            )
            == PHYSICAL_AUTO
        )
        self.emit(
            {
                "event": "trial_complete",
                "api_requests_accepted": True,
                "state_restored_AUTO": True,
                "physical_LOW_confirmed": physical_low,
                "physical_AUTO_confirmed": physical_auto,
                "full_success": physical_low and physical_auto,
            }
        )


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(
            2,
            "Invalid option. Use --check or --preflight; --execute requires separate authorization.\n",
        )


def main():
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("error", getpass.GetPassWarning)
    parser = SafeParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check", action="store_true", help="environment check only; no API"
    )
    modes.add_argument(
        "--preflight", action="store_true", help="GET only (also the default)"
    )
    modes.add_argument(
        "--execute",
        action="store_true",
        help="future separately authorized manual trial; per-POST approval required",
    )
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 12, 15) or version("thinqconnect") != "1.0.14":
        raise StopTrial("Expected Python 3.12.15 / thinqconnect 1.0.14")
    from aiohttp import ClientSession
    from thinqconnect.thinq_api import ThinQApi

    if args.check:
        print(
            "Python 3.12.15 / thinqconnect 1.0.14; repository probe.py loaded. No API requests sent."
        )
        return
    if not sys.stdin.isatty():
        raise StopTrial("Run in an interactive WSL terminal for hidden PAT entry")
    client_id = os.environ.get("THINQ_CLIENT_ID", "").strip()
    if not client_id:
        raise StopTrial("Set THINQ_CLIENT_ID in this terminal; no token requested")
    token = getpass.getpass("ThinQ PAT (hidden; never saved): ").strip()
    if not token:
        raise StopTrial("Empty PAT")

    async def session_main():
        async with ClientSession() as session:
            transport = Transport(session, execute=args.execute)
            sdk = ThinQApi(
                session=transport,
                access_token=token,
                country_code="KR",
                client_id=client_id,
            )
            trial = Trial(DeviceIO(sdk, transport, probe), execute=args.execute)
            # No raw SDK objects, response bodies or exceptions are ever printed.
            try:
                await trial.run()
            finally:
                if "LOW" in transport.attempted:
                    print(
                        "A LOW write was attempted. Confirm final AUTO in ThinQ/app or manually restore; no cleanup POST is sent."
                    )

    asyncio.run(session_main())


if __name__ == "__main__":
    try:
        main()
    except StopTrial as error:
        print("STOP: " + str(error))
        raise SystemExit(1)
    except ApiFailure as error:
        print(
            json.dumps(
                {
                    "event": "API_failure",
                    "http_status": error.http_status,
                    "error_code": error.code,
                }
            )
        )
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print(
            "Stopped by user. No automatic control or restoration. If LOW may have been sent, inspect and restore in the ThinQ app."
        )
        raise SystemExit(1)
    except Exception:
        print(
            "Stopped: unexpected failure; details withheld. No automatic restoration; inspect the purifier if a write was attempted."
        )
        raise SystemExit(1)
