#!/usr/bin/env python3
"""Read-only LG ThinQ Connect device discovery.

The token is accepted only through a hidden prompt or ``THINQ_PAT``. This tool
deliberately exposes no device-control operation.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol


class ThinQReadApi(Protocol):
    async def async_get_device_list(self) -> list[dict[str, Any]] | None: ...

    async def async_get_device_profile(self, device_id: str) -> dict[str, Any] | None: ...

    async def async_get_device_status(self, device_id: str) -> dict[str, Any] | None: ...


def _mask_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


def summarize_device(device: dict[str, Any], *, show_device_id: bool = False) -> dict[str, Any]:
    """Return the device-list fields useful for choosing a DESKMATE target."""

    info = device.get("deviceInfo")
    if not isinstance(info, dict):
        info = {}
    device_id = device.get("deviceId")
    if device_id is None:
        device_id = info.get("deviceId")
    return {
        "device_id": device_id if show_device_id else _mask_identifier(device_id),
        "device_type": info.get("deviceType", device.get("deviceType")),
        "alias": info.get("alias", device.get("alias")),
        "model_name": info.get("modelName", device.get("modelName")),
        "reportable": info.get("reportable", device.get("reportable")),
        "controllable": info.get("controllable", device.get("controllable")),
    }


async def list_devices(api: ThinQReadApi, *, show_device_id: bool = False) -> list[dict[str, Any]]:
    response = await api.async_get_device_list()
    if response is None:
        return []
    if not isinstance(response, list) or not all(isinstance(item, dict) for item in response):
        raise ValueError("ThinQ device-list response is not a list of objects")
    return [summarize_device(item, show_device_id=show_device_id) for item in response]


async def inspect_device(api: ThinQReadApi, device_id: str) -> dict[str, Any]:
    """Fetch the profile (capabilities) and current state without controlling it."""

    profile, state = await asyncio.gather(
        api.async_get_device_profile(device_id),
        api.async_get_device_status(device_id),
    )
    return {"device_id": device_id, "profile": profile, "state": state}


class FileMockApi:
    """Offline fixture adapter for authentication-free development and tests."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def async_get_device_list(self) -> list[dict[str, Any]] | None:
        return self._payload.get("devices")

    async def async_get_device_profile(self, device_id: str) -> dict[str, Any] | None:
        return self._payload.get("profiles", {}).get(device_id)

    async def async_get_device_status(self, device_id: str) -> dict[str, Any] | None:
        return self._payload.get("states", {}).get(device_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only LG ThinQ Connect probe")
    parser.add_argument(
        "--mock-file",
        type=Path,
        help="use a local JSON fixture instead of credentials or network access",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="GET /devices")
    list_parser.add_argument(
        "--show-device-id",
        action="store_true",
        help="show full device IDs locally (masked by default)",
    )
    inspect_parser = subparsers.add_parser(
        "inspect", help="GET /devices/{id}/profile and /devices/{id}/state"
    )
    inspect_parser.add_argument("device_id")
    return parser


async def _run(args: argparse.Namespace) -> Any:
    if args.mock_file is not None:
        payload = json.loads(args.mock_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("mock file root must be a JSON object")
        api: ThinQReadApi = FileMockApi(payload)
        if args.command == "list":
            return await list_devices(api, show_device_id=args.show_device_id)
        return await inspect_device(api, args.device_id)

    country = os.environ.get("THINQ_COUNTRY", "KR").strip().upper()
    client_id = os.environ.get("THINQ_CLIENT_ID", "").strip()
    if len(country) != 2:
        raise ValueError("THINQ_COUNTRY must be a two-letter country code (KR for Korea)")
    if not client_id:
        raise ValueError("set a stable THINQ_CLIENT_ID; do not generate a new ID on every run")
    token = os.environ.get("THINQ_PAT") or getpass.getpass("ThinQ PAT (not echoed): ")
    if not token:
        raise ValueError("ThinQ PAT is required")

    try:
        from aiohttp import ClientSession
        from thinqconnect.thinq_api import ThinQApi
    except ImportError as error:
        raise RuntimeError("install the official 'thinqconnect' Python SDK in a local venv") from error

    async with ClientSession() as session:
        api = ThinQApi(
            session=session,
            access_token=token,
            country_code=country,
            client_id=client_id,
        )
        if args.command == "list":
            return await list_devices(api, show_device_id=args.show_device_id)
        return await inspect_device(api, args.device_id)


def public_output(value: Any, *, show_device_id: bool = False) -> Any:
    """CLI output only: omit personal aliases, account data and SDK secrets."""
    if isinstance(value, list):
        return [public_output(item, show_device_id=show_device_id) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        normalized = str(key).lower().replace('_', '').replace('-', '')
        if any(part in normalized for part in ('token', 'authorization', 'password', 'secret')) or normalized in (
                'alias', 'email', 'userid', 'accountid', 'serialnumber', 'macaddress', 'ssid'):
            result[key] = '[redacted]'
        elif normalized == 'deviceid' and not show_device_id:
            result[key] = _mask_identifier(item)
        else:
            result[key] = public_output(item, show_device_id=show_device_id)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception:
        # SDK exceptions may embed HTTP headers including PAT. Never stringify
        # exceptions or emit their traceback in this discovery CLI.
        print("ThinQ probe failed: request_or_configuration_error (details redacted)", file=sys.stderr)
        return 1
    print(json.dumps(public_output(result, show_device_id=getattr(args, "show_device_id", False)),
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
