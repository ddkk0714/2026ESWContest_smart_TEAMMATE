import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/pr56/focus_report.dart';
import 'package:deskmate_ui_demo/pr56_workspace_view.dart';

DemoModel model() => DemoModel(
    DemoConfig(jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>),
    presentationEnabled: true);

void main() {
  test('hub report counts only what came after the start point', () {
    const start =
        LiveReport(durationS: 600, fatigueEpisodes: 3, interventions: 5);
    const now =
        LiveReport(durationS: 900, fatigueEpisodes: 4, interventions: 7);
    final r = now.since(start);
    expect(r.durationS, 300);
    expect(r.fatigueEpisodes, 1);
    expect(r.interventions, 2);
    // 기준점이 없으면 그대로
    expect(now.since(null).fatigueEpisodes, 4);
    // hub 가 다시 켜져 값이 줄었으면 지금 값
    expect(
        const LiveReport(fatigueEpisodes: 1).since(start).fatigueEpisodes, 1);
  });

  test('focus start and profile switch clear the report', () {
    final m = model();
    var resets = 0;
    m.onReportReset = () => resets++;
    m.start();
    expect(resets, 1);
    m.fatigue();
    m.recordFeeling('조금 피곤해요');
    m.finish();
    expect(m.page, DemoPage.report);
    expect(m.fatigueEvents, 1);
    // 다음 집중은 0 부터
    m.start();
    expect(resets, 2);
    expect(m.fatigueEvents, 0);
    expect(m.feeling, isEmpty);
    m.fatigue();
    m.finish();
    // 프로필을 바꾸면 지난 사람의 리포트를 비우고 홈으로
    m.recordFeeling('괜찮았어요');
    m.selectProfile(1);
    expect(resets, 3);
    expect(m.page, DemoPage.home);
    expect(m.fatigueEvents, 0);
    expect(m.sessionSeconds, 0);
    expect(m.feeling, isEmpty);
    expect(m.events, isEmpty);
  });

  test('focus record keeps its profile id in json', () {
    final r = FocusReport(
        startedAt: DateTime(2026, 11, 4, 9),
        endedAt: DateTime(2026, 11, 4, 9, 25),
        timerSeconds: 1500,
        demo: false,
        profileId: 'p-a');
    expect(FocusReport.parse(r.toJson())!.profileId, 'p-a');
    final old = Map.of(r.toJson())..remove('profileId');
    expect(FocusReport.parse(old)!.profileId, isNull);
  });

  testWidgets('focus records are shown per profile', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model()..now = DateTime(2026, 11, 4, 12);
    final a = m.profiles[0].profileId, b = m.profiles[1].profileId;
    final session = WorkspaceDemoSession(memoryOnly: true);
    FocusReport done(int minutes, String? id) => FocusReport(
        startedAt: DateTime(2026, 11, 4, 9),
        endedAt: DateTime(2026, 11, 4, 10),
        timerSeconds: minutes * 60,
        demo: false,
        profileId: id);
    // 예전 기록(id 없음) 하나 + 다른 프로필 기록 하나
    session.store.preferences.focusReports = [done(25, null), done(50, b)];
    Widget view() => MaterialApp(
        home: Scaffold(
            body: Pr56WorkspaceView(
                model: m,
                session: session,
                onExit: () {},
                onPreferences: () {})));
    await tester.pumpWidget(view());
    await tester.pump();
    // 첫 프로필: 예전 기록을 가져오고 다른 프로필 기록은 안 보인다
    expect(find.text('오늘 25 min · 1회 완료'), findsOneWidget);
    expect(session.store.preferences.focusReports.first.profileId, a);
    m.selectProfile(1);
    await tester.pumpWidget(view());
    await tester.pump();
    expect(find.text('오늘 50 min · 1회 완료'), findsOneWidget);
    expect(find.text('오늘 25 min · 1회 완료'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
