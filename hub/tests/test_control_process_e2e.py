"""Headless version of the independent Hub/Gateway test; Flutter is explicit."""
import pytest
pytest.importorskip('amqtt')
pytest.importorskip('paho.mqtt.client')
from control_process_e2e import run_scenario


def test_independent_hub_and_gateway_processes_use_real_mqtt(tmp_path):
    result = run_scenario(tmp_path)
    assert result['result'] == 'passed' and result['mode'] == 'mock'
    assert result['hub_pid'] != result['gateway_pid']
    assert result['commands'] == result['adapter_calls'] == 1
    assert result['state_confirmed'] and result['fsm_state'] == 'MONITOR'
    assert not result['flutter_process'] and not result['hardware']
