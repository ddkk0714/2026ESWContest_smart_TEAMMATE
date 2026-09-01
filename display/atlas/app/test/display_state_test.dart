import 'package:deskmate_display/display_state.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parses hub state envelope and optional sensor summary', () {
    final state = DisplayState.fromEnvelope({
      'schema_version': '1.0',
      'ts': 100.5,
      'seq': 3,
      'data': {
        'fsm_state': 'FOCUS_PC',
        'phase': 'focus',
        'context': 'pc',
        'c_focus': 0.2,
        'c_fatigue': 0.3,
        'confidence': 0.7,
        'gate': 'none',
        'sensor_summary': {'present': true, 'co2_ppm': 812, 'lux': 310},
      },
    });

    expect(state.fsmState, 'FOCUS_PC');
    expect(state.sequence, 3);
    expect(state.co2Ppm, 812);
    expect(state.present, isTrue);
  });

  test('rejects an unknown contract version', () {
    expect(
      () => DisplayState.fromEnvelope({'schema_version': '2.0'}),
      throwsFormatException,
    );
  });

  test('reads keystroke timing metrics from the sensor summary', () {
    final state = DisplayState.fromEnvelope({
      'schema_version': '1.0',
      'ts': 200.0,
      'seq': 9,
      'data': {
        'fsm_state': 'FATIGUE',
        'phase': 'fatigue',
        'context': 'pc',
        'c_focus': 0.3,
        'c_fatigue': 0.8,
        'confidence': 0.8,
        'gate': 'suggest',
        'sensor_summary': {
          'keystroke': {
            'node': 'pc-collector',
            'ts': 199.0,
            'window_s': 60,
            'event_count': 184,
            'dwell_mean_ms': 92.4,
            'dwell_std_ms': 21.8,
            'flight_mean_ms': 148.2,
            'flight_std_ms': 63.5,
            'idle_ratio': 0.18,
            'correction_rate': 0.07,
            'valid': true,
          },
        },
      },
    });

    final ks = state.keystroke!;
    expect(ks.node, 'pc-collector');
    expect(ks.dwellMeanMs, 92.4);
    expect(ks.flightMeanMs, 148.2);
    expect(ks.correctionRate, closeTo(0.07, 1e-9));
    // collector 가 flight_cv 를 안 보내면 std/mean 으로 만든다.
    expect(ks.flightCv, closeTo(63.5 / 148.2, 1e-9));
    expect(ks.ageFrom(state.timestamp), const Duration(seconds: 1));
  });

  test('prefers the collector flight_cv over the derived one', () {
    final ks = KeystrokeMetrics.fromJson({
      'flight_mean_ms': 100.0,
      'flight_std_ms': 50.0,
      'flight_cv': 0.8,
    })!;

    expect(ks.flightCv, 0.8);
  });

  test('leaves absent keystroke fields null instead of zero', () {
    // 0 으로 채우면 화면이 '리듬 완벽 · 마우스 정지' 로 잘못 읽힌다.
    final ks = KeystrokeMetrics.fromJson({'dwell_mean_ms': 90.0})!;

    expect(ks.dwellMeanMs, 90.0);
    expect(ks.idleRatio, isNull);
    expect(ks.correctionRate, isNull);
    expect(ks.mouseEventRate, isNull);
    expect(ks.flightCv, isNull);
    expect(ks.typingActive, isNull);
  });

  test('has no keystroke when the summary omits it', () {
    final state = DisplayState.fromEnvelope({
      'schema_version': '1.0',
      'ts': 100.5,
      'seq': 3,
      'data': {
        'fsm_state': 'FOCUS_PC',
        'phase': 'focus',
        'context': 'pc',
        'c_focus': 0.2,
        'c_fatigue': 0.3,
        'confidence': 0.7,
        'gate': 'none',
        'sensor_summary': {'present': true},
      },
    });

    expect(state.keystroke, isNull);
  });
}
