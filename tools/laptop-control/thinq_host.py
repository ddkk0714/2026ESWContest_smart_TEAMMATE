"""Local ThinQ session. PAT is read by getpass and never crosses the web bridge."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hmac
import json
import logging
import secrets
import sys
import time
import os
import warnings
import stat
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "thinq_vendor"))
from poc.session import Session
from poc.session import OBSERVATION_FIELDS as FIELDS
from thinq_bridge import ReadSnapshot
from tools.thinq_connect.manual_trial import ApiFailure, StopTrial, Transport


def policy_support():
    root = Path(__file__).resolve().parents[2] / "hub" / "deskmate_hub"
    config = root / "config" / "air_purifier_policy.yaml"
    if not config.is_file():
        return None, None  # Legacy Docker GET remains usable without shadow policy files.
    sys.path.insert(0, str(root / "control"))
    import air_purifier_policy
    return air_purifier_policy, json.loads(config.read_text())


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
        self.session.authorization_valid = self.active
        self.backend = session.ctx.get("backend", "live_manual")
        self.snapshot = ReadSnapshot(FIELDS, mode="fixture" if self.backend == "fixture" else "live", monotonic=clock)
        self.policy_module, self.policy_config = policy_support()
        self.policy = self.policy_module.AirPurifierPolicy(self.policy_config, clock=clock) if self.policy_module else None
        self.fixture_policy_active = False
        self.policy_phase = None

    def active(self):
        return not self.revoked and self.clock() < self.deadline

    def claim(self):
        if self.claimed or not self.active():
            return None
        self.claimed = True
        return {"key": self.key, "expires_in_seconds": int(self.deadline-self.clock()),
                "mode": "fixture" if self.backend == "fixture" else "live",
                "access": "read_only" if self.backend == "live_read_only" or not self.session.ctx["enabled"] else "manual_control"}

    async def call(self, key, request):
        if not self.active() or not hmac.compare_digest(str(key), self.key):
            raise PermissionError("session_expired_or_invalid")
        if self.lock.locked():
            raise RuntimeError("busy")
        if not isinstance(request, dict) or request.get("op") not in {
            "read", "snapshot", "observation", "prepare", "execute", "physical_confirmation", "revoke",
            "policy", "policy_fixture", "policy_respond"
        }:
            raise ValueError("unsupported_operation")
        if self.backend == "live_read_only" and request["op"] not in {"read", "snapshot", "revoke", "policy"}:
            raise ValueError("live_read_only_operation_blocked")
        allowed = {"op", "value", "nonce", "approved", "physical_online_confirmed", "no_external_change"}
        if request["op"] in {"policy", "policy_fixture", "policy_respond"}:
            allowed = {"policy": {"op", "phase"}, "policy_fixture": {"op", "scenario"},
                       "policy_respond": {"op", "proposal_id", "decision"}}[request["op"]]
        if set(request)-allowed:
            raise ValueError("unexpected_request_fields")
        async with self.lock:
            if not self.active():
                raise PermissionError("session_expired")
            if request["op"].startswith("policy"):
                if self.policy is None:
                    return {"mode": "fixture" if self.backend == "fixture" else "live", "post_enabled": False,
                            "execution": "unavailable", "reason": "policy_sources_not_packaged", "proposal": None}
                if request["op"] == "policy_fixture":
                    if self.backend != "fixture":
                        raise ValueError("fixture_policy_only")
                    self.policy = self.policy_module.fixture_policy(self.policy_config, request.get("scenario"), clock=self.clock)
                    self.fixture_policy_active = True
                elif request["op"] == "policy":
                    if not self.fixture_policy_active:
                        self.policy_phase = request.get("phase")
                        self.policy.observe(self.policy_phase, self.snapshot.view())
                else:
                    if self.backend != "fixture":
                        raise ValueError("fixture_mock_response_only")
                    if not self.fixture_policy_active:
                        self.policy.observe(self.policy_phase, self.snapshot.view())
                    self.policy.respond(request.get("proposal_id"), request.get("decision"))
                result = self.policy.view()
                result["mode"] = "fixture" if self.backend == "fixture" else "live"
                result["fixture_timeline"] = self.fixture_policy_active
                return result
            if request["op"] == "snapshot":
                return self.snapshot.view()
            if request["op"] == "observation":
                return self.snapshot.export()
            if request["op"] == "revoke":
                self.revoked = True
                self.session.ctx["enabled"] = False
                self.session.ctx.pop("challenge", None)
                self.snapshot.update({"status": "session_revoked"})
                return {"status": "session_revoked", "post_enabled": False, "snapshot": self.snapshot.view()}
            # Context, target IDs and credentials never leave this process.
            attempts = len(self.session.ctx["attempted"])
            reply = await self.session.safe({**request, "backend": self.backend})
            public = reply["public"]
            if public.get("status") == "outcome_unconfirmed" or (
                request["op"] == "execute" and len(self.session.ctx["attempted"]) > attempts
                and public.get("status") != "state_match"
            ):
                self.session.ctx.update(uncertain=True, enabled=False)
                public["post_enabled"] = False
            if request["op"] in ("read", "execute"):
                self.snapshot.update(public)
            if not self.active():
                public["post_enabled"] = False
            public["mode"] = "fixture" if self.backend == "fixture" else "live"
            public["snapshot"] = self.snapshot.view()
            return public


def make_app(host, *, port=8792):
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
        if request.host != f"127.0.0.1:{port}":
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Cross-Origin-Resource-Policy": "same-origin"})
        return response

    app = web.Application(client_max_size=2048, middlewares=[headers])
    app.router.add_get("/bootstrap", bootstrap)
    app.router.add_post("/call", call)
    return app


async def serve_fixture(*, allow_control=False, port=8792):
    from aiohttp import web
    from poc.worker import FixtureSdk
    fixture = json.loads((Path(__file__).parent / "thinq_vendor/poc/fixture.json").read_text())
    host = HostSession(Session(FixtureSdk(fixture), {"enabled": allow_control, "backend": "fixture"}))
    runner = web.AppRunner(make_app(host, port=port), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"Fixture ONLY: no PAT, SDK or Cloud. http://127.0.0.1:{port}", flush=True)
    try:
        while host.active():
            await asyncio.sleep(.5)
    finally:
        host.revoked = True
        await runner.cleanup()


async def serve(token, *, client_id, allow_control=False, port=8792, loopback=False):
    from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web
    from thinqconnect.thinq_api import ThinQApi

    async with ClientSession(trust_env=False, cookie_jar=DummyCookieJar(),
                             timeout=ClientTimeout(total=15)) as http:
        transport = Transport(http, execute=allow_control)
        sdk = GuardedSdk(ThinQApi(session=transport, access_token=token,
                                  country_code="KR", client_id=client_id), transport)
        host = HostSession(Session(sdk, {"enabled": allow_control, "backend": "live_manual" if allow_control else "live_read_only"}))
        runner = web.AppRunner(make_app(host, port=port), access_log=None)
        await runner.setup()
        # Docker publishes this container port on host 127.0.0.1 only.
        await web.TCPSite(runner, "127.0.0.1" if loopback else "0.0.0.0", port).start()
        print("ThinQ 인증 세션 준비됨. 노트북 제어 화면의 ThinQ 연결을 눌러 주세요. 유효 시간 20분.", flush=True)
        try:
            while host.active():
                await asyncio.sleep(.5)
        finally:
            await runner.cleanup()
            host.revoked = True
    print("ThinQ 인증 세션이 종료됐어요.", flush=True)


def load_or_create_client_id(path=None, *, create=True):
    """Load the persistent UUIDv4 for Live Read-only, creating it once.

    Existing files with unexpected permissions or contents are rejected and
    never replaced. Exclusive creation prevents concurrent starts from
    generating separate IDs. The value is returned only to this process.
    """
    target = Path(path) if path is not None else Path.home() / ".config" / "deskmate-thinq" / "client-id"
    parent = target.parent
    if create:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = parent.lstat()
    if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid() or stat.S_IMODE(parent_info.st_mode) != 0o700):
        raise ValueError("private Client ID directory has unsafe owner or mode")

    def read_existing():
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1):
            raise ValueError("Client ID file has unsafe type, owner, or mode")
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                raise ValueError("Client ID file changed during validation")
            raw = os.read(fd, 129)
        finally:
            os.close(fd)
        if len(raw) > 128:
            raise ValueError("Client ID file has invalid length")
        try:
            value = raw.decode("ascii").strip()
        except UnicodeError:
            raise ValueError("Client ID file is not ASCII") from None
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            raise ValueError("Client ID is not a UUIDv4") from None
        if parsed.version != 4 or str(parsed) != value.lower():
            raise ValueError("Client ID is not a canonical UUIDv4")
        return value

    if target.exists() or target.is_symlink():
        return read_existing()

    if not create:
        raise ValueError("existing Client ID file required")

    # Claim the path before generating anything. A concurrent process that
    # wins the exclusive create is reused without minting another value.
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return read_existing()
    try:
        os.fchmod(fd, 0o600)
        value = str(uuid.uuid4())
        os.write(fd, (value + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return read_existing()


def main():
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("error", getpass.GetPassWarning)
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-control", action="store_true")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--live-manual", action="store_true")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("invalid_fixture_port")
    if sum((args.fixture, args.live_read_only, args.live_manual)) > 1 or args.live_read_only and args.allow_control or args.live_manual and args.allow_control:
        raise SystemExit("live_read_only_conflicting_mode")
    if args.fixture:
        asyncio.run(serve_fixture(allow_control=args.allow_control, port=args.port))
        return
    if args.port != 8792 and not (args.live_read_only or args.live_manual):
        raise SystemExit("live_port_change_not_enabled")
    if not sys.stdin.isatty():
        raise SystemExit("PAT는 노트북의 대화형 터미널에서 직접 입력해 주세요.")
    from importlib.metadata import version
    if version("thinqconnect") != "1.0.14":
        raise SystemExit("thinqconnect 1.0.14가 필요해요.")
    if args.live_read_only or args.live_manual:
        try:
            client_id = load_or_create_client_id(create=False) if args.live_manual else load_or_create_client_id()
        except (OSError, ValueError):
            raise SystemExit("사용자 전용 Client ID 설정을 확인할 수 없어요. 권한이나 UUID 형식을 확인해 주세요.") from None
    else:
        client_id = os.environ.get("THINQ_CLIENT_ID", "").strip()
        if not client_id:
            client_id = getpass.getpass("기존 고정 ThinQ Client ID (숨김 입력, 새로 생성하지 않음): ").strip()
        if not client_id or len(client_id) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in client_id):
            raise SystemExit("기존 고정 Client ID가 필요해요.")
    token = getpass.getpass("LG ThinQ PAT (숨김 입력, 저장하지 않음): ").strip()
    if not token:
        raise SystemExit("입력된 PAT가 없어요.")
    try:
        asyncio.run(serve(token, client_id=client_id, allow_control=args.allow_control or args.live_manual,
                          port=args.port, loopback=args.live_read_only or args.live_manual))
    except KeyboardInterrupt:
        pass
    except Exception:
        # SDK/network exceptions can carry headers; never print their contents.
        raise SystemExit("ThinQ 연결을 시작하지 못했어요. 인증과 네트워크 상태를 확인해 주세요.") from None


if __name__ == "__main__":
    main()
