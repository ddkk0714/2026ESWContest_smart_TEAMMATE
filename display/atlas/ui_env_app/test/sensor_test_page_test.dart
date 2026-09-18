import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/sensor_test_page.dart';
import 'package:deskmate_display/state_source.dart';
import 'package:deskmate_display/session_report.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('sensor lab exposes every normalized FSM signal and advances it',
      (tester) async {
    final source = _FakeSource();
    var state = _state();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SensorTestPage(
          source: source,
          state: state,
          onConnect: (_) async {},
          onStateChanged: (next) => state = next,
          onOverride: (_) {},
        ),
      ),
    ));

    expect(find.text('키스트로크'), findsOneWidget);
    expect(find.text('ToF 자세'), findsOneWidget);
    expect(find.text('호흡'), findsOneWidget);
    expect(find.text('환경'), findsOneWidget);
    expect(find.text('경과 시간'), findsOneWidget);
    expect(find.byType(Slider), findsNWidgets(11));

    await tester.tap(find.byKey(const ValueKey('sensor-test-reset')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 1300));
    await tester.pump();

    expect(source.lastCommand, 'reset');
    expect(
        source.lastInput?.signals.keys,
        containsAll(<String>[
          'keystroke',
          'posture',
          'respiration',
          'environment',
          'elapsed',
        ]));
  });

  testWidgets('Pi 4 없이도 임의의 FSM 상태로 화면을 고정할 수 있다', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    DisplayState? overridden;
    final source = _FakeSource()..sensorTest = false;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SensorTestPage(
          source: source,
          state: _state(),
          onConnect: (_) async {},
          onStateChanged: (_) {},
          onOverride: (next) => overridden = next,
        ),
      ),
    ));
    await tester.pump();

    // 허브 연결 폼은 그대로 있고, 그 옆에 상태 목록이 함께 나온다.
    expect(find.byKey(const ValueKey('hub-url-input')), findsOneWidget);
    expect(
        find.byKey(const ValueKey('override-ACTION_BREAK')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('override-ACTION_BREAK')));
    await tester.pump();

    expect(overridden, isNotNull);
    expect(overridden!.fsmState, 'ACTION_BREAK');
    // 국면 표는 허브의 presentation.py 와 같아야 한다.
    expect(overridden!.phase, 'fatigue');
    // 제안 화면은 gate 가 none 이 아닐 때만 뜬다.
    expect(overridden!.gate, isNot('none'));
  });
}

DisplayState _state() => DisplayState(
      fsmState: 'FOCUS_PC',
      phase: 'focus',
      context: 'pc',
      focus: .1,
      fatigue: .1,
      confidence: .1,
      gate: 'none',
      reasons: const [],
      sequence: 1,
      timestamp: DateTime.now(),
    );

class _FakeSource implements StateSource {
  TestSensorInput? lastInput;
  String? lastCommand;
  /// Pi 4 가 없는 상황(MQTT·데모 소스)을 흉내 낸다.
  bool sensorTest = true;

  @override
  String get label => 'fake';
  @override
  bool get isConnected => true;
  @override
  String get connectionLabel => label;
  @override
  SessionReport? get sessionReport => null;
  @override
  bool get hasPendingRequest => false;

  @override
  bool get supportsSensorTest => sensorTest;

  @override
  String? get displayMessage => null;

  @override
  Future<DisplayState> fetch() async => _state();

  @override
  Future<void> feedback(String verdict) async {}

  @override
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event}) async {
    lastInput = input;
    lastCommand = command;
  }

  @override
  void close() {}
}
