import 'package:deskmate_display/dashboard_view.dart';
import 'package:deskmate_display/deskmate_theme.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('renders the six product views at 1024x600', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await _pump(tester, _state(phase: 'idle', fsmState: 'IDLE'));
    expect(find.text('오늘도 편안하게 시작해볼까요?'), findsOneWidget);

    await _pump(tester, _state(phase: 'focus', fsmState: 'FOCUS_PC'));
    expect(find.text('지금 잘 집중하고 있어요'), findsOneWidget);

    await _pump(
      tester,
      _state(phase: 'focus', fsmState: 'FOCUS_PC'),
      message: 'Pi 4에서 보낸 안내 문구입니다.',
    );
    expect(find.text('받은 메시지'), findsOneWidget);
    expect(find.text('Pi 4에서 보낸 안내 문구입니다.'), findsOneWidget);

    await _pump(
      tester,
      _state(
        phase: 'fatigue',
        fsmState: 'ACTION_ENV',
        gate: 'suggest',
        cause: 'environment',
      ),
    );
    expect(find.text('조명을 조금 낮춰볼까요?'), findsOneWidget);

    await _pump(tester, _state(phase: 'end', fsmState: 'END'));
    expect(find.text('오늘의 집중 리포트'), findsOneWidget);

    await _pump(tester, _state(phase: 'recovery', fsmState: 'RECOVERY'));
    expect(find.textContaining('DESKMATE FOCUS'), findsOneWidget);

    await _pump(
      tester,
      _state(phase: 'focus', fsmState: 'FOCUS_PC'),
      detail: true,
    );
    expect(find.text('Deep Focus'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Pause'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

Future<void> _pump(
  WidgetTester tester,
  DisplayState state, {
  bool detail = false,
  String? message,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildDeskmateTheme(),
      home: Scaffold(
        body: DashboardView(
          state: state,
          displayMessage: message,
          keystroke: null,
          keystrokeReference: state.timestamp,
          onFeedback: (_) {},
          showDemoControl: false,
          demoCyclingEnabled: false,
          onToggleDemoCycling: () {},
          showFocusDetail: detail,
          onShowFocusDetail: (_) {},
        ),
      ),
    ),
  );
  await tester.pump();
  expect(tester.takeException(), isNull);
}

DisplayState _state({
  required String phase,
  required String fsmState,
  String gate = 'none',
  String? cause,
}) =>
    DisplayState(
      fsmState: fsmState,
      phase: phase,
      context: 'pc',
      focus: .18,
      fatigue: .24,
      confidence: .86,
      gate: gate,
      cause: cause,
      reasons: const [],
      sequence: 1,
      timestamp: DateTime(2026, 9, 4, 19, 21),
      present: true,
      co2Ppm: 720,
      lux: 444,
    );
