import asyncio
import copy
import json
from pathlib import Path

import pytest

from poc.session import Session
from poc.worker import FixtureSdk

FIXTURE = json.loads((Path(__file__).parents[1] / "poc/fixture.json").read_text())


def run(coro):
    return asyncio.run(coro)


def make():
    sdk = FixtureSdk(copy.deepcopy(FIXTURE))
    return sdk, Session(sdk)


def call(s, op, **kw):
    return run(s.safe({"op": op, **kw}))["public"]


def prepare(s, value="LOW"):
    call(s, "enable_fixture_post", enabled=True)
    return call(s, "prepare", value=value)


def execute(s, plan, **override):
    return call(
        s,
        "execute",
        nonce=plan["nonce"],
        approved=True,
        physical_online_confirmed=True,
        no_external_change=True,
        **override,
    )


def test_read_only_default_and_parsing():
    sdk, s = make()
    r = call(s, "read")
    assert (
        r["status"] == "read_ok" and not r["post_enabled"] and "post" not in sdk.calls
    )
    assert r["fields"]["pm1"]["value"] == 8 and r["fields"]["pm2"]["valid"]
    assert r["fields"]["monitoring_mode"]["value"] == "ON_WORKING"
    assert r["fields"]["pm2"]["unit"] is None and r["measured_at"] is None
    assert "fixture-only-not-a-device-id" not in json.dumps(r)


def test_low_auto_separate_gets_and_physical_evidence():
    sdk, s = make()
    plan = prepare(s)
    r = execute(s, plan)
    assert r["api_accepted"] and r["state_match"] and not r["physical_confirmed"]
    index = sdk.calls.index("post")
    assert "state" in sdk.calls[index + 1 :]
    assert call(s, "prepare", value="AUTO")["status"] == "blocked"
    call(s, "physical_confirmation", approved=True, value="LOW")
    plan = call(s, "prepare", value="AUTO")
    r = execute(s, plan)
    assert r["state_match"] and sdk.wind == "AUTO" and sdk.calls.count("post") == 2
    assert call(s, "prepare", value="LOW")["status"] == "blocked"


@pytest.mark.parametrize("op", ["prepare", "execute"])
def test_default_post_disabled(op):
    sdk, s = make()
    assert call(s, op, value="LOW")["status"] == "blocked"
    assert "post" not in sdk.calls


@pytest.mark.parametrize(
    "flag", ["approved", "physical_online_confirmed", "no_external_change"]
)
def test_missing_approval_conditions(flag):
    sdk, s = make()
    plan = prepare(s)
    args = {
        "nonce": plan["nonce"],
        "approved": True,
        "physical_online_confirmed": True,
        "no_external_change": True,
    }
    args[flag] = False
    assert call(s, "execute", **args)["status"] == "blocked"
    assert "post" not in sdk.calls


@pytest.mark.parametrize("elapsed", [30, 31, -1])
def test_monotonic_expiry_and_backward_clock(elapsed):
    sdk, s = make()
    s.clock = lambda: 100
    plan = prepare(s)
    s.clock = lambda: 100 + elapsed
    assert execute(s, plan)["status"] == "blocked"
    assert "post" not in sdk.calls


@pytest.mark.parametrize("change", ["power", "job", "wind", "profile"])
def test_changed_precondition_stops_post(change):
    sdk, s = make()
    plan = prepare(s)
    if change == "power":
        sdk.fixture["state"]["operation"]["airPurifierOperationMode"] = "POWER_OFF"
    if change == "job":
        sdk.fixture["state"]["airPurifierJobMode"]["currentJobMode"] = "SLEEP"
    if change == "wind":
        sdk.wind = "HIGH"
    if change == "profile":
        sdk.fixture["profile"]["property"]["airFlow"]["windStrength"]["mode"] = ["r"]
    assert execute(s, plan)["status"] == "blocked"
    assert "post" not in sdk.calls


def test_nonce_replay_and_double_acceptance():
    sdk, s = make()
    plan = prepare(s)
    execute(s, plan)
    assert execute(s, plan)["status"] == "blocked" and sdk.calls.count("post") == 1


@pytest.mark.parametrize("loss,mismatch", [(True, False), (False, True)])
def test_response_loss_or_mismatch_blocks_further_control(loss, mismatch):
    sdk, s = make()
    plan = prepare(s)
    sdk.response_loss = loss
    sdk.mismatch = mismatch
    r = execute(s, plan)
    assert r["status"] == "outcome_unconfirmed" and not r["physical_confirmed"]
    assert r["api_accepted"] is (not loss)
    assert r["state_match"] is (not mismatch)
    assert call(s, "prepare", value="AUTO")["status"] == "blocked"
    assert sdk.calls.count("post") == 1


@pytest.mark.parametrize(
    "status,code",
    [(401, "1302"), (403, "1308"), (429, "1306"), (503, None), (None, "1222")],
)
def test_safe_api_errors(status, code):
    sdk, s = make()
    sdk.fail = (status, code)
    r = call(s, "read")
    assert (
        r["status"] == "api_error" and r["http_status"] == status and r["code"] == code
    )
    assert "post" not in sdk.calls


