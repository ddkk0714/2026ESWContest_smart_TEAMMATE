# Recovery scripts run against fake commands, never a real board.
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

SOURCE = Path(__file__).parent / 'atlas-recovery'
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')
pytestmark = pytest.mark.skipif(not BASH or not Path(BASH).exists(), reason='bash required')

def posix(path):
    value = str(path.resolve()).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value

@pytest.fixture
def board(tmp_path):
    root = tmp_path
    commands = root / 'commands'
    prefix = root / 'recovery'
    service = root / 'hub'
    units = root / 'units'
    for directory in (commands, prefix, service, units):
        directory.mkdir()
    binary = service / 'deskmate_hub_service'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    (root / 'unit-paths').write_text(posix(units))
    (root / 'time').write_text('1000')
    (root / 'apps').write_text('[]')
    (root / 'http-state').write_text(json.dumps({'boot_id':'a', 'seq':1, 'data':{'source':'fsm'}}))
    config = (SOURCE / 'recovery.env.example').read_text()
    overrides = {'SERVICE_DIR':posix(service), 'DBUS_DIR':posix(root / 'dbus'),
                 'STATE_DIR':posix(root / 'state'), 'UART_DEV':posix(root / 'missing-uart')}
    lines = [key + "='" + overrides[key] + "'" if key in overrides else line
             for line in config.splitlines() for key in [line.split('=', 1)[0]]]
    (prefix / 'recovery.env').write_text('\n'.join(lines) + '\n')
    mocks = {
        'systemctl': '''echo "systemctl $*" >> "$FIXTURE/commands.log"
case "$1" in
show) cat "$FIXTURE/unit-paths";;
is-active) [ ! -f "$FIXTURE/inactive" ];;
esac
''',
        'busctl': '''echo "busctl $*" >> "$FIXTURE/commands.log"
case "$*" in
*GetConnectionUnixProcessID*) [ ! -f "$FIXTURE/no-owner" ] && echo 'u 4242';;
*StartServiceByName*) [ ! -f "$FIXTURE/start-fails" ];;
esac
''',
        'abusctl': '''echo "abusctl $*" >> "$FIXTURE/commands.log"
case "$3" in
ListRunningApps) cat "$FIXTURE/apps";;
Start) printf '["%s"]' "$4" > "$FIXTURE/apps";;
esac
''',
        'wget': '[ ! -f "$FIXTURE/http-fails" ] || exit 1\ncat "$FIXTURE/http-state"\n',
        'id': 'echo 0\n',
        'date': 'cat "$FIXTURE/time"\n',
        'chown': 'echo "chown $*" >> "$FIXTURE/commands.log"\n',
        'chmod': 'echo "chmod $*" >> "$FIXTURE/commands.log"\n',
    }
    for name, body in mocks.items():
        target = commands / name
        target.write_bytes(('#!/bin/sh\n' + body).encode())
        target.chmod(0o755)
    env = dict(os.environ, FIXTURE=posix(root), DESKMATE_RECOVERY_PREFIX=posix(prefix))
    def call(script, *args, check=True):
        result = subprocess.run([BASH, '-c', 'PATH="' + posix(commands) + ':$PATH"; export PATH; sh "$@"',
                                 'fixture', posix(SOURCE / script), *args], env=env,
                                capture_output=True, text=True, timeout=20)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result
    return root, prefix, service, units, call

def log(root):
    return (root / 'commands.log').read_text() if (root / 'commands.log').exists() else ''

def test_install_and_remove_managed_units(board):
    root, prefix, service, units, call = board
    call('install.sh', 'pi4', '--unit-dir', posix(units), '--no-start')
    assert (units / 'deskmate-hub.service').exists()
    assert (units / 'multi-user.target.wants/deskmate-hub.service').exists()
    assert '@PREFIX@' not in (units / 'deskmate-hub.service').read_text()
    assert not (units / 'deskmate-display.service').exists()
    call('install.sh', 'pi4', '--unit-dir', posix(units), '--remove')
    assert not (units / 'deskmate-hub.service').exists()
    assert (prefix / 'recovery.env').exists()

def test_reject_unmanaged_and_unloaded_units(board):
    root, prefix, service, units, call = board
    target = units / 'deskmate-hub.service'
    target.write_text('keep me')
    assert call('install.sh', 'pi4', '--unit-dir', posix(units), check=False).returncode != 0
    assert target.read_text() == 'keep me'
    assert call('install.sh', 'pi4', '--unit-dir', posix(root), check=False).returncode != 0

