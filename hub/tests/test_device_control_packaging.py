"""Host-only packaging smoke check: a fake ELF and a tiny stdlib directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def test_atlas_payload_includes_disabled_policy_without_pyyaml_runtime(tmp_path):
    executable = tmp_path/'fake-service'; executable.write_bytes(b'not-a-real-ELF')
    stdlib = tmp_path/'stdlib'; stdlib.mkdir()
    hub = ROOT/'hub/deskmate_hub'
    result = subprocess.run([sys.executable, str(ROOT/'hub/atlas/tools/build_payload.py'),
        '--executable', str(executable), '--hub', str(hub), '--config', str(hub/'config/fsm.yaml'),
        '--ingest-config', str(hub/'config/ingest.yaml'), '--stdlib', str(stdlib)],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(executable) as archive:
        config = json.loads(archive.read('deskmate_hub/config/environment_control.json'))
        assert not config['enabled'] and not config['automatic_control']
    code = '''
import sys
for name in ('yaml', '_socket', '_random', '_ssl'):
    sys.modules[name] = None
sys.path.insert(0, sys.argv[1])
from deskmate_hub.control.environment_policy import load_environment_control_config
from deskmate_hub.live import LiveHub
from deskmate_hub.ingest import SensorCache
hub = LiveHub(SensorCache())
assert not hub.environment_policy.enabled
print('restricted packaged defaults ok')
'''
    env = dict(os.environ); env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-c', code, str(executable)], cwd=tmp_path,
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_policy_and_mock_hub_do_not_import_gateway_sdk_or_sqlite_on_restricted_path():
    code = '''
import sys
for name in ('_socket', '_random', '_ssl', 'asyncio', 'sqlite3', 'aiohttp', 'thinqconnect', 'paho'):
    sys.modules[name] = None
from deskmate_hub.control.environment_policy import load_environment_control_config, EnvironmentControlPolicy
from deskmate_hub.control.devices import Capability, Device, DeviceRegistry
from deskmate_hub.ingest import Sample, SensorCache
from deskmate_hub.inference import GateMode
from deskmate_hub.live import LiveHub
cfg = load_environment_control_config()
cfg['enabled'] = True
cfg['rules']['co2_high']['minimum_duration_sec'] = 0
r = DeviceRegistry()
r.register(Device('mock_vent', 'mock', ('outside_air',),
    {'set_power': Capability('set_power', ('on','off'), verified=True)}, online=True,
    state={'set_power':'off'}, observed_at=1000))
hub = LiveHub(SensorCache(), environment_control_cfg=cfg, device_registry=r)
hub.environment_policy.observe(Sample(1000,1000,None,{'co2_ppm':2000,'co2_valid':True}),1000)
assert hub.environment_policy.plan('ACTION_ENV','environment',GateMode.SUGGEST,1000)
assert 'deskmate_hub.control.device_gateway' not in sys.modules
assert 'deskmate_hub.control.command_journal' not in sys.modules
print('pure policy/mock path ok')
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT/'hub'))
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
