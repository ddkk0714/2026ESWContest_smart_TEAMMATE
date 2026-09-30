import 'package:deskmate_display/connection_page.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/link_status.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _t0 = DateTime(2026, 9, 30, 12);
DateTime _at(int seconds) => _t0.add(Duration(seconds: seconds));

DisplayState _state({bool mmwave = true, bool environment = true}) =>
    DisplayState(
      fsmState: 'FOCUS_PC',
      phase: 'focus',
      context: 'pc',
      focus: 0.2,
      fatigue: 0.2,
      confidence: 0.5,
      gate: 'none',
      reasons: const [],
      sequence: 1,
      timestamp: _t0,
      present: mmwave ? true : null,
      mmwaveMotionState: mmwave ? 'still' : null,
      co2Ppm: environment ? 700 : null,
    );

void main() {
  group('assembleLinkSnapshot', () {
    test('demo never raises a problem for hub or sensors', () {
      final snap = assembleLinkSnapshot(
          now: _t0, kind: StateSourceKind.demo, state: _state(mmwave: false));
      expect(snap.hasProblem, isFalse);
      expect(snap[LinkId.mmwave]?.cause, LinkCause.demo);
    });

    test('healthy MQTT with sensors is all ok', () {
      final snap = assembleLinkSnapshot(
        now: _at(2),
        kind: StateSourceKind.mqtt,
        mqttConnected: true,
        mqttConnectedAt: _t0,
        lastStateAt: _t0,
        hubHealth: 'online',
        state: _state(),
      );
      expect(snap.hasProblem, isFalse);
      for (final id in [
        LinkId.mqtt,
        LinkId.hub,
        LinkId.mmwave,
        LinkId.environment
      ]) {
        expect(snap[id]?.health, LinkHealth.ok, reason: id.name);
      }
    });

    test('fresh state without mmWave flags only the sensor', () {
      final snap = assembleLinkSnapshot(
        now: _at(2),
        kind: StateSourceKind.mqtt,
        mqttConnected: true,
        lastStateAt: _t0,
        state: _state(mmwave: false),
      );
      expect(snap.attention.map((s) => s.id), [LinkId.mmwave]);
    });

    test('startup connect failure shows on MQTT, hub waits', () {
      final snap = assembleLinkSnapshot(
        now: _t0,
        kind: StateSourceKind.mqtt,
        startupFailure: LinkCause.networkUnreachable,
      );
      expect(snap[LinkId.mqtt]?.cause, LinkCause.networkUnreachable);
      expect(snap[LinkId.hub]?.health, LinkHealth.checking);
      expect(snap.attention.first.id, LinkId.mqtt);
    });

    test('hub-side startup failure is left to the hub row', () {
      final snap = assembleLinkSnapshot(
        now: _at(20),
        kind: StateSourceKind.mqtt,
        mqttConnected: true,
        mqttConnectedAt: _t0,
        startupFailure: LinkCause.hubOffline,
        hubHealth: 'offline',
      );
      expect(snap[LinkId.hub]?.cause, LinkCause.hubOffline);
      expect(snap[LinkId.mqtt]?.cause, isNot(LinkCause.hubOffline));
    });

    test('posture and Bluetooth default to not being problems', () {
      final snap = assembleLinkSnapshot(now: _t0, kind: StateSourceKind.demo);
      expect(snap[LinkId.posture]?.health, LinkHealth.checking);
      expect(snap[LinkId.speaker]?.health, LinkHealth.unconfigured);
      expect(snap[LinkId.lamp]?.needsAttention, isFalse);
    });

    test('a reported posture problem is included', () {
      final snap = assembleLinkSnapshot(
        now: _t0,
        kind: StateSourceKind.demo,
        posture: const LinkStatus(
            id: LinkId.posture,
            health: LinkHealth.degraded,
            cause: LinkCause.deviceNotFound),
      );
      expect(snap.attention.single.id, LinkId.posture);
    });
  });

  group('widgets', () {
    final problem = LinkSnapshot([
      LinkStatus(
          id: LinkId.mqtt,
          health: LinkHealth.down,
          cause: LinkCause.brokerRefused,
          lastSeen: _t0,
          detail: 'MQTT 192.0.2.10:1883'),
      const LinkStatus(id: LinkId.keystroke, health: LinkHealth.ok),
      const LinkStatus(
          id: LinkId.speaker,
          health: LinkHealth.unconfigured,
          cause: LinkCause.notConfigured),
    ]);

    testWidgets('connection page shows rows, guidance and actions',
        (tester) async {
      final actions = <String>[];
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: ConnectionPage(
            snapshot: problem,
            now: _at(12),
            sourceLabel: 'MQTT 192.0.2.10:1883',
            onAction: (id) => actions.add(id.name),
            onReloadAll: () => actions.add('reload'),
            onChangeAddress: () => actions.add('address'),
          ),
        ),
      ));

      expect(find.byKey(const ValueKey('link-row-mqtt')), findsOneWidget);
      expect(find.text('끊김'), findsOneWidget);
      expect(find.text('12초 전'), findsOneWidget);
      expect(find.byKey(const ValueKey('link-guidance-mqtt')), findsOneWidget);
      expect(
          find.byKey(const ValueKey('link-guidance-keystroke')), findsNothing);
      // 키스트로크는 앱에서 할 수 있는 조치가 없다.
      expect(find.byKey(const ValueKey('link-action-keystroke')), findsNothing);

      await tester.tap(find.byKey(const ValueKey('link-action-mqtt')));
      await tester.tap(find.byKey(const ValueKey('link-action-speaker')));
      await tester.tap(find.byKey(const ValueKey('connection-reload-all')));
      await tester.tap(find.byKey(const ValueKey('connection-change-address')));
      expect(actions, ['mqtt', 'speaker', 'reload', 'address']);
    });

    testWidgets('badge and status bar appear only with a problem',
        (tester) async {
      var taps = 0;
      Future<void> pump(LinkSnapshot snap) => tester.pumpWidget(MaterialApp(
            home: Scaffold(
              body: Column(children: [
                LinkProblemBadge(snapshot: snap, onTap: () => taps++),
                LinkStatusBar(snapshot: snap, onDismiss: () => taps += 10),
              ]),
            ),
          ));

      await pump(LinkSnapshot(const [
        LinkStatus(id: LinkId.mqtt, health: LinkHealth.ok),
      ]));
      expect(find.byKey(const ValueKey('link-problem-badge')), findsNothing);
      expect(find.byKey(const ValueKey('link-status-bar')), findsNothing);

      await pump(problem);
      expect(find.text('1'), findsOneWidget); // 문제 1건
      expect(find.textContaining('MQTT 브로커 확인 필요'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('link-problem-badge')));
      await tester.tap(find.byKey(const ValueKey('link-status-bar')));
      expect(taps, 11);
    });
  });
}
