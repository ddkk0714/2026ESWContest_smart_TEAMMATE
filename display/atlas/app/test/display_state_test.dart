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
}
