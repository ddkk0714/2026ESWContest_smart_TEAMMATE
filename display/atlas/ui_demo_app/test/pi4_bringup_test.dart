import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/live/pi4_bringup.dart';
import 'package:deskmate_ui_demo/live/transport.dart';

/// 브로커가 없는 재부팅 직후: connect 가 늘 실패한다.
class DeadTransport implements LiveTransport {
  final _messages = StreamController<LiveMessage>.broadcast();
  final _connection = StreamController<bool>.broadcast();
  @override
  Stream<LiveMessage> get messages => _messages.stream;
  @override
  Stream<bool> get connection => _connection.stream;
  @override
  bool get isConnected => false;
  @override
  Future<bool> connect() async => false;
  @override
  void subscribe(String topic) {}
  @override
  void publish(String topic, String text, {bool retain = false}) {}
  @override
  void close() {}
}

void main() {
  final t0 = DateTime(2026, 11, 3, 2, 0);

  group('BringupGuard', () {
    test('waits 20 s of no hub before the first run', () {
      final guard = BringupGuard();
      expect(guard.shouldRun(t0, live: false), isFalse);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 19)), live: false), isFalse);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 20)), live: false), isTrue);
    });

    test('does not run again inside the 2 min cooldown', () {
      final guard = BringupGuard();
      guard.shouldRun(t0, live: false);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 20)), live: false), isTrue);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 139)), live: false), isFalse);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 140)), live: false), isTrue);
    });

    test('unreachable Pi 4 (still booting) is retried after 15 s, not 2 min', () {
      final guard = BringupGuard();
      guard.shouldRun(t0, live: false);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 20)), live: false), isTrue);
      guard.unreachable();
      expect(guard.shouldRun(t0.add(const Duration(seconds: 34)), live: false), isFalse);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 35)), live: false), isTrue);
      // 이번엔 닿았다고 치면 다시 2분.
      expect(guard.shouldRun(t0.add(const Duration(seconds: 60)), live: false), isFalse);
    });

    test('live hub resets the wait', () {
      final guard = BringupGuard();
      guard.shouldRun(t0, live: false);
      guard.shouldRun(t0.add(const Duration(seconds: 15)), live: true);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 25)), live: false), isFalse);
      expect(guard.shouldRun(t0.add(const Duration(seconds: 45)), live: false), isTrue);
    });
  });

  group('LiveLink bring-up', () {
    late DateTime now;
    late List<String> calls;

    LiveLink link({BringupRunner? runner}) => LiveLink(
          transportFactory: (host, port) => DeadTransport(),
          clock: () => now,
          retryDelay: const Duration(hours: 1),
          bringupRunner: runner,
        );

    setUp(() {
      now = t0;
      calls = [];
    });

    test('calls the runner with the hub host after 20 s without hub, then shows the summary', () async {
      final live = link(runner: (host) async {
        calls.add(host);
        return const BringupResult(ok: true, summary: 'bringup hub=ok tof=ok uart=/dev/ttyUSB0');
      });
      live.configure('172.16.34.199', 1883);
      await live.checkBringup();
      expect(calls, isEmpty);
      now = t0.add(const Duration(seconds: 20));
      await live.checkBringup();
      expect(calls, ['172.16.34.199']);
      expect(live.bringupRunning, isFalse);
      expect(live.bringupSummary, contains('hub=ok'));
      live.dispose();
    });

    test('does nothing without a runner or without a host', () async {
      final noRunner = link();
      noRunner.configure('172.16.34.199', 1883);
      now = t0.add(const Duration(minutes: 5));
      await noRunner.checkBringup();
      expect(noRunner.bringupSummary, isNull);
      noRunner.dispose();

      now = t0;
      final noHost = link(runner: (host) async {
        calls.add(host);
        return const BringupResult(ok: true, summary: 'x');
      });
      await noHost.checkBringup();
      now = t0.add(const Duration(minutes: 5));
      await noHost.checkBringup();
      expect(calls, isEmpty);
      noHost.dispose();
    });

    test('LiveLink retries soon when SSH could not be reached', () async {
      final live = link(runner: (host) async {
        calls.add(host);
        return const BringupResult(ok: false, reached: false, summary: 'Pi 4 SSH 에 못 닿음');
      });
      live.configure('172.16.34.199', 1883);
      await live.checkBringup();
      now = t0.add(const Duration(seconds: 20));
      await live.checkBringup();
      now = t0.add(const Duration(seconds: 35));
      await live.checkBringup();
      expect(calls, hasLength(2));
      live.dispose();
    });

    test('a failed SSH is shown and retried only after the cooldown', () async {
      final live = link(runner: (host) async {
        calls.add(host);
        return const BringupResult(ok: false, summary: 'bringup 실패: SocketException');
      });
      live.configure('172.16.34.199', 1883);
      await live.checkBringup();
      now = t0.add(const Duration(seconds: 20));
      await live.checkBringup();
      now = t0.add(const Duration(seconds: 60));
      await live.checkBringup();
      expect(calls, hasLength(1));
      expect(live.bringupSummary, contains('실패'));
      now = t0.add(const Duration(seconds: 140));
      await live.checkBringup();
      expect(calls, hasLength(2));
      live.dispose();
    });
  });
}
