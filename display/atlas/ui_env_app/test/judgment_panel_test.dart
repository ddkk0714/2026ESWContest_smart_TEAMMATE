import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/judgment_panel.dart';
import 'package:deskmate_display/tof_raw_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _wrap(Widget child) => MaterialApp(
      home: Scaffold(body: SizedBox(width: 560, height: 260, child: child)),
    );

String _text(WidgetTester tester, String key) =>
    tester.widget<Text>(find.byKey(ValueKey(key))).data!;

void main() {
  testWidgets('shows every judgment value from state and posture service',
      (tester) async {
    final state = DisplayState.fromEnvelope({
      'schema_version': '1.0',
      'ts': 1769000002.0,
      'seq': 77,
      'data': {
        'fsm_state': 'FATIGUE_SUSPECT',
        'phase': 'fatigue',
        'context': 'pc',
        'c_fatigue': 0.6149,
        'c_focus': 0.3,
        'confidence': 0.6,
        'gate': 'suggest',
        'cause': 'posture',
        'reasons': ['tof_lean'],
        'sensor_summary': {
          'present': true,
          'presence_source': 'mmwave',
          'in_room': true,
          'tof': {
            'posture': 'lean_forward',
            'posture_confidence': 0.77,
            'posture_source': 'service',
          },
          'mmwave': {'distance_cm': 64, 'motion_state': 'still'},
          'personalize': {'explanation': '최근 수락률이 높은 자세 제안'},
        },
      },
    });
    const posture = TofPostureLabel(
      valid: true,
      posture: 'lean_forward',
      postureKo: '상체 앞으로',
      conf: 0.81,
      present: true,
      misplaced: false,
      onlineSensors: 2,
      placement: 'left',
    );
    await tester.pumpWidget(_wrap(JudgmentPanel(state: state, posture: posture)));
    expect(_text(tester, 'judge-state'), 'FATIGUE_SUSPECT');
    expect(_text(tester, 'judge-placement'), 'left ■□□');
    expect(_text(tester, 'judge-fatigue'), '0.61');
    expect(_text(tester, 'judge-focus'), '0.30');
    expect(_text(tester, 'judge-cause'), 'posture');
    expect(_text(tester, 'judge-gate'), 'suggest');
    expect(_text(tester, 'judge-posture-hub'), 'lean_forward (service, 0.77)');
    expect(_text(tester, 'judge-posture-svc'), 'lean_forward 0.81 (센서 2)');
    expect(_text(tester, 'judge-presence'),
        'present=true · src=mmwave · in_room=true · 64 cm');
    expect(_text(tester, 'judge-personalize'), '최근 수락률이 높은 자세 제안');
    expect(find.text('seq 77'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('without state or posture every value is "-"', (tester) async {
    await tester.pumpWidget(
        _wrap(const JudgmentPanel(state: null, posture: null)));
    for (final key in [
      'judge-state',
      'judge-fatigue',
      'judge-focus',
      'judge-cause',
      'judge-gate',
      'judge-posture-hub',
      'judge-posture-svc',
      'judge-presence',
      'judge-placement',
      'judge-personalize',
    ]) {
      expect(_text(tester, key), '-', reason: key);
    }
    expect(tester.takeException(), isNull);
  });

  testWidgets('state without tof summary shows "-" for hub posture, invalid service label says no input',
      (tester) async {
    final state = DisplayState.fromEnvelope({
      'schema_version': '1.0',
      'ts': 1769000002.0,
      'data': {
        'fsm_state': 'IDLE',
        'phase': 'idle',
        'context': 'none',
        'c_fatigue': 0,
        'c_focus': 0,
        'confidence': 0,
        'gate': 'none',
        'reasons': [],
        'sensor_summary': {},
      },
    });
    const posture = TofPostureLabel(
      valid: false,
      posture: 'unknown',
      postureKo: '',
      conf: 0,
      present: false,
      misplaced: false,
      onlineSensors: 0,
    );
    await tester.pumpWidget(_wrap(JudgmentPanel(state: state, posture: posture)));
    expect(_text(tester, 'judge-state'), 'IDLE');
    expect(_text(tester, 'judge-cause'), '-');
    expect(_text(tester, 'judge-posture-hub'), '-');
    expect(_text(tester, 'judge-posture-svc'), 'unknown (입력 없음, 센서 0)');
    expect(_text(tester, 'judge-presence'), '-');
    // 옛 서비스는 placement 를 안 준다 → "-"
    expect(_text(tester, 'judge-placement'), '-');
    expect(tester.takeException(), isNull);
  });
}
