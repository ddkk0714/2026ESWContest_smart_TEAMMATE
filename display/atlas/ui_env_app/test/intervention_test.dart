import 'package:deskmate_display/dashboard_view.dart';
import 'package:deskmate_display/deskmate_theme.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/intervention.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _t0 = DateTime(2026, 10, 1, 14);
DateTime _at(int seconds) => _t0.add(Duration(seconds: seconds));

DisplayState _state({
  String fsmState = 'FOCUS_PC',
  String phase = 'focus',
  String gate = 'none',
  String? cause,
  List<String> envFlags = const [],
  int? co2 = 1180,
  double? temp = 28.0,
  double? humidity = 22.0,
  int? lux = 150,
  double confidence = 0.82,
}) =>
    DisplayState(
      fsmState: fsmState,
      phase: phase,
      context: 'pc',
      focus: 0.3,
      fatigue: 0.7,
      confidence: confidence,
      gate: gate,
      cause: cause,
      reasons: const [],
      sequence: 1,
      timestamp: _t0,
      co2Ppm: co2,
      temperatureC: temp,
      humidityPct: humidity,
      lux: lux,
      envFlags: envFlags,
    );

void main() {
  group('reason text', () {
    test('environment flags become readable reasons with values', () {
      final reason = interventionReason(_state(
          cause: 'environment',
          envFlags: const ['co2_high', 'too_hot', 'too_dry', 'too_dark']));
      expect(
          reason,
          'CO₂가 높아요 (1180 ppm) · 방이 더워요 (28.0°C) · '
          '공기가 건조해요 (22%) · 조명이 어두워요 (150 lx)');
    });

    test('unknown flags are skipped and no flags fall back to CO₂', () {
      expect(
          interventionReason(
              _state(cause: 'environment', envFlags: const ['mystery'])),
          contains('CO₂ 1180 ppm'));
      expect(interventionReason(_state(cause: 'environment', co2: null)),
          '환경이 쾌적 범위를 벗어났어요');
    });

    test('other causes and confidence', () {
      expect(interventionReason(_state(cause: 'cognitive')), contains('입력 리듬'));
      expect(interventionReason(_state(cause: 'posture')), contains('졸음'));
      expect(confidenceLabel(_state(confidence: 0.816)), '확신도 82%');
    });

    test('display state parses env_flags from the hub summary', () {
      final state = DisplayState.fromEnvelope({
        'schema_version': '1.0',
        'ts': 1.0,
        'seq': 1,
        'data': {
          'fsm_state': 'ACTION_ENV',
          'phase': 'fatigue',
          'context': 'pc',
          'sensor_summary': {
            'env_flags': ['co2_rising', 'too_dark'],
          },
        },
      });
      expect(state.envFlags, ['co2_rising', 'too_dark']);
    });
  });

  group('InterventionTracker — auto notice', () {
    final auto =
        _state(fsmState: 'ACTION_ENV', gate: 'auto', cause: 'environment');

    test('appears on entering an automatic action', () {
      final tracker = InterventionTracker();
      tracker.update(auto, hasPendingRequest: false, now: _t0);
      expect(tracker.autoNotice?.title, '환경을 조정했어요');
      expect(tracker.autoNotice?.reason, contains('CO₂'));
    });

    test('suggest gate never shows the auto notice', () {
      final tracker = InterventionTracker();
      tracker.update(_state(fsmState: 'ACTION_ENV', gate: 'suggest'),
          hasPendingRequest: true, now: _t0);
      expect(tracker.autoNotice, isNull);
    });

    test('a dismissed notice stays dismissed for the same episode', () {
      final tracker = InterventionTracker();
      tracker.update(auto, hasPendingRequest: false, now: _t0);
      tracker.dismissAuto();
      tracker.update(auto, hasPendingRequest: false, now: _at(10));
      expect(tracker.autoNotice, isNull);
    });

    test('stays 30 s after the action ends, then clears', () {
      final tracker = InterventionTracker();
      tracker.update(auto, hasPendingRequest: false, now: _t0);
      final monitor = _state(fsmState: 'MONITOR');
      tracker.update(monitor, hasPendingRequest: false, now: _at(10));
      expect(tracker.autoNotice, isNotNull);
      tracker.update(monitor, hasPendingRequest: false, now: _at(40));
      expect(tracker.autoNotice, isNotNull);
      tracker.update(monitor, hasPendingRequest: false, now: _at(41));
      expect(tracker.autoNotice, isNull);
    });

    test('re-entering after it ended is a new notice', () {
      final tracker = InterventionTracker();
      tracker.update(auto, hasPendingRequest: false, now: _t0);
      tracker.dismissAuto();
      tracker.update(_state(fsmState: 'MONITOR'),
          hasPendingRequest: false, now: _at(10));
      tracker.update(auto, hasPendingRequest: false, now: _at(20));
      expect(tracker.autoNotice, isNotNull);
      expect(tracker.autoNotice?.startedAt, _at(20));
    });
  });

  group('InterventionTracker — suggestion timing', () {
    test('uses expires_in_s, reports remaining and response time', () {
      final tracker = InterventionTracker();
      tracker.update(_state(),
          hasPendingRequest: true, expiresInS: 30, now: _t0);
      expect(tracker.remaining(_at(12)), const Duration(seconds: 18));
      expect(tracker.responseMs(_at(4)), 4000);
    });

    test('times out once, then never again for the same card', () {
      final tracker = InterventionTracker();
      tracker.update(_state(),
          hasPendingRequest: true, expiresInS: 30, now: _t0);
      expect(tracker.takeTimeout(_at(29)), isFalse);
      expect(tracker.takeTimeout(_at(30)), isTrue);
      expect(tracker.takeTimeout(_at(31)), isFalse);
      expect(tracker.remaining(_at(40)), Duration.zero);
    });

    test('falls back to 60 s and clears when the question goes away', () {
      final tracker = InterventionTracker();
      tracker.update(_state(), hasPendingRequest: true, now: _t0);
      expect(tracker.remaining(_t0), const Duration(seconds: 60));
      tracker.update(_state(), hasPendingRequest: false, now: _at(5));
      expect(tracker.remaining(_at(5)), isNull);
      expect(tracker.responseMs(_at(5)), isNull);
      expect(tracker.takeTimeout(_at(100)), isFalse);
    });
  });

  group('widgets', () {
    Future<void> pump(WidgetTester tester, Widget child) async {
      tester.view.physicalSize = const Size(1024, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
          theme: buildDeskmateTheme(), home: Scaffold(body: child)));
    }

    Widget dashboard(DisplayState state,
            {AutoNotice? notice,
            bool pending = false,
            VoidCallback? onUndo,
            VoidCallback? onCorrect,
            ValueChanged<String>? onFeedback,
            String? focusSummary}) =>
        DashboardView(
          state: state,
          hasPendingRequest: pending,
          keystroke: null,
          keystrokeReference: state.timestamp,
          onFeedback: onFeedback ?? (_) {},
          showDemoControl: false,
          demoCyclingEnabled: false,
          onToggleDemoCycling: () {},
          pinAmbient: true,
          showFocusDetail: false,
          onShowFocusDetail: (_) {},
          autoNotice: notice,
          onUndoAuto: onUndo,
          onDismissAuto: () {},
          suggestionRemaining: pending ? const Duration(seconds: 25) : null,
          onCorrect: onCorrect,
          focusSummary: focusSummary,
        );

    testWidgets('auto notice comes before a pending suggestion and undoes',
        (tester) async {
      final auto = _state(
          fsmState: 'ACTION_ENV',
          gate: 'auto',
          cause: 'environment',
          envFlags: const ['co2_rising']);
      final tracker = InterventionTracker()
        ..update(auto, hasPendingRequest: true, now: _t0);
      var undone = 0;
      await pump(
          tester,
          dashboard(auto,
              notice: tracker.autoNotice,
              pending: true,
              onUndo: () => undone++));

      expect(find.byKey(const ValueKey('auto-action-card')), findsOneWidget);
      expect(find.text('이유: CO₂가 빠르게 오르고 있어요'), findsOneWidget);
      expect(find.text('확신도 82%'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('auto-undo')));
      expect(undone, 1);
      expect(tester.takeException(), isNull);
    });

    testWidgets('suggestion shows reason, remaining time and correction',
        (tester) async {
      final verdicts = <String>[];
      var corrections = 0;
      await pump(
          tester,
          dashboard(
            _state(
                fsmState: 'ACTION_ENV',
                phase: 'fatigue',
                gate: 'suggest',
                cause: 'environment',
                envFlags: const ['too_dark']),
            pending: true,
            onFeedback: verdicts.add,
            onCorrect: () => corrections++,
          ));

      expect(find.text('조명을 조금 밝혀 볼까요?'), findsOneWidget);
      expect(find.text('이유: 조명이 어두워요 (150 lx)'), findsOneWidget);
      expect(find.textContaining('25초 뒤 닫혀요'), findsOneWidget);
      await tester.tap(find.text('적용할게요'));
      await tester.tap(find.byKey(const ValueKey('suggestion-correct')));
      expect(verdicts, ['accept']);
      expect(corrections, 1);
      expect(tester.takeException(), isNull);
    });

    testWidgets('ambient home shows on-device note, focus summary, correction',
        (tester) async {
      var corrections = 0;
      await pump(
          tester,
          dashboard(_state(fsmState: 'IDLE', phase: 'idle'),
              onCorrect: () => corrections++, focusSummary: '오늘 집중 38분'));

      expect(find.text('오늘 집중 38분'), findsOneWidget);
      expect(find.byKey(const ValueKey('ambient-on-device')), findsOneWidget);
      expect(find.textContaining('28.0°C'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('correct-open')));
      expect(corrections, 1);
      expect(tester.takeException(), isNull);
    });

    testWidgets('no correction button without a destination (demo)',
        (tester) async {
      await pump(tester, dashboard(_state(fsmState: 'IDLE', phase: 'idle')));
      expect(find.byKey(const ValueKey('correct-open')), findsNothing);
    });

    testWidgets('correction sheet returns the picked FSM state',
        (tester) async {
      String? picked = 'unset';
      await pump(
          tester,
          Builder(
            builder: (context) => TextButton(
              onPressed: () async =>
                  picked = await showCorrectionSheet(context),
              child: const Text('open'),
            ),
          ));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('correct-REST')));
      await tester.pumpAndSettle();
      expect(picked, 'REST');
    });
  });
}