def test_partial_field_failure_preserves_other_values():
    sdk, s = make()
    sdk.fixture["state"]["airQualitySensor"]["PM1"] = True
    r = call(s, "read")
    assert not r["fields"]["pm1"]["valid"] and r["fields"]["pm2"]["valid"]
    assert r["fields"]["filter_remain_percent"]["value"] == 96


def test_multiple_targets_block_without_printing_identifiers():
    sdk, s = make()
    original = sdk.async_get_device_list

    async def multiple(**kw):
        devices = await original(**kw)
        return devices + devices

    sdk.async_get_device_list = multiple
    r = call(s, "read")
    assert r["status"] == "blocked" and "deviceId" not in json.dumps(r)
    assert "post" not in sdk.calls


def test_restore_never_runs_without_this_session_low():
    sdk, s = make()
    sdk.wind = "LOW"
    assert prepare(s, "AUTO")["status"] == "blocked"
    assert "post" not in sdk.calls


def test_sensitive_input_keys_rejected_without_echo():
    sdk, s = make()
    r = call(s, "read", PAT="fixture-private-canary")
    assert r["status"] == "blocked" and "canary" not in json.dumps(r)
    assert sdk.calls == []


def test_live_cannot_be_armed_using_fixture_switch():
    sdk, s = make()
    r = call(s, "enable_fixture_post", backend="live", enabled=True)
    assert r["status"] == "blocked" and not r["post_enabled"]


def test_network_timeout_has_safe_distinct_category():
    from poc.session import SafeApiFailure

    sdk, session = make()

    async def timeout(**kwargs):
        raise SafeApiFailure(category="network_timeout")

    sdk.async_get_device_list = timeout
    result = call(session, "read")
    assert result["category"] == "network_timeout"
    assert "post" not in sdk.calls


@pytest.mark.parametrize(
    "setting,value", [("power", "POWER_OFF"), ("job", "SLEEP"), ("wind", "HIGH")]
)
def test_nonbaseline_state_is_readable_but_not_controllable(setting, value):
    sdk, session = make()
    if setting == "power":
        sdk.fixture["state"]["operation"]["airPurifierOperationMode"] = value
    elif setting == "job":
        sdk.fixture["state"]["airPurifierJobMode"]["currentJobMode"] = value
    else:
        sdk.wind = value
    result = call(session, "read")
    assert (
        result["state"][
            {"power": "power", "job": "job", "wind": "windStrength"}[setting]
        ]
        == value
    )
    assert prepare(session)["status"] == "blocked"
    assert "post" not in sdk.calls


def test_target_is_pinned_across_native_rpc_children():
    sdk, first = make()
    plan = prepare(first)
    other = FixtureSdk(copy.deepcopy(FIXTURE))
    other.device_id = "fixture-other-device"
    second = Session(other, first.ctx)
    result = execute(second, plan)
    assert result["status"] == "blocked"
    assert "post" not in other.calls
    assert "fixture-other-device" not in json.dumps(result)


def test_fixture_authorization_cannot_move_to_live_backend():
    sdk, first = make()
    prepare(first)
    other = FixtureSdk(copy.deepcopy(FIXTURE))
    second = Session(other, first.ctx)
    result = call(second, "read", backend="live")
    assert result["status"] == "blocked"
    assert not result["post_enabled"]
    assert other.calls == []
    assert "challenge" not in second.ctx


@pytest.mark.parametrize(
    "op", ["enable_fixture_post", "prepare", "execute", "physical_confirmation"]
)
def test_live_read_only_cannot_inherit_control_permission(op):
    sdk = FixtureSdk(copy.deepcopy(FIXTURE))
    session = Session(sdk, {"backend": "live_read_only", "enabled": True})
    result = call(session, op, backend="live_read_only", value="LOW", approved=True)
    assert result["status"] == "blocked"
    assert sdk.calls == []
    assert result["post_enabled"] is False


def test_live_manual_mock_preserves_nonce_readback_and_no_resend():
    sdk = FixtureSdk(copy.deepcopy(FIXTURE))
    session = Session(sdk, {"backend": "live_manual", "enabled": True})
    plan = call(session, "prepare", backend="live_manual", value="LOW")
    sdk.response_loss = True
    request = dict(
        backend="live_manual",
        nonce=plan["nonce"],
        approved=True,
        physical_online_confirmed=True,
        no_external_change=True,
    )
    result = call(session, "execute", **request)
    assert result["status"] == "outcome_unconfirmed"
    assert result["api_accepted"] is False
    assert result["state_match"] is True
    assert sdk.calls.count("post") == 1
    assert "state" in sdk.calls[sdk.calls.index("post") + 1 :]
    assert call(session, "execute", **request)["status"] == "blocked"
    assert sdk.calls.count("post") == 1