def test_prepare_preserves_settings_and_reload_is_idempotent(board):
    root, prefix, service, units, call = board
    settings = 'DESKMATE_MQTT_HOST=custom-host\nDESKMATE_UART_DEV=/missing-custom\n'
    (service / 'hub.env').write_text(settings)
    call('lifecycle.sh', 'hub-prepare')
    call('lifecycle.sh', 'hub-prepare')
    assert (service / 'hub.env').read_text() == settings
    assert log(root).count('systemctl reload dbus') == 1
    assert 'User=' in (root / 'dbus/system-services/com.deskmate.hub1.service').read_text()

def test_stalled_fsm_recovers_with_cooldown_and_boot_change(board):
    root, prefix, service, units, call = board
    for _ in range(7):
        call('lifecycle.sh', 'watch')
    assert log(root).count('systemctl try-restart deskmate-hub.service') == 1
    (root / 'http-state').write_text(json.dumps({'boot_id':'b', 'seq':1, 'data':{'source':'fsm', 'seq':999}}))
    call('lifecycle.sh', 'watch')
    assert (root / 'state/hub-failures').read_text().strip() == '0'
    assert (root / 'state/hub-last').read_text().strip() == 'b:1'

def test_native_fallback_and_http_failure_recover(board):
    root, prefix, service, units, call = board
    (root / 'http-state').write_text('{"seq":3,"data":{"fsm_state":"IDLE"}}')
    for _ in range(3):
        call('lifecycle.sh', 'watch')
    (root / 'time').write_text('2000')
    (root / 'http-fails').touch()
    for _ in range(3):
        call('lifecycle.sh', 'watch')
    assert log(root).count('systemctl try-restart deskmate-hub.service') == 2

def test_display_exact_match_and_idempotent_start(board):
    root, prefix, service, units, call = board
    config = prefix / 'recovery.env'
    config.write_text(config.read_text().replace('ROLE=pi4', 'ROLE=pi5'))
    (root / 'apps').write_text('["com.atlas.app.deskmate_display_extra"]')
    call('lifecycle.sh', 'display-start')
    call('lifecycle.sh', 'display-start')
    assert log(root).count('abusctl call com.atlas.AppManager1 Start ') == 1
    (root / 'apps').write_text('[]')
    call('lifecycle.sh', 'watch')
    assert 'systemctl try-restart deskmate-display.service' in log(root)

def test_status_is_read_only_and_manual_stop_is_respected(board):
    root, prefix, service, units, call = board
    call('lifecycle.sh', 'status')
    assert not (root / 'state').exists()
    (root / 'inactive').touch()
    call('lifecycle.sh', 'watch')
    assert 'try-restart' not in log(root)

def test_failed_activation_exits(board):
    root, prefix, service, units, call = board
    (root / 'start-fails').touch()
    assert call('lifecycle.sh', 'hub-supervise', check=False).returncode != 0


def test_pi5_installs_only_display_services(board):
    root, prefix, service, units, call = board
    config = prefix / 'recovery.env'
    config.write_text(config.read_text().replace('ROLE=pi4', 'ROLE=pi5'))
    call('install.sh', 'pi5', '--unit-dir', posix(units), '--no-start')
    assert (units / 'deskmate-display.service').exists()
    assert not (units / 'deskmate-hub.service').exists()
    assert not (units / 'deskmate-broker.service').exists()

def test_no_persistent_unit_path_refuses_install(board):
    root, prefix, service, units, call = board
    (root / 'unit-paths').write_text('/nonexistent-deskmate-units')
    result = call('install.sh', 'pi4', check=False)
    assert result.returncode != 0
    assert 'no writable persistent unit path' in result.stderr
    assert not (units / 'deskmate-hub.service').exists()

def test_unexpected_character_device_is_not_modified(board):
    root, prefix, service, units, call = board
    (service / 'hub.env').write_text('DESKMATE_UART_DEV=/dev/null\n')
    result = call('lifecycle.sh', 'hub-prepare', check=False)
    assert result.returncode != 0
    assert 'unexpected UART node' in result.stderr
    assert 'chmod 660 /dev/null' not in log(root)
