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

  // 국면 화면 맨 위 한 줄. 시연 중 화면을 스치듯 볼 때 쓰는 값들이다.
  testWidgets('focus screen leads with time, focus, presence and vitals',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await _pump(tester, _state(phase: 'focus', fsmState: 'FOCUS_PC'));
    // 값은 아래 지표 스트립에도 나오므로 라벨로 스트립을 집어 확인한다.
    for (final label in ['현재 시간', '집중도', '재실 상태', '심박']) {
      expect(find.text(label), findsOneWidget);
    }
    expect(find.text('19:21'), findsOneWidget);       // 허브 envelope 의 ts
    expect(find.text('72 bpm'), findsOneWidget);      // sensor_summary.mmwave
    expect(find.text('18%'), findsWidgets);           // c_focus
    expect(find.text('재실'), findsOneWidget);
  });

  // 심박은 락온이 풀리면 값 자체가 안 온다. 자리를 비워 두면 "-- bpm" 이
  // 아니라 항목이 통째로 빠져야 한다 — 없는 값에 자리를 내줄 여유가 없다.
  testWidgets('status strip drops the heart slot when no sample arrived',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await _pump(
      tester,
      _state(phase: 'focus', fsmState: 'FOCUS_PC', mmwave: null),
    );
    expect(find.text('심박'), findsNothing);
    expect(find.text('재실'), findsOneWidget);
  });

  // 실기에서 SCD41 만 안 올라오고 온·습도·조도는 멀쩡히 들어오는 일이 있었다.
  testWidgets('status strip still summarises env when CO2 is missing',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await _pump(
      tester,
      _state(phase: 'focus', fsmState: 'FOCUS_PC', co2Ppm: null),
    );
    expect(find.text('센서 대기'), findsNothing);
    expect(find.textContaining('조도 444 lx'), findsWidgets);
  });

  testWidgets('pending MQTT request opens suggestion controls', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await _pump(
      tester,
      _state(phase: 'focus', fsmState: 'FOCUS_PC'),
      pendingRequest: true,
    );
    expect(find.byType(SuggestionView), findsOneWidget);
  });}

Future<void> _pump(
  WidgetTester tester,
  DisplayState state, {
  bool detail = false,
  String? message,
  bool pendingRequest = false,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildDeskmateTheme(),
      home: Scaffold(
        body: DashboardView(
          state: state,
          displayMessage: message,
          hasPendingRequest: pendingRequest,
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
  int? co2Ppm = 720,
  MmwaveSummary? mmwave = const MmwaveSummary(
    motionState: 'still',
    motionLevel: 4,
    distanceCm: 55,
    respBpm: 16,
    heartBpm: 72,
    drowsyState: 'AWAKE',
  ),
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
      co2Ppm: co2Ppm,
      lux: 444,
      mmwave: mmwave,
    );
