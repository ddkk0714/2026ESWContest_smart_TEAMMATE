import 'dart:convert';
import 'dart:io';
import 'package:deskmate_display/control_state.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/environment_control_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  // CI/local runs may point to a fresh Python export instead of the checked-in
  // synthetic replay. The full server scenarios exercise real loopback MQTT.
  final fixture = jsonDecode(File(const String.fromEnvironment(
          'DESKMATE_CONTROL_FIXTURE',
          defaultValue: 'test/fixtures/control_e2e.json'))
      .readAsStringSync()) as Map<String, dynamic>;
  for (final item in fixture['cases'] as List) {
    final stateEnvelope = Map<String, dynamic>.from(item['state'] as Map);
    final parsed = DisplayState.fromEnvelope(stateEnvelope);
    final control = parsed.control!;
    testWidgets('real Hub Mock export: ${item['name']}', (tester) async {
      tester.view.physicalSize = const Size(1024, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final request = item['request'];
      Widget child;
      if (request != null) {
        // Rebase synthetic time to the widget clock without changing plan/ID.
        final now = DateTime.now();
        final elapsed =
            now.millisecondsSinceEpoch / 1000 - (stateEnvelope['ts'] as num);
        final rebasedState =
            jsonDecode(jsonEncode(stateEnvelope)) as Map<String, dynamic>;
        rebasedState['ts'] = (rebasedState['ts'] as num) + elapsed;
        final rebasedRequest =
            jsonDecode(jsonEncode(request)) as Map<String, dynamic>;
        rebasedRequest['ts'] = (rebasedRequest['ts'] as num) + elapsed;
        final state = DisplayState.fromEnvelope(rebasedState);
        final proposal = ControlProposal.parse(rebasedRequest)!;
        expect(proposal.plan.signature, control.signature);
        expect(
            proposal.matches(
                current: state.control,
                hubBootId: state.hubBootId,
                stateTime: state.timestamp,
                now: now),
            isTrue);
        child = EnvironmentControlSuggestion(
            state: state,
            proposal: proposal,
            connected: true,
            onFeedback: (_) {});
      } else {
        expect(parsed.fsmState, 'MONITOR');
        child = EnvironmentControlResult(control: control);
      }
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: Padding(padding: const EdgeInsets.all(24), child: child))));
      await tester.pump();
      expect(find.textContaining('MOCK'), findsOneWidget);
      for (final command in control.commands) {
        expect(find.textContaining(command.description), findsOneWidget);
        if (request == null) {
          expect(
              find.byWidgetPredicate((widget) =>
                  widget is Text &&
                  (widget.data?.contains(command.description) ?? false) &&
                  (widget.data?.contains(command.statusLabel) ?? false)),
              findsOneWidget);
        }
      }
      for (final advice in control.advice) {
        expect(find.text(adviceText(advice)), findsOneWidget);
      }
      if (request != null) {
        expect(
            tester
                .widget<FilledButton>(
                    find.byKey(const ValueKey('control-accept')))
                .onPressed,
            isNotNull);
      }
      expect(tester.takeException(), isNull);
    });
  }
}
