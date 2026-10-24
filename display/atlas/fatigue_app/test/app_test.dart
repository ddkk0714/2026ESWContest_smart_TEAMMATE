import 'dart:io';

import 'package:deskmate_fatigue/fatigue_log.dart';
import 'package:deskmate_fatigue/main.dart';
import 'package:deskmate_fatigue/mqtt_sink.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Directory dir;
  setUp(() => dir = Directory.systemTemp.createTempSync('fatigue-app'));
  tearDown(() => dir.deleteSync(recursive: true));

  test('broker falls back to the main app settings, own file wins', () {
    final store = BrokerStore(dirs: [dir.path]);
    expect(store.load(), isNull);
    File('${dir.path}/settings.json')
        .writeAsStringSync('{"mqtt_host":"172.16.34.146","mqtt_port":18883}');
    expect(store.load().toString(), '172.16.34.146:18883');
    expect(store.save(const BrokerAddress('192.168.0.9')), isTrue);
    expect(store.load().toString(), '192.168.0.9:1883');
  });

  testWidgets('without a broker the start button stays off at 1024x600',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final profiles = FatigueProfileStore(candidates: ['${dir.path}/p.json'])
      ..save(const [
        FatigueProfile(
            id: 'A-1',
            slot: 'A',
            chronotype: 'morning',
            usualSleepH: 7,
            workType: 'study')
      ]);
    await tester.pumpWidget(FatigueApp(
        brokers: BrokerStore(dirs: [dir.path]), profiles: profiles));
    expect(find.text('브로커 주소 없음 — 눌러서 입력'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('fatigue-slot-A')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('kss-4')));
    await tester.pump();
    final start = tester.widget<FilledButton>(
        find.byKey(const ValueKey('fatigue-start')));
    expect(start.onPressed, isNull);
    expect(tester.takeException(), isNull);
  });

  // 실제 브로커가 있을 때만: FATIGUE_TEST_BROKER=host:port flutter test
  final broker = Platform.environment['FATIGUE_TEST_BROKER'];
  test('publishes one envelope to a real broker', () async {
    final parts = broker!.split(':');
    final sink = MqttFatigueSink(BrokerAddress(parts[0], int.parse(parts[1])));
    final session = FatigueSession()..sink = sink;
    await session.start(
        const FatigueProfile(
            id: 'E-2e2e',
            slot: 'E',
            chronotype: 'neither',
            usualSleepH: 7,
            workType: 'coding'),
        sleepHours: 6,
        kss: 4);
    await session.sleepy();
    await session.end(kss: 6);
    expect(session.outbox, isEmpty);
    await Future<void>.delayed(const Duration(seconds: 1));
    sink.close();
  }, skip: broker == null ? 'FATIGUE_TEST_BROKER 없음' : false);
}
