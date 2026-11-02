"""Fake credentials only. Real Linux local IPC; no SSH host or HTTPS calls."""

import importlib.util
import json
import os
import socket
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HOST = os.environ.get("POC_AUTH_HOST_BINARY")
SERVICE = os.environ.get("POC_SECURITY_SERVICE_BINARY")


def load_policy():
    spec = importlib.util.spec_from_file_location(
        "policy", ROOT / "native/configure_bindings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Host:
    def __init__(self, op_uid=None):
        args = [HOST] + ([] if op_uid is None else [str(op_uid)])
        self.p = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.ready = json.loads(self.p.stdout.readline())

    def call(self, op, **kwargs):
        self.p.stdin.write(json.dumps({"op": op, **kwargs}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close()
        assert self.p.wait(timeout=5) == 0
        assert not self.p.stderr.read()


@pytest.fixture
def host():
    if not HOST:
        pytest.skip("explicit host security harness required")
    h = Host()
    try:
        yield h
    finally:
        h.close()


def connect(host, ttl=60, mode="live_read_only"):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    s.connect("\0" + host.ready["endpoint"])
    try:
        s.send(json.dumps({"op": "begin", "mode": mode, "ttl": ttl}).encode())
        ready = json.loads(s.recv(8192))
    except (BrokenPipeError, ConnectionResetError):
        # Peer rejects before reading an unauthenticated body. Linux may reset
        # when closing with that unread body; either way no session is granted.
        ready = {"error": "connection_rejected"}
    return s, ready


def packet(sid, token):
    client = os.urandom(16).hex().encode()
    key = os.urandom(24).hex().encode()
    return (
        b"SUP1"
        + sid.encode()
        + struct.pack("!III", len(token), len(client), len(key))
        + token
        + client
        + key
    )


def inject(s, ready, token):
    s.send(packet(ready["session_id"], token))
    return json.loads(s.recv(8192))


def test_authenticated_delivery_and_no_public_token(host):
    token = os.urandom(32).hex().encode()
    s, ready = connect(host)
    assert ready["status"] == "supply_required"
    assert inject(s, ready, token)["status"] == "credential_received"
    status = host.call("status")
    assert status["mode"] == "live_read_only" and not status["post_enabled"]
    result = host.call("get", session_id=ready["session_id"], mode="live_read_only")
    assert "response" in result
    assert token.decode() not in json.dumps([ready, status, result])
    s.close()


def test_kernel_uid_rejection_cannot_be_spoofed():
    if not HOST:
        pytest.skip("explicit harness required")
    h = Host(os.getuid() + 10)
    try:
        s, ready = connect(h)
        assert ready["error"] in ("unauthorized_operator", "connection_rejected")
        s.close()
        assert h.call("status")["status"] == "credential_unavailable"
    finally:
        h.close()


def test_duplicate_and_concurrent_supply(host):
    token = os.urandom(32).hex().encode()
    s, ready = connect(host)
    assert inject(s, ready, token)["status"] == "credential_received"
    assert (
        inject(s, ready, os.urandom(32).hex().encode())["error"] == "duplicate_supply"
    )
    second, busy = connect(host)
    assert busy["error"] in ("session_busy", "connection_rejected")
    second.close()
    assert (
        host.call("get", session_id=ready["session_id"], mode="live_read_only").get(
            "response"
        )
        == {}
    )
    s.close()


def test_no_credentials_and_bad_ui_caller_rejected(host):
    assert (
        host.call("get", session_id="missing", mode="live_read_only")["error"]
        == "authorization_denied"
    )
    s, ready = connect(host)
    inject(s, ready, os.urandom(32).hex().encode())
    r = host.call(
        "get",
        session_id=ready["session_id"],
        mode="live_read_only",
        caller_uid=os.getuid(),
    )
    assert r["error"] == "authorization_denied"
    s.close()


def test_expiry_and_retired_credential_reinjection(host):
    token = os.urandom(32).hex().encode()
    s, ready = connect(host, ttl=10)
    inject(s, ready, token)
    time.sleep(10.2)
    assert (
        host.call("get", session_id=ready["session_id"], mode="live_read_only")["error"]
        == "authorization_denied"
    )
    s.close()
    s, new = connect(host)
    r = inject(s, new, token)
    assert r["error"] == "credential_retired_in_this_process"
    s.close()


def test_explicit_revoke_and_ssh_disconnect_close_lease(host):
    s, ready = connect(host)
    inject(s, ready, os.urandom(32).hex().encode())
    host.call("revoke")
    assert (
        host.call("get", session_id=ready["session_id"], mode="live_read_only")["error"]
        == "authorization_denied"
    )
    s.close()
    time.sleep(0.1)
    s, ready = connect(host)
    inject(s, ready, os.urandom(32).hex().encode())
    s.close()
    time.sleep(0.1)
    assert host.call("status")["status"] == "credential_unavailable"


def test_restart_does_not_retain_credentials_or_old_envelope():
    if not HOST:
        pytest.skip("explicit harness required")
    h = Host()
    s, ready = connect(h)
    token = os.urandom(32).hex().encode()
    inject(s, ready, token)
    s.close()
    h.close()
    h = Host()
    try:
        assert h.call("status")["status"] == "credential_unavailable"
        s, new = connect(h)
        s.send(packet(ready["session_id"], token))
        r = json.loads(s.recv(8192))
        assert r["error"] == "invalid_supply_packet"
        s.close()
    finally:
        h.close()


def test_read_only_is_not_manual_and_unapproved_post_blocked(host):
    s, ready = connect(host)
    inject(s, ready, os.urandom(32).hex().encode())
    r = host.call("post", session_id=ready["session_id"], mode="live_read_only")
    assert r["error"] == "read_only_post_blocked" and r["post_calls"] == 0
    s.send(
        json.dumps(
            {"op": "arm_manual", "session_id": ready["session_id"], "approved": False}
        ).encode()
    )
    assert json.loads(s.recv(8192))["status"] == "blocked"
    s.send(
        json.dumps(
            {"op": "arm_manual", "session_id": ready["session_id"], "approved": True}
        ).encode()
    )
    assert json.loads(s.recv(8192))["status"] == "manual_session_armed"
    r = host.call("post_unapproved", session_id=ready["session_id"], mode="live_manual")
    assert r["error"] == "post_not_approved" and r["post_calls"] == 0
    r = host.call("post", session_id=ready["session_id"], mode="live_manual")
    assert r["post_calls"] == 1
    s.close()


def test_revoke_cancels_inflight_and_no_retry(host):
    s, ready = connect(host)
    inject(s, ready, os.urandom(32).hex().encode())
    host.p.stdin.write(
        json.dumps(
            {"op": "slow", "session_id": ready["session_id"], "mode": "live_read_only"}
        )
        + "\n"
    )
    host.p.stdin.flush()
    time.sleep(0.15)
    s.close()
    r = json.loads(host.p.stdout.readline())
    assert r["error"] == "authorization_expired_or_revoked"
    assert host.call("status")["status"] == "credential_unavailable"


def test_mapped_secret_is_not_inherited_by_python_fork(host):
    s, ready = connect(host)
    inject(s, ready, os.urandom(32).hex().encode())
    assert host.call("dontfork", session_id=ready["session_id"], mode="live_read_only")[
        "mapped_secret_not_inherited"
    ]
    s.close()


def test_helper_rejects_wrong_server_pid_before_reading_secret(host):
    if not SERVICE:
        pytest.skip("explicit helper binary required")
    args = [
        SERVICE,
        "--credential-stdin",
        "--host-fixture",
        host.ready["endpoint"],
        str(os.getuid()),
        str(host.ready["pid"] + 100),
        "fixture",
        "60",
    ]
    p = subprocess.run(args, input=b"", capture_output=True, timeout=5)
    assert p.returncode != 0 and b"supply_required" not in p.stdout
    assert p.stderr == b"credential_delivery_blocked\n"


def test_policy_has_exact_roles_and_no_token_method():
    config = {
        "service_user": "u0_a90001",
        "ui_user": "u0_a90002",
        "operator_user": "poc_operator",
        "reviewed": True,
        "read_only_verified": False,
        "manual_verified": False,
    }
    header, xml = load_policy().render(config)
    root = ET.fromstring(xml)
    assert "kBindingsConfigured=true" in header
    allows = root.findall(".//allow")
    assert not any(
        x.get("own") == "*" or x.get("send_destination") == "*" for x in allows
    )
    own = [x for x in root.findall("policy") if x.find("allow[@own]") is not None]
    assert len(own) == 1 and own[0].get("user") == config["service_user"]
    assert {x.get("send_member") for x in allows if x.get("send_destination")} == {
        "Exchange",
        "AuthStatus",
    }
    assert "perm_thinq" not in xml and "root" not in xml and "Supply" not in xml


@pytest.mark.parametrize(
    "change",
    [
        {"operator_user": "root"},
        {"ui_user": "u0_a90001"},
        {"operator_user": "u0_a90001"},
        {"service_user": "*"},
        {"reviewed": False},
        {"manual_verified": True},
    ],
)
def test_bad_policy_bindings_rejected(change):
    c = {
        "service_user": "u0_a90001",
        "ui_user": "u0_a90002",
        "operator_user": "poc_operator",
        "reviewed": True,
        "read_only_verified": False,
        "manual_verified": False,
    }
    c.update(change)
    with pytest.raises(ValueError):
        load_policy().render(c)


def test_real_helper_binary_stdin_flow_never_echoes_fake_secret(host):
    if not SERVICE:
        pytest.skip("explicit helper required")
    command = [
        SERVICE,
        "--credential-stdin",
        "--host-fixture",
        host.ready["endpoint"],
        str(os.getuid()),
        str(host.ready["pid"]),
        "fixture",
        "60",
    ]
    p = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        ready = json.loads(p.stdout.readline())
        token = os.urandom(24).hex().encode()
        body = packet(ready["session_id"], token)
        p.stdin.write(struct.pack("!I", len(body)) + body)
        p.stdin.flush()
        ack = json.loads(p.stdout.readline())
        assert ack["status"] == "credential_received"
        assert token.decode() not in json.dumps([ready, ack, host.call("status")])
        p.stdin.write(b"R")
        p.stdin.close()
        assert p.wait(timeout=5) == 0
        assert p.stderr.read() == b""
        assert token not in p.stdout.read()
        time.sleep(0.1)
        assert host.call("status")["status"] == "credential_unavailable"
    finally:
        if p.poll() is None:
            p.terminate()
            p.wait(timeout=5)


@pytest.mark.parametrize("token", [b"", b"short", b"test\ncredential", b"x" * 4097])
def test_bad_credential_bytes_do_not_activate_session(host, token):
    s, ready = connect(host)
    try:
        r = inject(s, ready, token)
        assert r["status"] == "blocked"
        assert host.call("status")["status"] == "awaiting_credential"
        assert (
            host.call("get", session_id=ready["session_id"], mode="live_read_only")[
                "error"
            ]
            == "authorization_denied"
        )
    finally:
        s.close()


def test_native_transport_source_uses_only_reviewed_sdk_headers_and_tls():
    # Source-contract check, NOT an observed HTTP request or real TLS handshake.
    import re

    body = (ROOT / "native/src/https.cpp").read_text()
    headers = set(re.findall(r'add\("([A-Za-z-]+):', body))
    assert headers == {
        "Authorization",
        "x-country",
        "x-message-id",
        "x-client-id",
        "x-api-key",
        "x-service-phase",
        "Content-Type",
        "x-conditional-control",
    }
    assert "CURLOPT_SSL_VERIFYPEER,1L" in body
    assert "CURLOPT_SSL_VERIFYHOST,2L" in body
    assert "CURLOPT_FOLLOWLOCATION,0L" in body
    assert "CURLOPT_VERBOSE" not in body and "CURLOPT_DEBUGFUNCTION" not in body
    assert "https://api-kic.lgthinq.com" in body
