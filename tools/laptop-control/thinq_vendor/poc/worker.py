"""Bounded NDJSON child; native parent alone owns transport/credentials."""

import asyncio
import copy
import json
import sys

from poc.session import SafeApiFailure, Session
from tools.thinq_connect.manual_trial import ApiFailure


class FixtureSdk:
    def __init__(self, fixture, wind="AUTO"):
        self.fixture = fixture
        self.wind = wind
        self.device_id = "fixture-only-not-a-device-id"
        self.calls = []
        self.fail = None
        self.response_loss = False
        self.mismatch = False

    async def async_get_device_list(self, **kwargs):
        self.calls.append("devices")
        if self.fail:
            raise ApiFailure(*self.fail)
        return [
            {
                "deviceId": self.device_id,
                "deviceInfo": {
                    "deviceType": "DEVICE_AIR_PURIFIER",
                    "modelName": "AIR_910604_WW",
                    "reportable": True,
                },
            }
        ]

    async def async_get_device_profile(self, target, **kwargs):
        self.calls.append("profile")
        if self.fail:
            raise ApiFailure(*self.fail)
        return copy.deepcopy(self.fixture["profile"])

    async def async_get_device_status(self, target, **kwargs):
        self.calls.append("state")
        if self.fail:
            raise ApiFailure(*self.fail)
        state = copy.deepcopy(self.fixture["state"])
        state["airFlow"]["windStrength"] = self.wind
        state["airFlow"]["windStrengthDetail"] = self.wind
        return state

    async def async_post_device_control(self, target, payload, **kwargs):
        value = payload["airFlow"]["windStrength"]
        if self.permit.value != value:
            raise ApiFailure()
        self.permit.value = None
        self.calls.append("post")
        if not self.mismatch:
            self.wind = value
        if self.response_loss:
            raise ApiFailure()
        return {}


class NativeSdk:
    """Exact SDK 1.0.14 endpoints; no credentials or arbitrary URL support."""

    async def request(self, method, path, payload=None):
        print(
            json.dumps(
                {"kind": "http", "method": method, "path": path, "payload": payload}
            ),
            flush=True,
        )
        response = json.loads(sys.stdin.readline(1048577))
        if response.get("error"):
            raise SafeApiFailure(
                response.get("http_status"), response.get("code"), response.get("error")
            )
        return response.get("response")

    async def async_get_device_list(self, **kwargs):
        return await self.request("GET", "/devices")

    async def async_get_device_profile(self, target, **kwargs):
        return await self.request("GET", f"/devices/{target}/profile")

    async def async_get_device_status(self, target, **kwargs):
        return await self.request("GET", f"/devices/{target}/state")

    async def async_post_device_control(self, target, payload, **kwargs):
        if self.permit.value != payload["airFlow"]["windStrength"]:
            raise ApiFailure()
        self.permit.value = None
        return await self.request("POST", f"/devices/{target}/control", payload)


def main(fixture):
    try:
        data = json.loads(sys.stdin.readline(65537))
        request, context = data["request"], data.get("context", {})
        sdk = (
            FixtureSdk(fixture, context.get("wind", "AUTO"))
            if request.get("backend", "fixture") == "fixture"
            else NativeSdk()
        )
        result = asyncio.run(Session(sdk, context).safe(request))
        print(json.dumps({"kind": "reply", **result}, allow_nan=False), flush=True)
    except Exception:
        print(
            '{"kind":"reply","public":{"status":"error","error":"worker_failed"},"context":{}}',
            flush=True,
        )
