import 'package:deskmate_display/deskmate_theme.dart';
import 'package:deskmate_display/session_report.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _envelope(
        {Map<String, dynamic>? metrics, double duration = 100}) =>
    {
      'schema_version': '1.0',
      'ts': 1790854000.0,
      'node': 'hub',
      'data': {
        't_start': 1790853900.0,
        't_end': 1790853900.0 + duration,
        'duration_s': duration,
        'focus_time_s': 40.0,
        'focus_ratio': 0.4,
        'fatigue_episodes': [
          {'t_onset': 1.0, 'peak_fatigue': 0.8, 't_resolved': 2.0}
        ],
        'intervention_counts': {'total': 2, 'recovered': 1},
        'break_accept_rate': null,
        if (metrics != null) 'metrics': metrics,
        'state_durations_s': {'FOCUS_PC': 40.0, 'IDLE': 60.0},
      },
    };

void main() {
  test('parses hub metrics and keeps null as unknown', () {
    final r = SessionReport.fromEnvelope(_envelope(metrics: {
      'suggest_accept_rate': 0.5,
      'timeout_rate': null,
      'auto_undo_rate': 1.0,
      'correction_count': 2,
      'median_response_ms': 4200,
      'recovery_time_s': 30,
    }));
    expect(r.metrics.suggestAcceptRate, 0.5);
    expect(r.metrics.timeoutRate, isNull);
    expect(r.metrics.autoUndoRate, 1.0);
    expect(r.metrics.correctionCount, 2);
    expect(r.metrics.medianResponseMs, 4200);
    expect(r.metrics.recoveryTimeSeconds, 30);
    // 옛 hub(지표 없음)도 읽는다
    expect(SessionReport.fromEnvelope(_envelope()).metrics.correctionCount, 0);
  });

  testWidgets('card shows seconds, last update and the response metrics',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final report =
        SessionReport.fromEnvelope(_envelope(duration: 100, metrics: {
      'suggest_accept_rate': 0.5,
      'timeout_rate': null,
      'auto_undo_rate': 0.0,
      'correction_count': 1,
      'median_response_ms': 4200,
      'recovery_time_s': null,
    }));
    await tester.pumpWidget(MaterialApp(
        theme: buildDeskmateTheme(),
        home: Scaffold(body: SessionReportCard(report: report))));

    // 10초마다 오는 리포트가 화면에서도 움직이도록 10분 미만은 초까지
    expect(find.textContaining('1분 40초'), findsOneWidget);
    expect(find.textContaining('마지막 갱신'), findsOneWidget);
    expect(find.text('40초'), findsOneWidget); // 몰입 시간
    expect(find.text('제안 수용률'), findsOneWidget);
    expect(find.text('50%'), findsOneWidget);
    expect(find.text('0%'), findsOneWidget); // 자동 실행 되돌림
    expect(find.text('1회'), findsWidgets);
    expect(find.text('4.2초'), findsOneWidget);
    expect(find.text('--'), findsWidgets); // 무응답률·회복까지·휴식 수락률
    expect(tester.takeException(), isNull);
  });
}
