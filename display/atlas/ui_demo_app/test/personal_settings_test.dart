import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/pr56_workspace_view.dart';

DemoModel model() => DemoModel(
    DemoConfig(jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>),
    presentationEnabled: true);
void main() {
  test('profile preferences apply to session and remain separate per person',
      () {
    final m = model();
    final draft = m.persona.copy();
    draft.preferences
      ..brightness = .4
      ..warmth = .7
      ..fanEnabled = true
      ..fanSpeed = 2
      ..fatigueBrightness = .8
      ..fatigueWarmth = .9
      ..fatigueFanSpeed = 1
      ..startNotice = false;
    expect(m.persona.preferences.brightness, .85);
    m.updateProfile(draft);
    m.start();
    expect(m.brightness, .4);
    expect(m.warmth, .7);
    expect(m.fanOn, true);
    expect(m.fanSpeed, 2);
    expect(m.notice, isEmpty);
    m.fatigue();
    expect(m.brightness, .8);
    expect(m.warmth, .9);
    m.fatigue();
    expect(m.fanSpeed, 1);
    m.finish();
    m.selectProfile(1);
    expect(m.persona.preferences.brightness, .45);
    m.selectProfile(0);
    expect(m.persona.preferences.brightness, .4);
  });
  test('automatic choices can be disabled without bypassing confirmation', () {
    final m = model();
    m.persona.preferences
      ..autoStart = false
      ..autoAdjust = false;
    m.beginScenario();
    for (int i = 0; i < 10; i++) {
      m.tick();
    }
    expect(m.proposal, true);
    expect(m.active, false);
    m.start();
    m.fatigue();
    expect(m.pendingAction, '조명');
    expect(m.brightness, .85);
    m.feedback(true);
    expect(m.brightness, .95);
  });
  test('music and widget focus goal follow saved profile choices', () async {
    final m = model();
    final session = WorkspaceDemoSession(memoryOnly: true);
    m.persona.preferences
      ..volume = .65
      ..focusMinutes = 45
      ..musicEnabled = true;
    m.start();
    session.syncPreferences(m);
    expect(session.music.isPlaying, false);
    expect(session.music.volume, 0);
    expect(session.store.preferences.focusMinutes, 45);
    final draft = m.persona.copy();
    draft.preferences.musicEnabled = false;
    m.updateProfile(draft);
    session.syncPreferences(m);
    expect(session.music.isPlaying, false);
    await session.music.dispose();
  });
  testWidgets(
      'personal settings saves, cancels and is reachable from profiles and widgets',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false));
    await tester.tap(find.byTooltip('나의 책상 설정'));
    await tester.pumpAndSettle();
    expect(m.preferencesOpen, true);
    tester
        .widget<Slider>(find.byKey(const ValueKey('preference-brightness')))
        .onChanged!(.4);
    await tester.pump();
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(m.persona.preferences.brightness, .85);
    expect(m.preferencesOpen, false);
    await tester.tap(find.text('지우 · 수험생'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('나의 책상 설정'));
    await tester.pumpAndSettle();
    tester
        .widget<Slider>(find.byKey(const ValueKey('preference-brightness')))
        .onChanged!(.4);
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('settings-tab-1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('preference-music-enabled')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('settings-tab-2')));
    await tester.pumpAndSettle();
    tester
        .widget<Slider>(find.byKey(const ValueKey('preference-focus-minutes')))
        .onChanged!(45);
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('save-personal-settings')));
    await tester.pumpAndSettle();
    expect(m.persona.preferences.brightness, .4);
    expect(m.persona.preferences.musicEnabled, false);
    expect(m.persona.preferences.focusMinutes, 45);
    await tester.tap(find.byKey(const ValueKey('status-공기')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 600));
    await tester.tap(find.byTooltip('나의 책상 설정'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 600));
    for (int i = 0; i < 40; i++) {
      m.tick();
    }
    expect(m.page, DemoPage.widgets);
    await tester.tap(find.byTooltip('설정 닫기'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    expect(m.preferencesOpen, false);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
