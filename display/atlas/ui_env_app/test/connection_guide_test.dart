import 'dart:async';

import 'package:deskmate_display/connection_guide.dart';
import 'package:deskmate_display/link_status.dart';
import 'package:deskmate_display/mqtt_probe.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

MqttProbeResult _fail(MqttProbeStep step, LinkCause cause, {String? detail}) =>
    MqttProbeResult(
        ok: false,
        cause: cause,
        failedAt: step,
        detail: detail,
        elapsed: const Duration(milliseconds: 5));

const _ok = MqttProbeResult(
    ok: true,
    cause: LinkCause.none,
    hubHealth: 'online',
    elapsed: Duration(milliseconds: 5));

/// onStep 을 steps 순서대로 부른 뒤 result 를 돌려주는 가짜 진단기.
MqttProber _prober(List<MqttProbeStep> steps, MqttProbeResult result) =>
    (host, port, {onStep}) async {
      for (final step in steps) {
        onStep?.call(step);
      }
      return result;
    };

void main() {
  group('MqttStartupCheck', () {
    test('marks steps before the failing one as passed', () async {
      final check = MqttStartupCheck(
          prober: _prober([MqttProbeStep.tcp, MqttProbeStep.connack],
              _fail(MqttProbeStep.connack, LinkCause.brokerRefused)));
      final result = await check.run('192.0.2.10', 1883);

      expect(result.ok, isFalse);
      expect(check.failed, isTrue);
      expect(check.passed(MqttProbeStep.tcp), isTrue);
      expect(check.passed(MqttProbeStep.connack), isFalse);
      expect(check.running, isNull);
      expect(probeGuidance(result), contains('pi4-broker-start.sh'));
    });

    test('success passes every step', () async {
      final check = MqttStartupCheck(prober: _prober([MqttProbeStep.tcp], _ok));
      await check.run('192.0.2.10', 1883);
      expect(MqttProbeStep.values.every(check.passed), isTrue);
      expect(check.failed, isFalse);
    });

    test('hub-side causes use the hub guidance', () {
      expect(
          probeGuidance(_fail(MqttProbeStep.hubHealth, LinkCause.hubOffline)),
          contains('pi4-hub-activate.sh'));
      expect(probeGuidance(_fail(MqttProbeStep.firstState, LinkCause.noState)),
          contains('hub 로그'));
    });

    test('a late result from an older run does not overwrite the new one',
        () async {
      final slow = Completer<MqttProbeResult>();
      var calls = 0;
      final check = MqttStartupCheck(prober: (host, port, {onStep}) {
        calls++;
        return calls == 1 ? slow.future : Future.value(_ok);
      });
      final first = check.run('192.0.2.10', 1883);
      await check.run('192.0.2.20', 1883);
      slow.complete(_fail(MqttProbeStep.tcp, LinkCause.networkUnreachable));
      await first;

      expect(check.host, '192.0.2.20');
      expect(check.result?.ok, isTrue);
    });

    test('reset discards a pending run', () async {
      final slow = Completer<MqttProbeResult>();
      final check =
          MqttStartupCheck(prober: (host, port, {onStep}) => slow.future);
      final pending = check.run('192.0.2.10', 1883);
      expect(check.isRunning, isTrue);
      check.reset();
      slow.complete(_fail(MqttProbeStep.tcp, LinkCause.networkUnreachable));
      await pending;
      expect(check.host, isNull);
      expect(check.result, isNull);
    });
  });

  group('ConnectionGuidePanel', () {
    Future<List<String>> pump(
        WidgetTester tester, MqttStartupCheck check) async {
      final taps = <String>[];
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: ConnectionGuidePanel(
            check: check,
            onRetry: () => taps.add('retry'),
            onChangeAddress: () => taps.add('address'),
            onDemo: () => taps.add('demo'),
          ),
        ),
      ));
      return taps;
    }

    testWidgets('shows the failed step, guidance and detail', (tester) async {
      final check = MqttStartupCheck(
          prober: _prober(
              [MqttProbeStep.tcp],
              _fail(MqttProbeStep.tcp, LinkCause.networkUnreachable,
                  detail: 'SocketException: Connection refused')));
      await check.run('192.0.2.10', 1883);
      final taps = await pump(tester, check);

      expect(find.text('MQTT 192.0.2.10:1883'), findsOneWidget);
      expect(
          find.byKey(const ValueKey('guide-step-tcp-failed')), findsOneWidget);
      expect(find.byKey(const ValueKey('guide-step-connack-pending')),
          findsOneWidget);
      expect(find.textContaining('IP 가 바뀌었으면'), findsOneWidget);
      expect(find.textContaining('Connection refused'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('guide-retry')));
      await tester.tap(find.byKey(const ValueKey('guide-change-address')));
      await tester.tap(find.byKey(const ValueKey('guide-demo')));
      expect(taps, ['retry', 'address', 'demo']);
    });

    testWidgets('shows progress while running and disables retry',
        (tester) async {
      final gate = Completer<MqttProbeResult>();
      final check = MqttStartupCheck(prober: (host, port, {onStep}) {
        onStep?.call(MqttProbeStep.tcp);
        onStep?.call(MqttProbeStep.connack);
        return gate.future;
      });
      unawaited(check.run('192.0.2.10', 1883));
      await pump(tester, check);

      expect(
          find.byKey(const ValueKey('guide-step-tcp-passed')), findsOneWidget);
      expect(find.byKey(const ValueKey('guide-step-connack-running')),
          findsOneWidget);
      final retry = tester
          .widget<FilledButton>(find.byKey(const ValueKey('guide-retry')));
      expect(retry.onPressed, isNull);
      expect(find.byKey(const ValueKey('guide-message')), findsNothing);

      gate.complete(_ok);
      await tester.pump();
      expect(find.byKey(const ValueKey('guide-step-firstState-passed')),
          findsOneWidget);
    });
  });
}
