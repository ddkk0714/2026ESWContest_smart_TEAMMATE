"""Mock-only entrypoint validation; no boards, accounts or LAN access."""
import asyncio
import copy
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
import yaml

from deskmate_hub.control.gateway_service import (
    ConfigurationError, DEFAULT_CONFIG, MockGatewayService, build_mock_devices,
    load_settings, main, prepare_journal,
)


def configuration(tmp_path, **changes):
    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text())
    cfg.update(changes)
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg))
    return path


def settings(tmp_path, **changes):
    path = configuration(tmp_path, **changes)
    return load_settings(['--config', str(path), '--mock', '--enable',
                          '--journal', str(tmp_path / 'private' / 'commands.db')])


def test_no_arguments_or_enable_without_explicit_mock_cannot_start(tmp_path):
    assert main([]) == 2
    assert main(['--enable']) == 2
    with pytest.raises(ConfigurationError):
        load_settings(['--mock', '--journal', str(tmp_path / 'commands.db')])
    assert not (tmp_path / 'commands.db').exists()


def test_mock_config_or_cli_opt_in_and_connection_overrides(tmp_path):
    cfg = configuration(tmp_path, enabled=True)
    result = load_settings(['--config', str(cfg), '--mock', '--broker-port', '19999',
                            '--journal', str(tmp_path / 'private' / 'commands.db')])
    assert (result.host, result.port) == ('127.0.0.1', 19999)
    registry, adapter = build_mock_devices(result)
    assert set(registry.devices) == {'mock_lamp', 'mock_vent', 'mock_cooling'}
    assert adapter.mode == 'mock' and not adapter.calls
    assert not result.journal.exists()


@pytest.mark.parametrize('change', [
    {'mode': 'live'}, {'automatic_control': True}, {'enabled': 'true'},
    {'journal': 'relative.db'}, {'pat': 'never-log-this-secret'},
    {'devices': [{'target_id': 'desk_lamp'}]},
    {'broker': {'profile': 'localhost', 'host': '192.0.2.1', 'port': 1883}},
    {'broker': {'profile': 'pi4', 'host': '192.0.2.1', 'port': 1883}},
    {'broker': {'profile': 'localhost', 'host': 'user:secret@localhost', 'port': 1883}},
    {'broker': {'profile': 'localhost', 'host': 'localhost', 'port': True}},
])
def test_configuration_fails_closed_and_redacts_input(tmp_path, change, caplog):
    path = configuration(tmp_path, **change)
    args = ['--config', str(path), '--mock', '--enable']
    if 'journal' not in change:
        args += ['--journal', str(tmp_path / 'private' / 'commands.db')]
    assert main(args) == 2
    assert 'never-log-this-secret' not in caplog.text
    assert 'user:secret' not in caplog.text
    assert not (tmp_path / 'private').exists()


def test_parser_does_not_echo_unknown_credentials(capsys):
    with pytest.raises(SystemExit) as caught:
        main(['--password', 'sensitive-fixture-value'])
    assert caught.value.code == 2
    assert 'sensitive-fixture-value' not in capsys.readouterr().err


def test_remote_profile_requires_env_auth_and_verified_tls(tmp_path, monkeypatch):
    monkeypatch.setenv('MOCK_MQTT_USER', 'synthetic-user')
    monkeypatch.setenv('MOCK_MQTT_PASSWORD', 'synthetic-password')
    result = settings(tmp_path, broker={'profile': 'pi4', 'host': '192.0.2.1', 'port': 8883,
        'username_env': 'MOCK_MQTT_USER', 'password_env': 'MOCK_MQTT_PASSWORD', 'tls_ca': '/not-contacted/ca.pem'})
    assert result.port == 8883  # parse only; never connect outside loopback
    monkeypatch.delenv('MOCK_MQTT_PASSWORD')
    with pytest.raises(ConfigurationError):
        settings(tmp_path, broker={'profile': 'pi4', 'host': '192.0.2.1', 'port': 8883,
            'username_env': 'MOCK_MQTT_USER', 'password_env': 'MOCK_MQTT_PASSWORD', 'tls_ca': '/ca.pem'})


