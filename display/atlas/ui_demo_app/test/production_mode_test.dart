import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/pr56/workspace_shell.dart';
import 'package:deskmate_ui_demo/pr56/music_playback.dart';
import 'package:deskmate_ui_demo/pr56_workspace_view.dart';

DemoModel production() => DemoModel(DemoConfig(
    jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>));

void main() {
  test('default session uses real seconds and cannot run synthetic scenarios',
      () {
    final m = production();
    expect(m.profiles.length, 1);
    expect(m.persona.rounds, 0);
    expect(m.presentationEnabled, isFalse);
    expect(m.showFocusGoal, isFalse);
    m.beginScenario();
    for (var i = 0; i < 10; i++) {
      m.tick();
    }
    expect(m.active, isFalse);
    expect(m.proposal, isFalse);
    m.start();
    for (var i = 0; i < 3; i++) {
      m.tick();
    }
    expect(m.sessionSeconds, 3);
    m.fatigue();
    m.away();
    expect(m.fatigueEvents, 0);
    expect(m.absent, isFalse);
    expect(m.posture, '연결 대기');
  });

  test('presentation options require opt-in and never survive a fresh model',
      () {
    final m = production()..start();
    m.configurePresentation(enabled: false, speed: 60, goal: true);
    m.tick();
    expect(m.sessionSeconds, 1);
    expect(m.showFocusGoal, isFalse);
    m.configurePresentation(enabled: true, speed: 10, goal: true);
    m.tick();
    expect(m.sessionSeconds, 11);
    m.configurePresentation(enabled: false);
    m.tick();
    expect(m.sessionSeconds, 12);
    expect(m.showFocusGoal, isFalse);
    expect(production().timeScale, 1);
    m.liveSession = true;
    m.configurePresentation(enabled: true, speed: 60);
    m.tick();
    expect(m.sessionSeconds, 72);
  });

  test('workspace has no pretend audio playback or fabricated timeline', () {
    final session = WorkspaceDemoSession(memoryOnly: true);
    final m = production()..start();
    session.syncPreferences(m);
    expect(session.music.isPlaying, isFalse);
    expect(session.music is MusicTimeline, isFalse);
    expect(() => session.music.toggle(), throwsStateError);
    session.dispose();
  });

  testWidgets(
      'ordinary screens show unknown sensors and presentation is explicitly enabled',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = production();
    var exits = 0;
    await tester.pumpWidget(DemoApp(model: m, ticking: false, onExit: () => exits++));
    expect(find.textContaining('720'), findsNothing);
    expect(find.textContaining('24.2'), findsNothing);
    expect(find.text('바른 자세'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('status-공기')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 700));
    await tester.pump(const Duration(milliseconds: 700));
    expect(
        tester.widget<DeskmateWorkspace>(find.byType(DeskmateWorkspace)).demo,
        isFalse);
    expect(find.byKey(const ValueKey('home-focus-goal')), findsNothing);
    m.start();
    m.show(DemoPage.details);
    await tester.pumpAndSettle();
    expect(find.textContaining('목표'), findsNothing);
    expect(find.textContaining('배속'), findsNothing);
    await tester.tap(find.byTooltip('발표 조작창'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('presentation-panel')), findsOneWidget);
    expect(find.byType(BottomSheet), findsNothing);
    expect(m.preferencesOpen, isTrue);
    expect(find.text('재실·작업 상황 재생'), findsNothing);
    expect(find.text('피로·졸음 상황'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('presentation-enabled')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('10배속'));
    await tester.tap(find.byKey(const ValueKey('presentation-goal')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('닫기'));
    await tester.pumpAndSettle();
    expect(exits, 0);
    expect(m.preferencesOpen, isFalse);
    expect(find.byKey(const ValueKey('presentation-panel')), findsNothing);
    expect(find.text('목표 25분 · 10배속'), findsOneWidget);
    m.tick();
    expect(m.sessionSeconds, 10);
    m.configurePresentation(enabled: false);
    m.finish();
    await tester.pumpAndSettle();
    expect(find.text('기록 없음'), findsOneWidget);
    expect(find.text('조명 OFF'), findsNothing);
    expect(find.text('조명 상태 미확인'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
