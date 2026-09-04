import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/sensor_test_page.dart';
import 'package:deskmate_display/state_source.dart';
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

  @override
  String get label => 'fake';

  @override
  bool get supportsSensorTest => true;

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
