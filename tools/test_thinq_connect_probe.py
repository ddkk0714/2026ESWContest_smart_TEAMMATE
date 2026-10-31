import asyncio
from pathlib import Path

from tools.thinq_connect.probe import FileMockApi, inspect_device, list_devices, main


PAYLOAD = {
    "devices": [
        {
            "deviceId": "01234567-89ab-cdef",
            "deviceInfo": {
                "deviceType": "DEVICE_AIR_CONDITIONER",
                "alias": "study air conditioner",
                "modelName": "MODEL-A",
                "reportable": True,
                "controllable": True,
            },
        },
        {"deviceId": "short", "deviceInfo": {"deviceType": "DEVICE_HUMIDIFIER"}},
    ],
    "profiles": {
        "01234567-89ab-cdef": {
            "operation": {"properties": {"airConOperationMode": {"writable": True}}}
        }
    },
    "states": {"01234567-89ab-cdef": {"operation": {"airConOperationMode": "POWER_ON"}}},
}


def test_device_list_masks_ids_and_preserves_capabilities():
    result = asyncio.run(list_devices(FileMockApi(PAYLOAD)))
    assert result == [
        {
            "device_id": "0123…cdef",
            "device_type": "DEVICE_AIR_CONDITIONER",
            "alias": "study air conditioner",
            "model_name": "MODEL-A",
            "reportable": True,
            "controllable": True,
        },
        {
            "device_id": "***",
            "device_type": "DEVICE_HUMIDIFIER",
            "alias": None,
            "model_name": None,
            "reportable": None,
            "controllable": None,
        },
    ]


def test_device_list_can_show_full_id_for_local_selection():
    result = asyncio.run(list_devices(FileMockApi(PAYLOAD), show_device_id=True))
    assert result[0]["device_id"] == "01234567-89ab-cdef"


def test_profile_and_state_are_read_only_queries():
    result = asyncio.run(inspect_device(FileMockApi(PAYLOAD), "01234567-89ab-cdef"))
    assert result["profile"]["operation"]["properties"]["airConOperationMode"]["writable"]
    assert result["state"]["operation"]["airConOperationMode"] == "POWER_ON"


def test_invalid_device_list_shape_is_rejected():
    try:
        asyncio.run(list_devices(FileMockApi({"devices": {"unexpected": True}})))
    except ValueError as error:
        assert "list of objects" in str(error)
    else:
        raise AssertionError("invalid response was accepted")


def test_mock_cli_needs_no_pat(tmp_path: Path, capsys):
    fixture = tmp_path / "devices.json"
    fixture.write_text('{"devices": []}', encoding="utf-8")
    assert main(["--mock-file", str(fixture), "list"]) == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_cli_never_renders_sdk_error_containing_a_token(monkeypatch, capsys):
    from tools.thinq_connect import probe
    async def fail(args):
        raise Exception('Authorization: Bearer mock-sensitive-marker device-id private@example.test')
    monkeypatch.setattr(probe, '_run', fail)
    assert probe.main(['list']) == 1
    captured = capsys.readouterr()
    assert 'mock-sensitive-marker' not in captured.err and not captured.out
    assert 'details redacted' in captured.err


def test_cli_masks_identifiers_aliases_and_credentials_recursively():
    from tools.thinq_connect.probe import public_output
    value = public_output({'device_id': 'mock-device-12345678', 'alias': 'private desk',
        'profile': {'serialNumber': 'serial', 'access_token': 'secret', 'operation': 'on'}})
    assert value['alias'] == '[redacted]'
    assert value['device_id'] != 'mock-device-12345678'
    assert value['profile'] == {'serialNumber': '[redacted]', 'access_token': '[redacted]', 'operation': 'on'}
