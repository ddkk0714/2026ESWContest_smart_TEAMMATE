"""Fake SSH children only: these tests never contact a board."""
import tempfile
import threading
import unittest

from sensor_forward import command, failure_reason, supervise


class SensorForwardTests(unittest.TestCase):
    def test_command_is_verified_noninteractive_and_only_loopback_forwards(self):
        with tempfile.NamedTemporaryFile() as trust:
            args = command('192.0.2.5', '192.0.2.4', trust.name)
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('BatchMode=yes', args)
        self.assertIn('ForwardAgent=no', args)
        self.assertIn('-N', args)
        forwards = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '-L']
        self.assertEqual(forwards, [f'127.0.0.2:{p}:192.0.2.4:{p}' for p in (1883, 8771, 8772, 8765)])
        self.assertEqual(args[-1], 'root@192.0.2.5')

    def test_missing_trust_file_and_non_addresses_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            command('192.0.2.5', '192.0.2.4', '/file-that-does-not-exist')
        with self.assertRaises(ValueError):
            command('-oProxyCommand=anything', '192.0.2.4', __file__)

    def test_security_and_bind_errors_do_not_retry(self):
        for text in ('Host key verification failed', 'REMOTE HOST IDENTIFICATION HAS CHANGED',
                     'Permission denied (publickey)', 'Address already in use',
                     'Could not request local forwarding'):
            self.assertNotEqual(failure_reason(text), 'network_or_ssh_exit')
            events = []
            spawn = lambda *a, **k: FakeChild(text)
            supervise(['fake-ssh'], threading.Event(), events.append, spawn=spawn)
            self.assertEqual(events[-1]['status'], 'stopped')
            self.assertFalse(any(e['status'] == 'retrying' for e in events))

    def test_transient_failure_retries_with_bounded_delay(self):
        class Stop:
            waits = []
            def is_set(self):
                return len(self.waits) == 5
            def wait(self, delay):
                self.waits.append(delay)
        stop = Stop()
        events = []
        supervise(['fake'], stop, events.append,
                  spawn=lambda *a, **k: FakeChild('Connection timed out'), clock=lambda: 0)
        self.assertEqual(stop.waits, [5, 10, 20, 30, 30])
        self.assertEqual(events[-1]['reason'], 'operator_stop')

    def test_running_more_than_two_hours_does_not_expire(self):
        stop = threading.Event()
        events = []
        times = iter((0, 8000, 8000))
        count = [0]
        def spawn(*a, **k):
            count[0] += 1
            if count[0] == 2:
                stop.set()
            return FakeChild('Connection reset by peer')
        stop.wait = lambda delay: None
        supervise(['fake'], stop, events.append, spawn=spawn, clock=lambda: next(times))
        self.assertEqual(count[0], 2)
        self.assertEqual(events[1]['retry_after_sec'], 5)

    def test_stop_terminates_only_owned_child(self):
        stop = threading.Event()
        child = FakeChild('')
        def spawn(*a, **k):
            stop.set()
            return child
        supervise(['fake'], stop, lambda event: None, spawn=spawn)
        self.assertTrue(child.terminated)


class FakeChild:
    pid = 99999999
    terminated = False
    def __init__(self, stderr):
        self.stderr = stderr
        self.exited = False
    def communicate(self, timeout=None):
        self.exited = True
        return None, self.stderr
    def poll(self):
        return 255 if self.exited else None
    def terminate(self):
        self.terminated = True
    def kill(self):
        self.terminated = True


if __name__ == '__main__':
    unittest.main()
