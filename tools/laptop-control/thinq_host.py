"""Local ThinQ session. PAT is read by getpass and never crosses the web bridge."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hmac
import logging
import secrets
import sys
import time
import uuid
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "thinq_vendor"))
from poc.session import Session
from tools.thinq_connect.manual_trial import ApiFailure, StopTrial, Transport


class GuardedSdk:
    """Connect PR81's single-use permit to its existing SDK HTTP boundary."""

    def __init__(self, sdk, transport):
        self.sdk, self.transport = sdk, transport
        self.permit = None

    def _target(self, target):
        if self.transport.target is None:
            self.transport.select(target)
        elif self.transport.target != target:
            raise StopTrial("target_changed")

    async def async_get_device_list(self, **kwargs):
        return await self.sdk.async_get_device_list(**kwargs)

    async def async_get_device_profile(self, target, **kwargs):
        self._target(target)
        return await self.sdk.async_get_device_profile(target, **kwargs)

    async def async_get_device_status(self, target, **kwargs):
        self._target(target)
        return await self.sdk.async_get_device_status(target, **kwargs)

    async def async_post_device_control(self, target, payload, **kwargs):
        self._target(target)
        value = payload.get("airFlow", {}).get("windStrength")
        if payload != {"airFlow": {"windStrength": value}} or value not in ("LOW", "AUTO"):
            raise StopTrial("unapproved_payload")
        if self.permit is None or self.permit.value != value:
            raise StopTrial("no_single_use_permit")
        self.permit.value = None
        self.transport.approve(value)
        return await self.sdk.async_post_device_control(target, payload, **kwargs)


class HostSession:
    def __init__(self, session, *, lifetime=1200, clock=time.monotonic):
        self.session, self.clock = session, clock
        self.deadline = clock() + lifetime
        self.key = secrets.token_hex(32)
        self.claimed = False
        self.lock = asyncio.Lock()
        self.revoked = False

    def active(self):
        return not self.revoked and self.clock() < self.deadline

    def claim(self):
        if self.claimed or not self.active():
            return None
        self.claimed = True
        return {"key": self.key, "expires_in_seconds": int(self.deadline-self.clock())}

    async def call(self, key, request):
        if not self.active() or not hmac.compare_digest(str(key), self.key):
            raise PermissionError("session_expired_or_invalid")
        if self.lock.locked():
            raise RuntimeError("busy")
        if not isinstance(request, dict) or request.get("op") not in {
            "read", "prepare", "execute", "physical_confirmation", "revoke"
        }:
            raise ValueError("unsupported_operation")
        allowed = {"op", "value", "nonce", "approved", "physical_online_confirmed", "no_external_change"}
        if set(request)-allowed:
            raise ValueError("unexpected_request_fields")
        async with self.lock:
            if not self.active():
                raise PermissionError("session_expired")
            if request["op"] == "revoke":
                self.revoked = True
                self.session.ctx["enabled"] = False
                self.session.ctx.pop("challenge", None)
                return {"status": "session_revoked"}
            # Context, target IDs and credentials never leave this process.
            reply = await self.session.safe({**request, "backend": "live_manual"})
            return reply["public"]


def make_app(host):
    from aiohttp import web

    async def bootstrap(request):
        if request.headers.get("X-Deskmate-Bootstrap") != "1":
            raise web.HTTPForbidden()
        result = host.claim()
        if result is None:
            return web.json_response({"status": "session_unavailable"}, status=409)
        return web.json_response(result)

    async def call(request):
        try:
            result = await host.call(request.headers.get("X-Deskmate-Session", ""), await request.json())
            return web.json_response(result)
        except PermissionError:
            return web.json_response({"status": "session_expired"}, status=401)
        except (ValueError, TypeError):
            return web.json_response({"status": "invalid_operation"}, status=400)
        except RuntimeError:
            return web.json_response({"status": "busy"}, status=409)

    @web.middleware
    async def headers(request, handler):
        if request.host != "127.0.0.1:8792":
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Cross-Origin-Resource-Policy": "same-origin"})
        return response

    app = web.Application(client_max_size=2048, middlewares=[headers])
    app.router.add_get("/bootstrap", bootstrap)
    app.router.add_post("/call", call)
    return app


async def serve(token, *, allow_control=False):
    from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web
    from thinqconnect.thinq_api import ThinQApi

    async with ClientSession(trust_env=False, cookie_jar=DummyCookieJar(),
                             timeout=ClientTimeout(total=15)) as http:
        transport = Transport(http, execute=allow_control)
        sdk = GuardedSdk(ThinQApi(session=transport, access_token=token,
                                  country_code="KR", client_id=str(uuid.uuid4())), transport)
        host = HostSession(Session(sdk, {"enabled": allow_control, "backend": "live_manual"}))
        runner = web.AppRunner(make_app(host), access_log=None)
        await runner.setup()
        # Docker publishes this container port on host 127.0.0.1 only.
        await web.TCPSite(runner, "0.0.0.0", 8792).start()
        print("ThinQ 인증 세션 준비됨. 노트북 제어 화면의 ThinQ 연결을 눌러 주세요. 유효 시간 20분.", flush=True)
        try:
            while host.active():
                await asyncio.sleep(.5)
        finally:
            await runner.cleanup()
            host.revoked = True
    print("ThinQ 인증 세션이 종료됐어요.", flush=True)


def main():
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("error", getpass.GetPassWarning)
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-control", action="store_true")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("PAT는 노트북의 대화형 터미널에서 직접 입력해 주세요.")
    from importlib.metadata import version
    if version("thinqconnect") != "1.0.14":
        raise SystemExit("thinqconnect 1.0.14가 필요해요.")
    token = getpass.getpass("LG ThinQ PAT (숨김 입력, 저장하지 않음): ").strip()
    if not token:
        raise SystemExit("입력된 PAT가 없어요.")
    try:
        asyncio.run(serve(token, allow_control=args.allow_control))
    except KeyboardInterrupt:
        pass
    except Exception:
        # SDK/network exceptions can carry headers; never print their contents.
        raise SystemExit("ThinQ 연결을 시작하지 못했어요. 인증과 네트워크 상태를 확인해 주세요.") from None


if __name__ == "__main__":
    main()
