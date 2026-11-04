import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/pr56/display_state.dart';
import 'package:deskmate_ui_demo/report/month_chart.dart';
import 'package:deskmate_ui_demo/report/session_timeline.dart';
import 'live_test.dart' show FakeTransport, phase;

DemoModel model() => DemoModel(DemoConfig(
    jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>));

Map<String, Object?> command(String target, String operation, Object? value,
        {String gate = 'auto', String cause = 'posture'}) =>
    {
      'target_id': target,
      'operation': operation,
      'value': value,
      'gate': gate,
      'cause': cause
    };

void main() {
  test('command labels for the chart', () {
    expect(
        commandLabel(
            command('desk_lamp', 'adjust_brightness', {'percent': 60})),
        '무드등 밝게 +60 %');
    expect(commandLabel(command('music', 'set_music', {'on': true})), '음악 켜기');
    expect(commandLabel(command('vent_fan', 'set_power', 'on')), '선풍기 켜기');
    expect(
        commandLabel(command('desk_lamp', 'set_power', 'off', cause: 'absent')),
        '자리 비움 · 끄기');
    expect(
        commandLabel(command('desk_lamp', 'set_brightness', 40, gate: 'undo')),
        '되돌리기');
    expect(commandLabel(command('unknown', 'x', 1)), isNull);
  });

  test('commands sent together become one mark', () {
    var now = DateTime(2026, 11, 5, 10);
    final t = SessionTimeline(clock: () => now);
    t.addCommand(command('music', 'set_music', {'on': true}));
    now = now.add(const Duration(seconds: 1));
    t.addCommand(command('desk_lamp', 'adjust_brightness', {'percent': 60}));
    now = now.add(const Duration(seconds: 30));
    t.addCommand(command('vent_fan', 'set_power', 'on'));
    expect(t.events.map((e) => e.label), ['음악 켜기 · 무드등 밝게 +60 %', '선풍기 켜기']);
  });

  test('the model records only during focus and clears on a new focus', () {
    final m = model();
    DisplayState s(String fsm, {String posture = 'upright'}) =>
        DisplayState.fromEnvelope(phase(fsm, summary: {
          'present': true,
          'tof': {'posture': posture}
        }));
    m.followLive(s('IDLE', posture: 'upright'));
    m.recordCommand(command('music', 'set_music', {'on': true}));
    expect(m.timeline.samples, isEmpty);
    expect(m.timeline.events, isEmpty);
    m.followLive(s('FOCUS_PC')); // 자동 시작
    expect(m.active, isTrue);
    m.followLive(s('FATIGUE', posture: 'face_down'));
    m.recordCommand(command('music', 'set_music', {'on': true}));
    expect(m.timeline.samples, hasLength(2));
    expect(m.timeline.samples.last.drowsyEvidence, isTrue);
    expect(m.timeline.events, hasLength(1));
    m.followLive(s('END'));
    expect(m.page, DemoPage.report);
    expect(m.timeline.samples, hasLength(3), reason: '리포트가 볼 기록은 남는다');
    m.followLive(s('FOCUS_PC'));
    expect(m.timeline.samples, hasLength(1), reason: '새 집중은 새로 센다');
    expect(m.timeline.events, isEmpty);
  });

  test('30-day sample follows the help order and learns from undos', () {
    final m = model();
    final persona = m.persona..order = ['알림', '조명', '선풍기'];
    final days = sampleMonth(persona);
    expect(days, hasLength(30));
    expect(sampleMonth(persona).map((d) => d.total), days.map((d) => d.total),
        reason: '같은 사람은 같은 그림');
    int undos(int a, int b) =>
        days.sublist(a, b).fold(0, (s, d) => s + d.undos);
    expect(undos(15, 30), lessThan(undos(0, 15)));
    int used(String k, int a, int b) =>
        days.sublist(a, b).fold(0, (s, d) => s + (d.counts[k] ?? 0));
    expect(used('알림', 0, 30), greaterThan(used('선풍기', 0, 30)));
  });

  testWidgets('focus end shows the flow chart, the next tab the 30 days',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    late FakeTransport transport;
    final link = LiveLink(
        transportFactory: (h, p) => transport = FakeTransport(),
        retryDelay: const Duration(hours: 1));
    await tester.pumpWidget(DemoApp(model: m, ticking: false, live: link));
    link.configure('192.168.0.10', 1883);
    await tester.pump();
    Future<void> state(String fsm, String posture, double fatigue) async {
      transport.deliver(
          LiveLink.stateTopic,
          phase(fsm, fatigue: fatigue, summary: {
            'present': true,
            'co2_ppm': 720,
            'tof': {'posture': posture},
            'mmwave': {'motion_level': 12}
          }));
      await tester.pump();
    }

    await state('FOCUS_PC', 'upright', .2);
    await state('FATIGUE', 'face_down', .8);
    transport.deliver(LiveLink.commandTopic, {
      'data': command('desk_lamp', 'adjust_brightness', {'percent': 60})
    });
    await tester.pump();
    await state('RECOVERY', 'upright', .3);
    await state('END', 'upright', .2);
    await tester.pumpAndSettle();
    expect(m.page, DemoPage.report);
    expect(find.byKey(const ValueKey('session-chart')), findsOneWidget);
    expect(find.textContaining('무드등 밝게 +60 %'), findsOneWidget);
    expect(find.text('오늘 피곤했나요?'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('report-tab-1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('month-report')), findsOneWidget);
    expect(find.byKey(const ValueKey('month-footnote')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });
}
