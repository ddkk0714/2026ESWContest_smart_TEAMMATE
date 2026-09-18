import 'package:deskmate_display/deskmate_theme.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/sensor_overview_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('shows the current environment sensor values', (tester) async {
    await _pump(tester, _state());

    expect(find.text('센서 전체'), findsOneWidget);
    expect(find.text('CO₂'), findsOneWidget);
    expect(find.text('694 ppm'), findsOneWidget);
    expect(find.text('25.1 °C'), findsOneWidget);
    expect(find.text('49.9 %'), findsOneWidget);
    expect(find.text('298 lx'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('uses placeholders when environment values are missing',
      (tester) async {
    await _pump(
      tester,
      _state(co2Ppm: null, temperatureC: null, humidityPct: null, lux: null),
    );

    expect(find.text('-- ppm'), findsOneWidget);
    expect(find.text('-- °C'), findsOneWidget);
    expect(find.text('-- %'), findsOneWidget);
    expect(find.text('-- lx'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows mmWave vitals and the firmware drowsy verdict',
      (tester) async {
    await _pump(tester, _state());

    expect(find.text('72 bpm'), findsOneWidget);
    expect(find.text('16 회/분'), findsOneWidget);
    expect(find.text('55 cm'), findsOneWidget);
    expect(find.textContaining('각성'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  // 락온이 풀리면 심박은 값 자체가 안 온다. 0 으로 그리면 "심박 0" 으로 읽힌다.
  testWidgets('leaves vitals blank instead of showing zero when unlocked',
      (tester) async {
    await _pump(
      tester,
      _state(
        mmwave: const MmwaveSummary(
          motionLevel: 3,
          distanceCm: 55,
          drowsyState: 'NOLOCK',
        ),
      ),
    );

    expect(find.text('-- bpm'), findsOneWidget);
    expect(find.text('-- 회/분'), findsOneWidget);
    expect(find.textContaining('판정 불가'), findsOneWidget);
  });

  testWidgets('says so when no mmWave sample is in the summary',
      (tester) async {
    await _pump(tester, _state(mmwave: null));

    expect(find.text('수신 없음'), findsOneWidget);
    expect(find.text('-- bpm'), findsOneWidget);
  });

  test('drops a vital whose *_valid flag is false', () {
    final summary = MmwaveSummary.fromJson(const {
      'heart_bpm': 88,
      'heart_valid': false,
      'resp_bpm': 16,
      'resp_valid': true,
      'drowsy_state': 'AWAKE',
    });

    expect(summary!.heartBpm, isNull);
    expect(summary.respBpm, 16);
  });
}

Future<void> _pump(WidgetTester tester, DisplayState state) async {
  tester.view.physicalSize = const Size(1024, 600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      theme: buildDeskmateTheme(),
      home: Scaffold(body: SensorOverviewPage(state: state)),
    ),
  );
  await tester.pump();
}

DisplayState _state({
  int? co2Ppm = 694,
  double? temperatureC = 25.1,
  double? humidityPct = 49.9,
  int? lux = 298,
  MmwaveSummary? mmwave = const MmwaveSummary(
    motionState: 'still',
    motionLevel: 3,
    distanceCm: 55,
    respBpm: 16,
    heartBpm: 72,
    drowsyState: 'AWAKE',
  ),
}) =>
    DisplayState(
      fsmState: 'IDLE',
      phase: 'idle',
      context: 'none',
      focus: 0,
      fatigue: 0,
      confidence: .8,
      gate: 'none',
      reasons: const [],
      sequence: 1,
      timestamp: DateTime(2026, 9, 18),
      present: true,
      co2Ppm: co2Ppm,
      temperatureC: temperatureC,
      humidityPct: humidityPct,
      lux: lux,
      mmwave: mmwave,
    );
