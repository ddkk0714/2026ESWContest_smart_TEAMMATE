import 'support/control_fixtures.dart';
import 'dart:convert';
import 'package:deskmate_display/control_state.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/environment_control_view.dart';
import 'package:deskmate_display/dashboard_view.dart';
import 'package:deskmate_display/intervention.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
      'legacy success without state confirmation is not displayed as completed',
      () {
    final plan = controlPlan(status: 'succeeded');
    (plan['commands'][0] as Map).remove('state_confirmed');
    final state = ControlState.parse(plan)!;
    expect(state.commands.single.statusLabel, contains('미확인'));
    expect(state.commands.single.stateConfirmed, isFalse);
  });

  test('optional control parsing preserves existing sensor and legacy messages',
      () {
    final state = DisplayState.fromEnvelope(controlEnvelope(controlPlan()));
    expect(state.temperatureC, 32);
    expect(state.control!.commands.single.description,
        'mock_ac · set_power = "on"');
    expect(state.control!.commands.single.reason, 'temperature_high');
    final legacy = controlEnvelope(controlPlan());
    (legacy['data']['sensor_summary'] as Map).remove('control');
    expect(DisplayState.fromEnvelope(legacy).control, isNull);
  });
  test(
      'request binding rejects altered target/op/value/reason/mode, reboot and stale/expired state',
      () {
    final now = DateTime.now();
    final plan = controlPlan();
    final proposal = ControlProposal.parse(requestEnvelope(plan, now: now))!;
    bool matches(Map<String, dynamic> value,
            {DateTime? time, String boot = 'mock-hub', DateTime? at}) =>
        proposal.matches(
            current: ControlState.parse(value),
            hubBootId: boot,
            stateTime: time ?? now,
            now: at ?? now);
    expect(matches(plan), isTrue);
    for (final change in [
      {'target': 'other'},
      {'op': 'set_brightness'},
      {'value': 'off'},
      {'reason': 'co2_high'}
    ]) {
      final changed = jsonDecode(jsonEncode(plan)) as Map<String, dynamic>;
      (changed['commands'][0] as Map).addAll(change);
      expect(matches(changed), isFalse);
    }
    expect(matches({...plan, 'execution_mode': 'live'}), isFalse);
    expect(matches(plan, boot: 'rebooted'), isFalse);
    expect(matches(plan, time: now.subtract(const Duration(seconds: 31))),
        isFalse);
    expect(matches(plan, at: now.add(const Duration(seconds: 60))), isFalse);
    expect(matches({...plan, 'awaiting_user': false}), isFalse);
  });
  test('malformed/empty/unknown plans and NaN cannot enable acceptance', () {
    for (final plan in [
      <String, dynamic>{},
      {...controlPlan(), 'commands': []},
      {...controlPlan(), 'execution_mode': 'unknown'},
      {
        ...controlPlan(),
        'commands': [
          {'command_id': 'bad', 'value': double.nan}
        ]
      }
    ]) {
      expect(ControlProposal.parse(requestEnvelope(plan)), isNull);
    }
    expect(
        ControlProposal.parse(
            {...requestEnvelope(controlPlan()), 'ts': double.infinity}),
        isNull);
  });
  test('new request ID starts its own deadline and response timer', () {
    final now = DateTime.now();
    final state =
        DisplayState.fromEnvelope(controlEnvelope(controlPlan(), now: now));
    final tracker = InterventionTracker();
    tracker.update(state,
        hasPendingRequest: true, requestKey: 'old', expiresInS: 5, now: now);
    tracker.update(state,
        hasPendingRequest: true,
        requestKey: 'new',
        expiresInS: 50,
        now: now.add(const Duration(seconds: 4)));
    expect(tracker.remaining(now.add(const Duration(seconds: 5))),
        const Duration(seconds: 49));
    expect(tracker.responseMs(now.add(const Duration(seconds: 5))), 1000);
  });
  test('ACTION_ENV auto gate never fabricates a completed action notification',
      () {
    final env = controlEnvelope(controlPlan());
    env['data']['gate'] = 'auto';
    final tracker = InterventionTracker()
      ..update(DisplayState.fromEnvelope(env),
          hasPendingRequest: false, now: DateTime.now());
    expect(tracker.autoNotice, isNull);
  });
  testWidgets(
      'mixed CO2/heat renders exact server cooling plan, no guessed ventilation action',
      (tester) async {
    final plan = controlPlan();
    final state = DisplayState.fromEnvelope(controlEnvelope(plan));
    final proposal = ControlProposal.parse(requestEnvelope(plan));
    final feedback = <String>[];
    await pumpControl(
        tester,
        EnvironmentControlSuggestion(
            state: state,
            proposal: proposal,
            connected: true,
            onFeedback: feedback.add));
    expect(find.text('mock_ac · set_power = "on"'), findsOneWidget);
    expect(find.text('원인: temperature_high'), findsOneWidget);
    expect(find.textContaining('MOCK'), findsOneWidget);
    expect(find.text('직접 환기해 주세요'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('control-accept')));
    expect(feedback, ['accept']);
    expect(tester.takeException(), isNull);
  });
  testWidgets(
      'CO2 without vent is advice-only and cannot imply appliance actuation',
      (tester) async {
    final plan = controlPlan(adviceOnly: true);
    await pumpControl(
        tester,
        EnvironmentControlSuggestion(
            state: DisplayState.fromEnvelope(controlEnvelope(plan)),
            proposal: ControlProposal.parse(requestEnvelope(plan)),
            connected: true,
            onFeedback: (_) {}));
    expect(find.text('직접 환기해 주세요'), findsOneWidget);
    expect(find.text('안내 확인'), findsOneWidget);
    expect(find.textContaining('환기를 위한 가전 명령은 없습니다.'), findsOneWidget);
    expect(find.byKey(const ValueKey('plan-mock-command-1')), findsNothing);
    expect(tester.takeException(), isNull);
  });
  for (final kind in ['missing', 'mismatch', 'expired', 'disconnected']) {
    testWidgets('$kind disables execution acceptance', (tester) async {
      final plan = controlPlan();
      final proposal = kind == 'missing'
          ? null
          : ControlProposal.parse(requestEnvelope(plan,
              now: kind == 'expired'
                  ? DateTime.now().subtract(const Duration(minutes: 2))
                  : null));
      await pumpControl(
          tester,
          EnvironmentControlSuggestion(
              state: DisplayState.fromEnvelope(controlEnvelope(
                  kind == 'mismatch' ? controlPlan(target: 'other') : plan)),
              proposal: proposal,
              connected: kind != 'disconnected',
              onFeedback: (_) => fail('must not accept')));
      expect(
          tester
              .widget<FilledButton>(
                  find.byKey(const ValueKey('control-accept')))
              .onPressed,
          isNull);
    });
  }
  for (final status in [
    'accepted',
    'executing',
    'succeeded',
    'failed',
    'timeout',
    'cancelled'
  ]) {
    testWidgets('MONITOR retains $status result and Mock label',
        (tester) async {
      final plan = controlPlan(status: status);
      plan['commands'][0]['error_code'] =
          status == 'failed' ? 'network_error' : null;
      final envelope = controlEnvelope(
          {'active': false, 'execution_mode': 'mock', 'last_episode': plan});
      envelope['data']['fsm_state'] = 'MONITOR';
      envelope['data']['phase'] = 'focus';
      final state = DisplayState.fromEnvelope(envelope);
      await pumpControl(
          tester,
          DashboardView(
              state: state,
              keystroke: null,
              keystrokeReference: state.timestamp,
              onFeedback: (_) {},
              showDemoControl: false,
              demoCyclingEnabled: false,
              onToggleDemoCycling: () {},
              showFocusDetail: false,
              onShowFocusDetail: (_) {}));
      expect(find.byKey(const ValueKey('environment-control-result')),
          findsOneWidget);
      expect(find.textContaining('MOCK'), findsOneWidget);
      expect(find.textContaining(state.control!.commands.single.statusLabel),
          findsOneWidget);
      if (status == 'failed') {
        expect(find.textContaining('network_error'), findsOneWidget);
      }
      expect(find.byKey(const ValueKey('auto-action-card')), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }
}

Future<void> pumpControl(WidgetTester tester, Widget child) async {
  tester.view.physicalSize = const Size(1024, 600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      home: Scaffold(
          body: Padding(padding: const EdgeInsets.all(24), child: child))));
  await tester.pump();
}
