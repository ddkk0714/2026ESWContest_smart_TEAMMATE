import 'package:deskmate_display/developer_page.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/intervention.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

DisplayState _base() => DisplayState(
      fsmState: 'FOCUS_PC',
      phase: 'focus',
      context: 'pc',
      focus: .1,
      fatigue: .1,
      confidence: .1,
      gate: 'none',
      reasons: const [],
      sequence: 7,
      timestamp: DateTime(2026, 10, 1, 21),
      co2Ppm: 650,
      temperatureC: 24.0,
      humidityPct: 45.0,
      lux: 420,
    );

void main() {
  group('buildOverrideState', () {
    test('phase table matches the hub and gate follows the preset', () {
      final auto = buildOverrideState('ACTION_ENV', _base(), gate: 'auto');
      expect(auto.phase, 'fatigue');
      expect(auto.gate, 'auto');
      expect(auto.cause, 'environment');
      expect(auto.fatigue, greaterThanOrEqualTo(0.75));
      // 18상태 칩처럼 gate 를 안 주면 ACTION_* 는 제안
      expect(buildOverrideState('ACTION_BREAK', _base()).gate, 'suggest');
      expect(buildOverrideState('ACTION_BREAK', _base()).cause, 'cognitive');
      expect(buildOverrideState('IDLE', _base()).gate, 'none');
      expect(kFsmPhaseByState.length, 18);
    });

    test('environment flags set values that make the reason text read right',
        () {
      final s = buildOverrideState('ACTION_ENV', _base(),
          gate: 'auto', envFlags: {'too_dark', 'co2_high', 'too_hot'});
      // 플래그 순서는 표 순서로 고정
      expect(s.envFlags, ['co2_high', 'too_hot', 'too_dark']);
      expect(s.lux, 80);
      expect(s.temperatureC, 28.6);
      expect(interventionReason(s),
          'CO₂가 높아요 (1280 ppm) · 방이 더워요 (28.6°C) · 조명이 어두워요 (80 lx)');
      // 고르지 않은 값은 지금 값 그대로
      expect(s.humidityPct, 45.0);
    });
  });

  group('DeveloperPage', () {
    Future<void> pump(WidgetTester tester,
        {bool overridden = false,
        Set<String> flags = const {},
        ValueChanged<DevPreset>? onPreset,
        ValueChanged<Set<String>>? onFlags,
        VoidCallback? onClear}) async {
      tester.view.physicalSize = const Size(1280, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: DeveloperPage(
            state: _base(),
            overridden: overridden,
            activePreset: null,
            envFlags: flags,
            onPreset: onPreset ?? (_) {},
            onEnvFlags: onFlags ?? (_) {},
            onClear: onClear ?? () {},
            preview: const Center(child: Text('preview')),
          ),
        ),
      ));
    }

    testWidgets('every screen has a button and the preview is shown',
        (tester) async {
      await pump(tester);
      for (final p in [
        ...devPhaseScreens,
        ...devAutoNotices,
        ...devSuggestions
      ]) {
        expect(find.byKey(ValueKey('dev-${p.key}')), findsOneWidget,
            reason: p.key);
      }
      expect(find.text('preview'), findsOneWidget);
      expect(find.byKey(const ValueKey('dev-clear')), findsNothing);
    });

    testWidgets('pressing a button reports that preset', (tester) async {
      final pressed = <DevPreset>[];
      await pump(tester, onPreset: pressed.add);
      await tester.tap(find.byKey(const ValueKey('dev-auto-posture')));
      await tester.tap(find.byKey(const ValueKey('dev-suggest-env')));
      expect(pressed.map((p) => (p.fsmState, p.gate, p.pending)), [
        ('ACTION_POSTURE', 'auto', false),
        ('ACTION_ENV', 'suggest', true),
      ]);
    });

    testWidgets('18-state chips and env flag chips', (tester) async {
      final pressed = <DevPreset>[];
      Set<String>? flags;
      await pump(tester,
          flags: const {'too_hot'},
          onPreset: pressed.add,
          onFlags: (f) => flags = f);
      final list = find.descendant(
          of: find.byType(DeveloperPage), matching: find.byType(Scrollable));
      await tester.scrollUntilVisible(
          find.byKey(const ValueKey('override-ESCALATE')), 120,
          scrollable: list.first);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('override-ESCALATE')));
      expect(pressed.single.fsmState, 'ESCALATE');
      await tester.scrollUntilVisible(
          find.byKey(const ValueKey('dev-flag-too_dark')), -120,
          scrollable: list.first);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('dev-flag-too_dark')));
      expect(flags, {'too_hot', 'too_dark'});
    });

    testWidgets('live button appears only while overridden', (tester) async {
      var cleared = 0;
      await pump(tester, overridden: true, onClear: () => cleared++);
      await tester.tap(find.byKey(const ValueKey('dev-clear')));
      expect(cleared, 1);
    });
  });
}
