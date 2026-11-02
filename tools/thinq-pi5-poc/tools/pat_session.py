"""Laptop-only SSH stdin provisioner. No credential argv/env/files or HTTP.

Real input requires an explicitly approved separate terminal. Tests inject Fake
Token/SSH primitives. This module never imports the SDK or contacts a host at import.
"""

import argparse
import ctypes
import getpass
import json
import mmap
import os
import re
import resource
import selectors
import shlex
import struct
import subprocess
import sys
import time
import uuid
import warnings
from importlib.metadata import version
from pathlib import Path

REMOTE = "/data/share/usr/atlas/services/com.deskmate.thinqpoc1/deskmate_thinq_poc"
MANUAL_APPROVAL = "ARM MANUAL LOW AUTO FOR THIS SESSION"


class DeliveryBlocked(Exception):
    pass


class ProtectedPacket:
    def __init__(self):
        self.mem = mmap.mmap(-1, 8192, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        self.address = ctypes.addressof(ctypes.c_char.from_buffer(self.mem))
        self.libc = ctypes.CDLL(None, use_errno=True)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if self.libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE=0
            self.close()
            raise DeliveryBlocked("memory_protection_failed")
        self.libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if self.libc.mlock(self.address, 8192) != 0:
            self.close()
            raise DeliveryBlocked("memory_lock_failed")
        try:
            self.mem.madvise(mmap.MADV_DONTDUMP)
            self.mem.madvise(mmap.MADV_DONTFORK)
        except Exception:
            self.close()
            raise DeliveryBlocked("memory_protection_failed") from None

    def close(self):
        if getattr(self, "mem", None) is not None:
            self.mem.seek(0)
            self.mem.write(b"\0" * 8192)
            self.libc.munlock(self.address, 8192)
            self.mem.close()
            self.mem = None

    def encode(self, sid, pat, client_id, api_key):
        if not re.fullmatch(r"[a-f0-9]{32}", sid):
            raise DeliveryBlocked("invalid_session")
        fields = [pat, client_id, api_key]
        maxima = [4096, 128, 1024]
        if len(pat) < 8 or any(
            not x or len(x) > limit or any(v <= 32 or v >= 127 for v in x)
            for x, limit in zip(fields, maxima)
        ):
            raise DeliveryBlocked("invalid_credential_format")
        self.mem.seek(0)
        self.mem.write(
            b"SUP1"
            + sid.encode("ascii")
            + struct.pack("!III", *(len(x) for x in fields))
        )
        for field in fields:
            self.mem.write(field)
        return self.mem.tell()


def private_file(value, identity=False):
    path = Path(value).expanduser().resolve(strict=True)
    stat = path.stat()
    if (
        not path.is_file()
        or stat.st_uid not in (os.getuid(), 0)
        or stat.st_mode & (0o077 if identity else 0o022)
    ):
        raise DeliveryBlocked("unsafe_ssh_file_permissions")
    if any(c.isspace() for c in str(path)) or "%" in str(path):
        raise DeliveryBlocked("unsupported_ssh_path")
    return str(path)


def ssh_command(
    host, user, known_hosts, identity, service_user, service_pid, mode, ttl, port=22
):
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host) or host.startswith("-"):
        raise DeliveryBlocked("invalid_host")
    if user == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise DeliveryBlocked("nonroot_operator_required")
    if (
        not re.fullmatch(r"u0_a[0-9]+", service_user)
        or service_pid < 2
        or not 10 <= ttl <= 300
        or not 1 <= port <= 65535
    ):
        raise DeliveryBlocked("unverified_service_identity")
    if mode not in ("fixture", "live_read_only"):
        raise DeliveryBlocked("invalid_mode")
    command = ["ssh", "-F", "/dev/null", "-T", "-p", str(port), "-i", identity]
    options = [
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=" + known_hosts,
        "GlobalKnownHostsFile=/dev/null",
        "UpdateHostKeys=no",
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ClearAllForwardings=yes",
        "PermitLocalCommand=no",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ControlMaster=no",
        "ControlPath=none",
        "ControlPersist=no",
        "ConnectionAttempts=1",
        "ConnectTimeout=10",
        "ServerAliveInterval=5",
        "ServerAliveCountMax=1",
        "LogLevel=ERROR",
    ]
    for option in options:
        command += ["-o", option]
    command += [
        "-l",
        user,
        "--",
        host,
        shlex.join(
            [
                REMOTE,
                "--credential-stdin",
                service_user,
                str(service_pid),
                mode,
                str(ttl),
            ]
        ),
    ]
    return command


