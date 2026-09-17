import 'package:deskmate_ui_env/deskmate_theme.dart';
import 'package:deskmate_ui_env/display_state.dart';
import 'package:deskmate_ui_env/sensor_overview_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('shows the four current environment sensor values',
      (tester) async {
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
      _state(
        co2Ppm: null,
        temperatureC: null,
        humidityPct: null,
        lux: null,
      ),
    );

    expect(find.text('-- ppm'), findsOneWidget);
    expect(find.text('-- °C'), findsOneWidget);
    expect(find.text('-- %'), findsOneWidget);
    expect(find.text('-- lx'), findsOneWidget);
    expect(tester.takeException(), isNull);
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
      co2Ppm: co2Ppm,
      temperatureC: temperatureC,
      humidityPct: humidityPct,
      lux: lux,
    );