def test_shared_symlink_foreign_or_insecure_journal_is_not_silently_chmodded(tmp_path):
    path = tmp_path / 'private' / 'commands.db'
    prepare_journal(path)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.write_text('data'); path.chmod(0o644)
    with pytest.raises(ConfigurationError): prepare_journal(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    path.unlink(); path.symlink_to(tmp_path / 'other.db')
    with pytest.raises(ConfigurationError): prepare_journal(path)
    path.unlink(); path.parent.chmod(0o755)
    with pytest.raises(ConfigurationError): prepare_journal(path)


def test_runner_ignores_live_unowned_and_malformed_commands_and_cleans_resources(tmp_path):
    service = MockGatewayService(settings(tmp_path))
    try:
        import json
        for data in (None, [], {'target_id': []}, {'target_id': 'desk_lamp', 'execution_mode': 'mock'},
                     {'target_id': 'mock_lamp'}, {'target_id': 'mock_lamp', 'execution_mode': 'live'}):
            message = SimpleNamespace(topic='deskmate/control/cmd', payload=json.dumps({'data': data}).encode(), retain=False)
            service.client.on_message(service.client, None, message)
        asyncio.run(service.gateway.pump())
        assert not service.gateway.results
        assert not service.gateway.adapters['mock'].calls
        assert service.gateway.mode == 'mock' and not service.gateway.automatic_control
    finally:
        service.gateway.close()
    # Lock must be releasable; no network loop was started in this test.
    replacement = MockGatewayService(settings(tmp_path))
    replacement.gateway.close()


def test_stop_before_connection_closes_journal_and_client(tmp_path):
    service = MockGatewayService(settings(tmp_path))
    async def stop_now():
        stop = asyncio.Event(); stop.set()
        await service.run(stop)
    asyncio.run(stop_now())
    assert service.gateway._closed and service.gateway._journal is None
    assert not service.ready.is_set()


def test_result_outbox_reconnect_replays_results_without_executing(tmp_path):
    service = MockGatewayService(settings(tmp_path))
    try:
        sent = []
        envelope = {'node': 'device-gateway-mock', 'data': {'command_id': 'synthetic', 'status': 'succeeded'}}
        service.queue_result('deskmate/control/result', envelope)
        service.flush_results()
        assert 'synthetic' in service.outbox
        service.client.publish = lambda topic, payload, **kw: (sent.append((topic, payload, kw)) or SimpleNamespace(rc=0))
        service.gateway.results['synthetic'] = copy.deepcopy(envelope)
        service.ready.set(); service.resync.set(); service.flush_results()
        assert not service.outbox and len(sent) == 1
        assert sent[0][2] == {'qos': 1, 'retain': False}
        service.resync.set(); service.flush_results()
        assert len(sent) == 2 and not service.gateway.adapters['mock'].calls
    finally:
        service.gateway.close()


def test_unexpected_library_failure_is_redacted(tmp_path, monkeypatch, caplog):
    import deskmate_hub.control.gateway_service as entrypoint
    async def failure(settings):
        raise RuntimeError('synthetic-PAT-and-personal-device-data')
    monkeypatch.setattr(entrypoint, 'serve', failure)
    args = ['--config', str(configuration(tmp_path)), '--mock', '--enable',
            '--journal', str(tmp_path / 'private' / 'commands.db')]
    assert main(args) == 1
    assert 'mock_gateway_failed' in caplog.text
    assert 'synthetic-PAT' not in caplog.text and 'Traceback' not in caplog.text


@pytest.mark.parametrize('number', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_nonfinite_json_is_discarded_before_journal_claim(tmp_path, number):
    service = MockGatewayService(settings(tmp_path))
    try:
        body = ('{"node":"hub","data":{"execution_mode":"mock","command_id":"bad-number",'
                '"target_id":"mock_lamp","operation":"set_brightness","value":' + number + ','
                '"gate":"suggest","requires_confirmation":false,"expires_ts_ms":9999999999999}}')
        service.client.on_message(service.client, None, SimpleNamespace(
            topic='deskmate/control/cmd', payload=body.encode(), retain=False))
        asyncio.run(service.gateway.pump())
        assert not service.gateway.results and not service.gateway.adapters['mock'].calls
        assert not service.gateway._journal.load()
    finally:
        service.gateway.close()


def test_journal_rejects_symlink_ancestors_hardlinks_and_sqlite_sidecar_alias(tmp_path):
    private = tmp_path / 'real' / 'private'
    private.mkdir(parents=True, mode=0o700)
    alias = tmp_path / 'alias'; alias.symlink_to(private.parent, target_is_directory=True)
    with pytest.raises(ConfigurationError): prepare_journal(alias / 'private' / 'commands.db')
    source = private / 'other'; source.write_text('not-a-database'); source.chmod(0o600)
    path = private / 'commands.db'
    path.hardlink_to(source)
    with pytest.raises(ConfigurationError): prepare_journal(path)
    path.unlink()
    sidecar = private / 'commands.db-journal'; sidecar.symlink_to(source)
    with pytest.raises(ConfigurationError): prepare_journal(path)
    assert source.read_text() == 'not-a-database'


@pytest.mark.parametrize('failure', ['connect', 'subscribe'])
def test_mqtt_access_denial_clears_ready_and_fails_closed(tmp_path, failure):
    service = MockGatewayService(settings(tmp_path))
    try:
        service.ready.set()
        if failure == 'connect':
            service.client.on_connect(service.client, None, None, 5)
        else:
            service.on_subscribe(service.client, None, 1, [SimpleNamespace(is_failure=True)])
        assert service.rejected.is_set() and not service.ready.is_set()
        assert not service.gateway.results and not service.gateway.adapters['mock'].calls
    finally:
        service.gateway.close()
