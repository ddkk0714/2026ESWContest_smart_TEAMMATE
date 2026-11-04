"""Manual wind or opt-in power trial using Session and the final HTTP guard.

Default: GET preflight only. --manual requires independent typed approval for
each POST. No server, MQTT writer, persistence, automatic retry or restore.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import sys
import warnings
from importlib.metadata import version

from thinq_host import GuardedSdk, HostSession, Session, load_or_create_client_id
from tools.thinq_connect.manual_trial import (
    AUTO_APPROVAL, LOW_APPROVAL, PHYSICAL_AUTO, PHYSICAL_LOW, Transport,
)

POWER_APPROVAL = {value: f"APPROVE AS183HWWA {value} ONLINE NO OTHER CONTROL"
                  for value in ("POWER_OFF", "POWER_ON")}


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes unknown arguments, possibly a secret.
        self.exit(2, "허용 옵션은 --manual, --power-control, --help입니다. 인증정보는 인자로 넣지 마세요.\n")


def record(event):
    # Fixed, allowlisted records only. Never print an SDK object or exception.
    print(json.dumps(event, ensure_ascii=False), flush=True)


def state_summary(reply):
    state = reply.get("state")
    if not isinstance(state, dict):
        return None
    allowed = {"power": {"POWER_ON", "POWER_OFF"}, "job": {"CLEAN", "SLEEP"},
               "windStrength": {"LOW", "AUTO", "MID", "HIGH", "POWER"},
               "windStrengthDetail": {None, "OFF", "LOW", "AUTO", "MID", "HIGH", "POWER"}}
    return {key: state.get(key) if state.get(key) in values else None for key, values in allowed.items()}


async def run_trial(host, *, manual=False, power_control=False, confirm=input, emit=record):
    """One fixed target; each permitted stage at most once, with no restore."""
    key = host.key  # In-process call; no HTTP listener or exposed bootstrap.
    try:
        read = await host.call(key, {"op": "read"})
        state = state_summary(read)
        ready = (read.get("status") == "read_ok" and read.get("wind_profile_verified") is True
                 and state is not None and state["power"] == "POWER_ON"
                 and state["job"] == "CLEAN" and state["windStrength"] == "AUTO")
        if power_control:
            ready = (read.get("status") == "read_ok" and read.get("power_profile_verified") is True
                     and state is not None and state["power"] in ("POWER_ON", "POWER_OFF"))
        emit({"event": "preflight", "status": "ready" if ready else "blocked",
              "wind_profile_verified": read.get("wind_profile_verified") is True,
              "power_profile_verified": read.get("power_profile_verified") is True,
              "state": state, "connection": "unknown", "post_attempted": False,
              "control_permission_verified": False, "physical_online_confirmed": False})
        if not ready:
            return 1
        if not manual:
            return 0
        commands = "set_power off / set_power on / GET / QUIT" if power_control else "LOW / AUTO / GET / QUIT"
        print("수동 시험 대기. 각 동작은 별도 승인입니다. 명령: " + commands, flush=True)
        while host.active():
            command = confirm("명령 (승인 전에는 POST 없음): ").strip()
            if power_control:
                command = {"set_power off": "POWER_OFF", "set_power on": "POWER_ON"}.get(command, command)
            if command == "QUIT":
                return 0
            if command == "GET":
                reply = await host.call(key, {"op": "read"})
                emit({"event": "read", "status": reply.get("status"), "state": state_summary(reply)})
                if reply.get("status") != "read_ok":
                    return 1
                continue
            if command not in (("POWER_ON", "POWER_OFF") if power_control else ("LOW", "AUTO")):
                print(commands + " 중 선택해 주세요.", flush=True)
                continue
            prepared = await host.call(key, {"op": "prepare", "value": command})
            if prepared.get("status") != "approval_required":
                emit({"event": "approval", "value": command, "status": "blocked", "post_attempted": False})
                return 1
            phrase = POWER_APPROVAL[command] if power_control else LOW_APPROVAL if command == "LOW" else AUTO_APPROVAL
            print("본체 온라인·현재 전원 상태·외부 조작/예약 없음을 확인하세요. 승인은 30초입니다. 전원 ON은 AUTO 변경을 포함하지 않습니다.", flush=True)
            if confirm("별도 동작 승인 후 다음 문구를 정확히 입력하세요:\n" + phrase + "\n> ") != phrase:
                emit({"event": "approval", "value": command, "status": "cancelled", "post_attempted": False})
                return 0
            attempted_before = len(host.session.ctx["attempted"])
            reply = await host.call(key, {"op": "execute", "nonce": prepared["nonce"],
                                         "approved": True, "physical_online_confirmed": True,
                                         "no_external_change": True})
            emit({"event": "post_response", "value": command,
                  "post_attempted": len(host.session.ctx["attempted"]) > attempted_before,
                  "api_accepted": reply.get("api_accepted"), "status": reply.get("status"),
                  "automatic_resend": False})
            emit({"event": "get_readback", "value": command,
                  "result_available": "state_match" in reply,
                  "state_match": reply.get("state_match"),
                  "state": state_summary(reply), "physical_confirmed": False})
            if (reply.get("status") != "state_match" or reply.get("api_accepted") is not True
                    or reply.get("state_match") is not True):
                print("결과 불확실: 추가 POST·자동 복원 없이 종료합니다. 본체와 ThinQ 앱에서 상태를 확인하세요.", flush=True)
                return 1
            physical_phrase = f"PHYSICAL {command} CONFIRMED" if power_control else PHYSICAL_LOW if command == "LOW" else PHYSICAL_AUTO
            physical = confirm("API 상태 일치와 물리 동작은 별개입니다. 본체 표시·풍량을 직접 확인한 경우만 입력하세요:\n" + physical_phrase + "\n> ") == physical_phrase
            if not physical:
                emit({"event": "physical_confirmation", "value": command, "confirmed": False})
                return 1
            confirmation = await host.call(key, {"op": "physical_confirmation", "value": command, "approved": True})
            confirmed = confirmation.get("physical_confirmed") is True
            emit({"event": "physical_confirmation", "value": command, "confirmed": confirmed,
                  "evidence": "user_attestation"})
            if not confirmed:
                return 1
            if command == "AUTO" or power_control and command == "POWER_ON":
                emit({"event": "trial_complete", "automatic_restore": False})
                return 0
            print("확인 완료. 다음 동작은 별도 승인 전까지 실행하지 않습니다.", flush=True)
        emit({"event": "session_expired", "post_enabled": False})
        return 1
    finally:
        host.revoked = True
        host.session.ctx["enabled"] = False
        host.session.ctx.pop("challenge", None)
        emit({"event": "session_closed", "automatic_restore": False})


def main():
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("error", getpass.GetPassWarning)
    parser = SafeParser(description=__doc__)
    parser.add_argument("--manual", action="store_true", help="GET preflight then individually approved LOW/AUTO")
    parser.add_argument("--power-control", action="store_true", help="GET-only power preflight; add --manual for approved set_power on/off")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("사용자의 대화형 WSL 터미널에서 직접 실행해 주세요.")
    if version("thinqconnect") != "1.0.14":
        raise SystemExit("기존 thinqconnect 1.0.14 환경이 필요합니다.")
    try:
        client_id = load_or_create_client_id(create=False)
    except (OSError, ValueError):
        raise SystemExit("기존 Client ID 파일의 형식·소유자·권한을 확인하세요. 새 ID를 생성하지 않습니다.") from None
    try:
        token = getpass.getpass("제어 시험용 ThinQ PAT (숨김 입력, 저장하지 않음): ").strip()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise SystemExit("PAT 숨김 입력을 완료하지 못했습니다. API 요청 없이 중단합니다.") from None
    if not token:
        raise SystemExit("PAT가 입력되지 않았습니다.")

    async def session_main():
        from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
        from thinqconnect.thinq_api import ThinQApi
        async with ClientSession(trust_env=False, cookie_jar=DummyCookieJar(), timeout=ClientTimeout(total=15)) as http:
            transport = Transport(http, execute=args.manual, power_control=args.power_control)
            sdk = GuardedSdk(ThinQApi(session=transport, access_token=token, country_code="KR", client_id=client_id), transport)
            host = HostSession(Session(sdk, {"enabled": args.manual, "backend": "live_manual" if args.manual else "live_read_only", "power_control": args.power_control}))
            return await run_trial(host, manual=args.manual, power_control=args.power_control)

    try:
        return asyncio.run(session_main())
    except (KeyboardInterrupt, EOFError):
        print("사용자가 종료했습니다. 자동 복원하지 않으므로 실제 기기의 최종 상태를 확인하세요.")
        return 1
    except Exception:
        # No exception text/traceback: SDK exceptions may contain auth headers.
        print("시험을 중단했습니다. 자동 재전송·복원 없이 본체/네트워크/인증을 확인하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