def read_reply(process, timeout=15):
    deadline = time.monotonic() + timeout
    line = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline and len(line) <= 4096:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            part = os.read(process.stdout.fileno(), 1)
            if not part:
                break
            line.extend(part)
            if part == b"\n":
                break
    if not line.endswith(b"\n") or len(line) > 4096:
        raise DeliveryBlocked("ssh_or_receiver_unavailable")
    try:
        value = json.loads(line)
    except Exception:
        raise DeliveryBlocked("invalid_receiver_reply") from None
    if not isinstance(value, dict):
        raise DeliveryBlocked("invalid_receiver_reply")
    return value


def sdk_header_key():
    if version("thinqconnect") != "1.0.14":
        raise DeliveryBlocked("reviewed_sdk_required")
    from thinqconnect.thinq_api import API_KEY

    return bytearray(API_KEY, "ascii")


def supply(process, packet, sid, pat, client, key):
    n = packet.encode(sid, pat, client, key)
    if process.poll() is not None:
        raise DeliveryBlocked("ssh_closed_before_supply")
    process.stdin.write(struct.pack("!I", n))
    view = memoryview(packet.mem)[:n]
    try:
        process.stdin.write(view)
        process.stdin.flush()
    finally:
        view.release()
    result = read_reply(process)
    if result.get("status") != "credential_received" or result.get("session_id") != sid:
        raise DeliveryBlocked("credential_not_accepted")
    return result


def run(
    args, *, popen=subprocess.Popen, prompt=getpass.getpass, input_fn=input, emit=print
):
    # No subprocess is spawned after PAT input: ssh cannot inherit a forked PAT heap.
    known = private_file(args.known_hosts)
    identity = private_file(args.identity, identity=True)
    if not args.host_key_verified:
        raise DeliveryBlocked("verify_host_fingerprint_first")
    if not args.fake and (not args.approved_terminal or not sys.stdin.isatty()):
        raise DeliveryBlocked("approved_interactive_terminal_required")
    mode = "fixture" if args.fake else "live_read_only"
    packet = ProtectedPacket()
    process = None
    pat = key = client = None
    try:
        # Resolved before prompt; never printed or read from environment.
        key = (
            bytearray(os.urandom(24).hex(), "ascii") if args.fake else sdk_header_key()
        )
        client = bytearray(str(uuid.uuid4()), "ascii")
        command = ssh_command(
            args.host,
            args.user,
            known,
            identity,
            args.service_user,
            args.service_pid,
            mode,
            args.ttl,
            args.port,
        )
        env = {
            k: os.environ[k]
            for k in ("PATH", "LANG", "SSH_AUTH_SOCK")
            if k in os.environ
        }
        process = popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            bufsize=0,
        )
        ready = read_reply(process)
        sid = ready.get("session_id", "")
        if (
            ready.get("status") != "supply_required"
            or ready.get("mode") != mode
            or not re.fullmatch(r"[a-f0-9]{32}", sid)
        ):
            raise DeliveryBlocked("receiver_not_authorized_or_ready")
        if args.fake:
            pat = bytearray(os.urandom(24).hex(), "ascii")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = prompt("ThinQ PAT (hidden; memory only): ")
            pat = bytearray(value, "ascii")
            value = None  # Python immutable copies cannot be rigorously erased; documented limitation.
        supply(process, packet, sid, pat, client, key)
        for secret in (pat, key, client):
            secret[:] = b"\0" * len(secret)
        packet.mem.seek(0)
        packet.mem.write(b"\0" * 8192)
        emit(
            "credential_session_active_read_only"
            if not args.fake
            else "fixture_credential_session_active"
        )
        if args.manual:
            if (
                args.fake
                or input_fn(
                    "Type " + MANUAL_APPROVAL + " only with separate approval: "
                )
                != MANUAL_APPROVAL
            ):
                raise DeliveryBlocked("manual_not_approved")
            process.stdin.write(b"M")
            process.stdin.flush()
            if read_reply(process).get("status") != "manual_session_armed":
                raise DeliveryBlocked("manual_platform_not_authorized")
            emit("manual_session_armed_commands_still_require_UI_approval")
        input_fn("Keep this terminal open. Press Enter to revoke this session: ")
        process.stdin.write(b"R")
        process.stdin.flush()
    finally:
        for secret in (pat, key, client):
            if secret is not None:
                secret[:] = b"\0" * len(secret)
        packet.close()
        if process is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        emit("credential_delivery_closed_no_device_rollback_implied")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-hosts", "identity", "service-user"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--service-pid", type=int, required=True)
    parser.add_argument("--ttl", type=int, default=120)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--host-key-verified", action="store_true")
    parser.add_argument("--approved-terminal", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except (Exception, KeyboardInterrupt):
        # No raw exception, argv, remote stderr, PAT substring or traceback.
        print("credential_delivery_blocked_or_cancelled", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
