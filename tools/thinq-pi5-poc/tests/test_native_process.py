"""Host C++ fixture IPC only; no system bus, Cloud, credential or board."""

import json
import os
import subprocess
import sys

import pytest

BINARY = os.environ.get("POC_HOST_BINARY")
pytestmark = pytest.mark.skipif(
    not BINARY, reason="explicitly supply the built host fixture runner"
)


class Runner:
    def __init__(self, python=sys.executable):
        self.p = subprocess.Popen(
            [BINARY, "--stdio-host-fixture", python],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def call(self, **request):
        self.p.stdin.write(json.dumps(request) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close()
        assert self.p.wait(timeout=5) == 0


@pytest.fixture
def runner():
    r = Runner()
    try:
        yield r
    finally:
        r.close()


def test_cpp_python_read_and_approved_fixture_flow(runner):
    r = runner.call(op="read")
    assert r["status"] == "read_ok"
    assert r["fields"]["pm1"]["value"] == 8
    assert not r["post_enabled"]
    assert runner.call(op="enable_fixture_post", enabled=True)["post_enabled"]
    plan = runner.call(op="prepare", value="LOW")
    assert plan["status"] == "approval_required"
    r = runner.call(
        op="execute",
        nonce=plan["nonce"],
        approved=True,
        physical_online_confirmed=True,
        no_external_change=True,
    )
    assert r["api_accepted"] and r["state_match"] and not r["physical_confirmed"]
    assert (
        runner.call(
            op="execute",
            nonce=plan["nonce"],
            approved=True,
            physical_online_confirmed=True,
            no_external_change=True,
        )["status"]
        == "blocked"
    )


def test_live_and_sensitive_rpc_keys_blocked(runner):
    assert (
        runner.call(op="read", backend="live")["error"]
        == "live_credentials_and_policy_unverified"
    )
    r = runner.call(op="read", PAT="synthetic-canary-not-a-token")
    assert r["status"] == "blocked" and "canary" not in json.dumps(r)
    assert runner.call(op="read", junk="x" * 5000)["error"] == "request_too_large"


def test_exec_failure_is_not_success():
    r = Runner("/definitely-nonexistent-poc-test-python")
    try:
        assert r.call(op="read")["status"] == "blocked"
    finally:
        r.close()


def test_worker_timeout_consumes_approval_in_parent(tmp_path):
    flag = tmp_path / "hold"
    wrapper = tmp_path / "python-fixture-wrapper"
    wrapper.write_text(
        '#!/bin/sh\nif [ -e "'
        + str(flag)
        + '" ]; then sleep 90; else exec "'
        + sys.executable
        + '" "$@"; fi\n'
    )
    wrapper.chmod(0o700)
    r = Runner(str(wrapper))
    try:
        assert r.call(op="enable_fixture_post", enabled=True)["post_enabled"]
        plan = r.call(op="prepare", value="LOW")
        assert plan["status"] == "approval_required"
        flag.touch()
        result = r.call(
            op="execute",
            nonce=plan["nonce"],
            approved=True,
            physical_online_confirmed=True,
            no_external_change=True,
        )
        assert result["error"] == "worker_timeout_or_runtime_unavailable"
        flag.unlink()
        assert (
            r.call(
                op="execute",
                nonce=plan["nonce"],
                approved=True,
                physical_online_confirmed=True,
                no_external_change=True,
            )["status"]
            == "blocked"
        )
        assert r.call(op="prepare", value="LOW")["status"] == "blocked"
    finally:
        r.close()
