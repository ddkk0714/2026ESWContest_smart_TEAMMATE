// Explicit local-only integration driver. Not discovered by the ordinary suite.
// Run with hub/tests/control_flutter_live_scenario.py; never targets a board.
import 'package:deskmate_display/state_source.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/environment_control_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets(
      'live real Hub Mock MQTT -> Flutter card -> accept -> confirmed Mock result',
      (tester) async {
    const port = int.fromEnvironment('DESKMATE_MOCK_BRIDGE_PORT');
    expect(port, greaterThan(0),
        reason: 'Use the local-only orchestration script');
    final source = MqttStateSource('127.0.0.1', port: port);
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    Future<DisplayState> waitFor(bool Function(DisplayState) condition) async {
      for (var i = 0; i < 200; i++) {
        DisplayState? state;
        await tester.runAsync(() async {
          try {
            state = await source.fetch();
          } on StateError {/* initial message */}
          await Future<void>.delayed(const Duration(milliseconds: 30));
        });
        if (state != null && condition(state!)) return state!;
      }
      throw StateError('live Mock MQTT did not deliver expected state');
    }

    try {
      final state = await waitFor((state) =>
          state.fsmState == 'ACTION_ENV' && source.controlProposal != null);
      final proposal = source.controlProposal!;
      expect(proposal.plan.isMock, isTrue);
      expect(proposal.plan.commands.single.target, 'mock_lamp');
      expect(proposal.plan.commands.single.value, 70);
      Future<void>? feedback;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: Padding(
                  padding: const EdgeInsets.all(24),
                  child: EnvironmentControlSuggestion(
                      state: state,
                      proposal: proposal,
                      connected: source.isConnected,
                      onFeedback: (verdict) {
                        feedback = source.feedbackControl(proposal, verdict);
                      })))));
      expect(
          tester
              .widget<FilledButton>(
                  find.byKey(const ValueKey('control-accept')))
              .onPressed,
          isNotNull);
      await tester.tap(find.byKey(const ValueKey('control-accept')));
      await tester.runAsync(() async => await feedback);
      final monitor = await waitFor((state) =>
          state.fsmState == 'MONITOR' &&
          state.control?.commands.single.status == 'succeeded');
      expect(monitor.control!.commands.single.id,
          proposal.plan.commands.single.id);
      expect(monitor.control!.commands.single.stateConfirmed, isTrue);
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: EnvironmentControlResult(control: monitor.control!))));
      expect(find.textContaining('MOCK'), findsOneWidget);
      expect(find.textContaining('상태 확인 완료'), findsOneWidget);
      expect(tester.takeException(), isNull);
    } finally {
      source.close();
    }
  });
}
