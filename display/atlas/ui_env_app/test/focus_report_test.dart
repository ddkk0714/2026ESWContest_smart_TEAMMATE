import 'package:deskmate_display/focus_report.dart';
import 'package:deskmate_display/focus_report_dialog.dart';
import 'package:deskmate_display/workspace_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final end = DateTime(2026, 10, 18, 13);
  FocusReport report(bool demo) => FocusReport(
      startedAt: end.subtract(const Duration(minutes: 25)),
      endedAt: end,
      timerSeconds: 1500,
      demo: demo);
  test('local reports round-trip; sensor evaluation stays unknown outside demo',
      () {
    final prefs = WorkspacePreferences()
      ..focusReports = [report(false), report(true)];
    final restored = WorkspacePreferences.fromJson(prefs.toJson());
    expect(restored.focusReports.length, 2);
    expect(restored.focusReports.first.focusedSeconds, isNull);
    expect(restored.focusReports.first.posturePercent, isNull);
    expect(restored.focusReports.last.focusedSeconds, 1260);
    expect(WorkspacePreferences.fromJson({}).focusReports, isEmpty);
    expect(FocusReport.parse({'timerSeconds': -1}), isNull);
    final bad = report(false).toJson()..['timerSeconds'] = 10801;
    expect(FocusReport.parse(bad), isNull);
    final large = WorkspacePreferences()
      ..focusReports = List.filled(205, report(true));
    expect(
        WorkspacePreferences.fromJson(large.toJson()).focusReports.length, 200);
    expect(sameFocusDay(end, DateTime(2026, 10, 19)), isFalse);
  });
  for (final size in [
    const Size(1024, 600),
    const Size(800, 600),
    const Size(320, 800)
  ]) {
    for (final demo in [false, true]) {
      testWidgets('report fits $size demo=$demo and stays until dismissed',
          (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        await tester.pumpWidget(MaterialApp(
            home: Builder(
                builder: (context) => Scaffold(
                    body: TextButton(
                        onPressed: () => showDialog<bool>(
                            context: context,
                            builder: (_) => FocusReportDialog(
                                report: report(demo), today: [report(demo)])),
                        child: const Text('open'))))));
        await tester.tap(find.text('open'));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 400));
        expect(
            find.byKey(const ValueKey('focus-report-dialog')), findsOneWidget);
        if (demo) {
          expect(find.text('21 min'), findsOneWidget);
          expect(find.text('바른 자세 78%'), findsOneWidget);
        } else {
          expect(find.text('분석 연결 대기'), findsNWidgets(3));
          expect(find.textContaining('84%'), findsNothing);
        }
        await tester.pump(const Duration(seconds: 10));
        expect(
            find.byKey(const ValueKey('focus-report-dialog')), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.tapAt(const Offset(2, 2));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 400));
        expect(find.byKey(const ValueKey('focus-report-dialog')), findsNothing);
        await tester.pumpWidget(const SizedBox());
      });
    }
  }
}
