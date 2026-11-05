"""Keep a verified, loopback-only sensor SSH forward alive until Ctrl+C.

No Cloud requests, remote commands, MQTT publications, or board changes.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

PORTS = (1883, 8771, 8772, 8765)


def command(pi5, pi4, known_hosts, *, alias=None):
    # Use verified addresses supplied by the operator, never discover/trust keys.
    for host in (pi5, pi4):
        if ipaddress.ip_address(host).version != 4:
            raise ValueError('IPv4 addresses required')
    trust = Path(known_hosts).resolve(strict=True)
    if not trust.is_file():
        raise ValueError('known_hosts file required')
    args = ['ssh', '-N', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={trust}', '-o', 'ConnectTimeout=5',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=2', '-o', 'ForwardAgent=no']
    if alias:
        if alias.startswith('-') or any(c.isspace() for c in alias):
            raise ValueError('invalid host key alias')
        args += ['-o', f'HostKeyAlias={alias}']
    for port in PORTS:
        args += ['-L', f'127.0.0.2:{port}:{pi4}:{port}']
    return args + [f'root@{pi5}']


def failure_reason(stderr):
    text = stderr.lower()
    if any(x in text for x in ('host key verification failed', 'remote host identification has changed',
                               'no host key is known', 'no ed25519 host key is known')):
        return 'host_key_verification_failed'
    if any(x in text for x in ('permission denied', 'authentication failed', 'too many authentication failures',
                               'bad owner or permissions')):
        return 'authentication_failed'
    if any(x in text for x in ('address already in use', 'cannot assign requested address',
                               'could not request local forwarding', 'cannot listen to port')):
        return 'local_forward_failed'
    return 'network_or_ssh_exit'


def supervise(args, stop, emit, *, spawn=subprocess.Popen, clock=time.monotonic):
    """Retry transport failures only; never stop because of an elapsed demo timer."""
    delay = 5
    while not stop.is_set():
        started = clock()
        child = None
        try:
            child = spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)
            emit({'status': 'ssh_running_unverified', 'child_pid': child.pid})
            while True:
                if stop.is_set():
                    child.terminate()
                try:
                    _, stderr = child.communicate(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if stop.is_set():
                        # Only this supervisor's own SSH child is terminated.
                        child.kill()
                        _, stderr = child.communicate()
                        break
        except OSError:
            emit({'status': 'stopped', 'reason': 'ssh_start_failed', 'child_pid': None})
            return
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
        if stop.is_set():
            break
        reason = failure_reason(stderr or '')
        if reason != 'network_or_ssh_exit':
            emit({'status': 'stopped', 'reason': reason, 'child_pid': None})
            return
        if clock() - started >= 60:
            delay = 5
        emit({'status': 'retrying', 'reason': reason, 'retry_after_sec': delay, 'child_pid': None})
        stop.wait(delay)
        delay = min(30, delay * 2)
    emit({'status': 'stopped', 'reason': 'operator_stop', 'child_pid': None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi5', required=True)
    parser.add_argument('--pi4-lan', required=True)
    parser.add_argument('--known-hosts', required=True)
    parser.add_argument('--host-key-alias')
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args()
    ssh = command(args.pi5, args.pi4_lan, args.known_hosts, alias=args.host_key_alias)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    def emit(state):
        state.update(supervisor_pid=os.getpid(), no_elapsed_time_limit=True,
                     strict_hostkey_checking=True, board_changed=False)
        # Fixed categories only. Do not expose raw SSH stderr or credentials.
        print(json.dumps(state), flush=True)
        if args.state_file:
            temporary = args.state_file.with_suffix('.new')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(state, f)
            temporary.replace(args.state_file)

    supervise(ssh, stop, emit)


if __name__ == '__main__':
    main()
