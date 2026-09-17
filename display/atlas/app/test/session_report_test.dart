import 'package:deskmate_display/deskmate_theme.dart';
import 'package:deskmate_display/session_report.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final envelope = <String, dynamic>{
    'schema_version': '1.0',
    'ts': 1769003600.0,
    'node': 'hub',
    'boot_id': 'test',
    'seq': 10,
    'data': {
      't_start': 1769000000.0,
      't_end': 1769003600.0,
      'duration_s': 3600.0,
      'focus_time_s': 2100.0,
      'focus_ratio': 0.583,
      'state_durations_s': {'FOCUS_PC': 1800.0, 'REST': 300.0},
      'fatigue_episodes': [
        {'t_onset': 1769001800.0, 'peak_fatigue': 0.81}
      ],
      'intervention_counts': {'total': 2, 'recovered': 1},
      'break_accept_rate': 0.5,
    },
  };

  test('parses session report envelope', () {
    final report = SessionReport.fromEnvelope(envelope);
    expect(report.durationSeconds, 3600);
    expect(report.focusRatio, closeTo(0.583, 0.001));
    expect(report.fatigueEpisodes, 1);
    expect(report.interventionTotal, 2);
    expect(report.stateDurations['FOCUS_PC'], 1800);
  });

  testWidgets('renders a compact session report card', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: buildDeskmateTheme(),
      home: Scaffold(body: SessionReportCard(report: SessionReport.fromEnvelope(envelope))),
    ));
    expect(find.byKey(const ValueKey('session-report-card')), findsOneWidget);
    expect(find.text('세션 리포트'), findsOneWidget);
    expect(find.text('58%'), findsOneWidget);
    expect(find.byKey(const ValueKey('state-duration-chart')), findsOneWidget);
    expect(find.text('FOCUS_PC'), findsOneWidget);
    expect(find.text('REST'), findsOneWidget);
    expect(find.textContaining('50% ('), findsOneWidget);
    expect(find.textContaining('8% ('), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}