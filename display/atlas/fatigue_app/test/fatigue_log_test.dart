import 'dart:io';

import 'package:deskmate_fatigue/fatigue_log.dart';
import 'package:deskmate_fatigue/fatigue_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _Sink implements FatigueSink {
  final sent = <Map<String, Object?>>[];
  bool fail = false;

  @override
  Future<void> publishFatigue(Map<String, Object?> data) async {
    if (fail) throw StateError('offline');
    sent.add(data);
  }
}

const _profile = FatigueProfile(
    id: 'A-0001',
    slot: 'A',
    chronotype: 'evening',
    usualSleepH: 6.5,
    workType: 'coding');

void main() {
  late DateTime now;
  late _Sink sink;
  late FatigueSession session;

  setUp(() {
    now = DateTime(2026, 10, 21, 9);
    sink = _Sink();
    session = FatigueSession(clock: () => now)..sink = sink;
  });

  test('start sends profile answers but not the slot', () async {
    await session.start(_profile, sleepHours: 5.5, kss: 4);
    final start = sink.sent.single;
    expect(start['event'], 'session_start');
    expect(start['profile_id'], 'A-0001');
    expect(start['kss'], 4);
    expect(start['sleep_hours_last_night'], 5.5);
    expect(start['profile'],
        {'chronotype': 'evening', 'usual_sleep_h': 6.5, 'work_type': 'coding'});
    expect(start.toString(), isNot(contains('slot')));
  });

  test('prompt appears every 15 min and the answer carries prompt time',
      () async {
    await session.start(_profile, sleepHours: 7, kss: 3);
    now = now.add(const Duration(minutes: 14, seconds: 59));
    expect(session.tick(), isFalse);
    now = now.add(const Duration(seconds: 1));
    expect(session.tick(), isTrue);
    expect(session.promptDue, isTrue);
    now = now.add(const Duration(seconds: 20));
    await session.answer(6);
    final label = sink.sent.last;
    expect(label['event'], 'kss');
    expect(label['kss'], 6);
    expect(label['response_ms'], 20000);
    expect(label['session_id'], session.sessionId);
    expect(session.promptDue, isFalse);
    expect(session.nextPromptAt, now.add(const Duration(minutes: 15)));
  });

  test('unanswered prompt is recorded as missed and rescheduled', () async {
    await session.start(_profile, sleepHours: 7, kss: 3);
    now = now.add(const Duration(minutes: 15));
    session.tick();
    now = now.add(const Duration(minutes: 3));
    expect(session.tick(), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(sink.sent.last['event'], 'prompt_missed');
    expect(session.promptDue, isFalse);
  });

  test('labels sent while offline are kept and resent in order', () async {
    sink.fail = true;
    await session.start(_profile, sleepHours: 7, kss: 3);
    await session.sleepy();
    expect(session.outbox.map((m) => m['event']), ['session_start', 'sleepy']);
    sink.fail = false;
    session.tick();
    await Future<void>.delayed(Duration.zero);
    expect(sink.sent.map((m) => m['event']), ['session_start', 'sleepy']);
    expect(session.outbox, isEmpty);
  });

  test('end closes the session and rejects out-of-range KSS', () async {
    await session.start(_profile, sleepHours: 7, kss: 3);
    expect(() => session.answer(10), throwsRangeError);
    await session.end(kss: 8);
    expect(sink.sent.last['event'], 'session_end');
    expect(sink.sent.last['kss'], 8);
    expect(session.active, isFalse);
  });

  test('profile store round-trips and skips broken entries', () {
    final dir = Directory.systemTemp.createTempSync('fatigue');
    addTearDown(() => dir.deleteSync(recursive: true));
    final store = FatigueProfileStore(candidates: ['${dir.path}/p.json']);
    expect(store.load(), isEmpty);
    expect(store.save([_profile]), isTrue);
    expect(store.load().single.toJson(), _profile.toJson());
    File('${dir.path}/p.json').writeAsStringSync(
        '{"profiles":[{"id":"x","slot":"Z"},${'{"id":"B-1","slot":"B","chronotype":"morning","usual_sleep_h":8,"work_type":"study"}'}]}');
    expect(store.load().map((p) => p.id), ['B-1']);
  });

  testWidgets('page: make a profile, start, then answer and end',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final dir = Directory.systemTemp.createTempSync('fatigue');
    addTearDown(() => dir.deleteSync(recursive: true));
    final store = FatigueProfileStore(candidates: ['${dir.path}/p.json']);
    final live = FatigueSession()..sink = sink;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: FatiguePage(
                session: live,
                store: store,
                canSend: true,
                onChanged: () {}))));

    await tester.tap(find.byKey(const ValueKey('fatigue-slot-B')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('fatigue-profile-save')));
    await tester.pumpAndSettle();
    expect(store.load().single.slot, 'B');

    await tester.tap(find.byKey(const ValueKey('kss-5')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('fatigue-start')));
    await tester.pumpAndSettle();
    expect(sink.sent.single['event'], 'session_start');
    expect(find.text('작업 중 · 프로필 B'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('fatigue-sleepy')));
    await tester.pump();
    expect(sink.sent.last['event'], 'sleepy');

    await tester.tap(find.byKey(const ValueKey('fatigue-end')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('kss-7')).last);
    await tester.pumpAndSettle();
    expect(sink.sent.last['event'], 'session_end');
    expect(sink.sent.last['kss'], 7);
    expect(tester.takeException(), isNull);
  });
}
