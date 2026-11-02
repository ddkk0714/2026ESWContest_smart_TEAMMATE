"""Laptop delivery uses mocked SSH only, never a host connection or SDK key."""

import argparse
import importlib.util
import io
import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "pat_session", ROOT / "tools/pat_session.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def args(tmp_path, **changes):
    identity = tmp_path / "identity"
    identity.write_text("test placeholder, not a key")
    identity.chmod(0o600)
    hosts = tmp_path / "hosts"
    hosts.write_text("test placeholder, no host key")
    hosts.chmod(0o600)
    values = dict(
        host="example.invalid",
        user="poc_operator",
        known_hosts=str(hosts),
        identity=str(identity),
        service_user="u0_a90001",
        service_pid=1234,
        ttl=30,
        port=22,
        host_key_verified=True,
        approved_terminal=True,
        fake=True,
        manual=False,
    )
    values.update(changes)
    return argparse.Namespace(**values)


class Sink(io.BytesIO):
    saved = b""

    def close(self):
        self.saved = self.getvalue()
        super().close()


class Process:
    def __init__(self):
        self.stdin = Sink()
        self.terminated = False

    def poll(self):
        return None

    def wait(self, timeout):
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


def test_strict_ssh_no_credential_argument_or_forwarding():
    cmd = tool.ssh_command(
        "example.invalid",
        "poc_operator",
        "/safe/hosts",
        "/safe/id",
        "u0_a90001",
        1234,
        "live_read_only",
        30,
    )
    for setting in (
        "StrictHostKeyChecking=yes",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ClearAllForwardings=yes",
        "BatchMode=yes",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ControlPath=none",
    ):
        assert setting in cmd
    assert "--credential-stdin" in cmd[-1] and not any(
        "password" in x.lower() or "token=" in x.lower() for x in cmd
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"user": "root"},
        {"host": "-bad"},
        {"ttl": 301},
        {"mode": "live_manual"},
        {"service_user": "root"},
    ],
)
def test_bad_ssh_options_fail_closed(changes):
    values = dict(
        host="example.invalid",
        user="poc_operator",
        known_hosts="/safe/hosts",
        identity="/safe/id",
        service_user="u0_a90001",
        service_pid=1234,
        mode="fixture",
        ttl=30,
    )
    values.update(changes)
    with pytest.raises(tool.DeliveryBlocked):
        tool.ssh_command(**values)


def test_unverified_host_blocks_before_process_or_prompt(tmp_path):
    with pytest.raises(tool.DeliveryBlocked):
        tool.run(
            args(tmp_path, host_key_verified=False),
            popen=lambda *a, **kw: pytest.fail("SSH must not start"),
            prompt=lambda _: pytest.fail("no prompt"),
        )


def test_ssh_failure_precedes_hidden_prompt(tmp_path, monkeypatch):
    p = Process()
    monkeypatch.setattr(
        tool,
        "read_reply",
        lambda *a: (_ for _ in ()).throw(
            tool.DeliveryBlocked("ssh_or_receiver_unavailable")
        ),
    )
    with pytest.raises(tool.DeliveryBlocked):
        tool.run(
            args(tmp_path),
            popen=lambda *a, **kw: p,
            prompt=lambda _: pytest.fail("no prompt"),
            emit=lambda _: None,
        )
    assert p.stdin.closed and p.stdin.saved == b""


def test_receiver_denial_precedes_token_input(tmp_path, monkeypatch):
    p = Process()
    monkeypatch.setattr(tool, "read_reply", lambda *a: {"status": "blocked"})
    with pytest.raises(tool.DeliveryBlocked):
        tool.run(
            args(tmp_path),
            popen=lambda *a, **kw: p,
            prompt=lambda _: pytest.fail("no prompt"),
            emit=lambda _: None,
        )
    assert p.stdin.saved == b""


def test_fake_supply_is_stdin_only_and_finally_closed(tmp_path, monkeypatch):
    p = Process()
    sid = "a" * 32
    replies = iter(
        [
            {"status": "supply_required", "mode": "fixture", "session_id": sid},
            {"status": "credential_received", "session_id": sid},
        ]
    )
    captured = {}
    output = []
    monkeypatch.setattr(tool, "read_reply", lambda *a: next(replies))
    monkeypatch.setenv("THINQ_PAT", "must-not-be-forwarded")

    def start(command, **kw):
        captured.update(command=command, **kw)
        return p

    tool.run(args(tmp_path), popen=start, input_fn=lambda _: "", emit=output.append)
    body = p.stdin.saved
    n = struct.unpack("!I", body[:4])[0]
    assert body[4:8] == b"SUP1" and len(body) == 4 + n + 1 and body[-1:] == b"R"
    raw = body[4 : 4 + n]
    size = struct.unpack("!I", raw[36:40])[0]
    fake = raw[48 : 48 + size].decode()
    assert fake not in json.dumps([captured["command"], captured["env"], output])
    assert (
        "THINQ_PAT" not in captured["env"] and captured["stderr"] == subprocess.DEVNULL
    )
    assert (
        p.stdin.closed
        and output[-1] == "credential_delivery_closed_no_device_rollback_implied"
    )


def test_secret_page_erased_and_invalid_header_rejected():
    page = tool.ProtectedPacket()
    try:
        with pytest.raises(tool.DeliveryBlocked):
            page.encode(
                "a" * 32,
                bytearray(b"bad\r\nsecret"),
                bytearray(b"client"),
                bytearray(b"key"),
            )
        assert not page.mem[:].strip(b"\0")
    finally:
        page.close()
    assert page.mem is None


def test_private_key_permissions_enforced(tmp_path):
    a = args(tmp_path)
    Path(a.identity).chmod(0o644)
    with pytest.raises(tool.DeliveryBlocked):
        tool.private_file(a.identity, identity=True)


def test_cli_has_no_secret_argument_and_catches_sensitive_exception(
    monkeypatch, capsys
):
    fake = os.urandom(24).hex()
    monkeypatch.setattr(
        tool.sys,
        "argv",
        [
            "pat_session.py",
            "--host",
            "example.invalid",
            "--user",
            "poc_operator",
            "--identity",
            "/unused",
            "--known-hosts",
            "/unused",
            "--service-user",
            "u0_a90001",
            "--service-pid",
            "1234",
        ],
    )
    monkeypatch.setattr(tool, "run", lambda *a: (_ for _ in ()).throw(ValueError(fake)))
    assert tool.main() == 2
    output = capsys.readouterr()
    assert fake not in output.err + output.out
    assert output.err == "credential_delivery_blocked_or_cancelled\n"
