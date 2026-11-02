// Explicit local-only integration driver. Not discovered by the ordinary suite.
// Run with hub/tests/control_flutter_live_scenario.py; never targets a board.
import 'dart:async';
import 'dart:convert';
import 'package:deskmate_display/state_source.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/control_state.dart';
import 'package:deskmate_display/environment_control_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';

// Private test broker only. These observations do not replace the Hub's guard.
class HubReadiness {
  HubReadiness(int port)
      : client =
            MqttServerClient.withPort('127.0.0.1', 'mock-test-readiness', port);
  final MqttServerClient client;
  final watch = Stopwatch()..start();
  StreamSubscription<List<MqttReceivedMessage<MqttMessage?>>>? updates;
  Map<String, dynamic>? latest;
  int? receivedMs;
  bool announced = false;

  Future<void> connect() async {
    const topic = 'deskmate/test/process-e2e/readiness';
    final subscribed = Completer<void>();
    client.setProtocolV311();
    client.onSubscribed = (name) {
      if (name == topic && !subscribed.isCompleted) subscribed.complete();
    };
    await client.connect();
    updates = client.updates!.listen((messages) {
      for (final message in messages) {
        final publish = message.payload as MqttPublishMessage;
        if (message.topic != topic || publish.header?.retain == true) continue;
        latest = jsonDecode(MqttPublishPayload.bytesToStringAsString(
            publish.payload.message)) as Map<String, dynamic>;
        receivedMs = watch.elapsedMilliseconds;
      }
    });
    client.subscribe(topic, MqttQos.atLeastOnce);
    await subscribed.future.timeout(const Duration(seconds: 10));
  }

  void announce(MqttStateSource source) {
    if (announced || !source.isConnected) return;
    announced = true;
    final payload = MqttClientPayloadBuilder()..addString('{"ready":true}');
    client.publishMessage('deskmate/test/process-e2e/client-ready',
        MqttQos.atLeastOnce, payload.payload!,
        retain: false);
  }

  bool matches(DisplayState state, ControlProposal? proposal) {
    final status = latest;
    return status != null &&
        receivedMs != null &&
        watch.elapsedMilliseconds - receivedMs! <= 1000 &&
        status['eligible'] == true &&
        status['gate'] == 'suggest' &&
        status['awaiting_user'] == true &&
        proposal != null &&
        status['request_id'] == proposal.requestId &&
        status['boot_id'] == state.hubBootId &&
        status['expires_at'] is num &&
        DateTime.now().millisecondsSinceEpoch <
            (status['expires_at'] as num) * 1000 &&
        jsonEncode(status['command_ids']) ==
            jsonEncode(proposal.plan.commands.map((c) => c.id).toList()) &&
        proposal.matches(
            current: state.control,
            hubBootId: state.hubBootId,
            stateTime: state.timestamp,
            now: DateTime.now());
  }

  Future<void> close() async {
    await updates?.cancel();
    client.disconnect();
  }
}

void main() {
  testWidgets(
      'live real Hub Mock MQTT -> Flutter card -> accept -> confirmed Mock result',
      (tester) async {
    const port = int.fromEnvironment('DESKMATE_MOCK_BRIDGE_PORT');
    // The older in-process Simulation driver has no independent Hub readiness
    // publisher. The three-process driver explicitly requires this handshake.
    const processReadiness = bool.fromEnvironment('DESKMATE_PROCESS_READINESS');
    expect(port, greaterThan(0),
        reason: 'Use the local-only orchestration script');
    final source = MqttStateSource('127.0.0.1', port: port);
    const readinessPort = int.fromEnvironment('DESKMATE_READINESS_BRIDGE_PORT');
    if (processReadiness) expect(readinessPort, greaterThan(0));
    final readiness = HubReadiness(readinessPort);
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    Future<DisplayState> waitFor(bool Function(DisplayState) condition,
        {String? commandId,
        Duration timeout = const Duration(seconds: 60)}) async {
      final elapsed = Stopwatch()..start();
      while (elapsed.elapsed < timeout) {
        DisplayState? state;
        await tester.runAsync(() async {
          try {
            state = await source.fetch();
          } on StateError {/* initial message */}
          if (processReadiness) readiness.announce(source);
          await Future<void>.delayed(const Duration(milliseconds: 30));
        });
        if (state != null && condition(state!)) return state!;
        if (commandId != null && state != null) {
          for (final command
              in state!.control?.commands ?? <ControlCommandState>[]) {
            if (command.id == commandId &&
                const ['skipped', 'failed', 'timeout', 'cancelled']
                    .contains(command.status)) {
              throw StateError(
                  'Hub blocked command: ${command.status}; readiness=${readiness.latest}');
            }
          }
        }
      }
      throw StateError(
          'live Mock MQTT deadline; readiness=${readiness.latest}');
    }

    try {
      if (processReadiness) await tester.runAsync(readiness.connect);
      late ControlProposal proposal;
      Future<void>? feedback;
      final preparation = Stopwatch()..start();
      var waitingLogged = false;
      while (true) {
        final remaining = const Duration(seconds: 60) - preparation.elapsed;
        if (remaining <= Duration.zero) {
          throw StateError(
              'No current actionable card within preparation deadline');
        }
        final state = await waitFor(
            (state) =>
                state.fsmState == 'ACTION_ENV' &&
                source.controlProposal != null &&
                (!processReadiness ||
                    readiness.matches(state, source.controlProposal)),
            timeout: remaining);
        final candidate = source.controlProposal!;
        expect(candidate.plan.isMock, isTrue);
        expect(candidate.plan.commands.single.target, 'mock_lamp');
        expect(candidate.plan.commands.single.value, 70);
        await tester.pumpWidget(MaterialApp(
            home: Scaffold(
                body: Padding(
                    padding: const EdgeInsets.all(24),
                    child: EnvironmentControlSuggestion(
                        state: state,
                        proposal: candidate,
                        connected: source.isConnected,
                        onFeedback: (verdict) {
                          feedback = source.feedbackControl(candidate, verdict);
                        })))));
        final button = tester
            .widget<FilledButton>(find.byKey(const ValueKey('control-accept')));
        if (button.onPressed != null &&
            (!processReadiness ||
                readiness.matches(state, source.controlProposal))) {
          proposal = candidate;
          break;
        }
        // A real wall-clock step may invalidate the card between fetch and
        // build. No tap/feedback has occurred: reacquire the CURRENT eligible
        // plan instead of enabling the old card or replaying an acceptance.
        if (!waitingLogged) {
          debugPrint(
              'E2E card invalidated before tap; waiting for current Hub eligibility');
          waitingLogged = true;
        }
        if (preparation.elapsed >= const Duration(seconds: 60)) {
          throw StateError(
              'No current actionable card within preparation deadline');
        }
      }
      await tester.tap(find.byKey(const ValueKey('control-accept')));
      await tester.runAsync(() async => await feedback);
      final monitor = await waitFor(
          (state) =>
              state.fsmState == 'MONITOR' &&
              state.control?.commands.single.status == 'succeeded',
          commandId: proposal.plan.commands.single.id);
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
      if (processReadiness) await tester.runAsync(readiness.close);
    }
  }, timeout: const Timeout(Duration(seconds: 120)));
}
