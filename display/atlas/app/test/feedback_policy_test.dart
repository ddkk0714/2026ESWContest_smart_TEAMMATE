import 'package:deskmate_display/feedback_policy.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('iLink checksum matches the verified power and status protocol', () {
    expect(ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        [0x55, 0xaa, 0x01, 0x08, 0x05, 0x01, 0xf1]);
    expect(ilinkFrame([0x01, 0x08, 0x15, 0x06]),
        [0x55, 0xaa, 0x01, 0x08, 0x15, 0x06, 0xdc]);
    // RGB frames carry mode byte 0x03, which must be included in the checksum.
    expect(ilinkFrame([0x03, 0x08, 0x02, 0xff, 0x00, 0x00]),
        [0x55, 0xaa, 0x03, 0x08, 0x02, 0xff, 0x00, 0x00, 0xf4]);
  });

  test('fatigue feedback turns on lamp and uses the configured orange RGB', () {
    final frames = ilinkFramesForPhase('fatigue');
    expect(frames.first, [0x55, 0xaa, 0x01, 0x08, 0x05, 0x01, 0xf1]);
    expect(frames.last.sublist(0, 7), [0x55, 0xaa, 0x03, 0x08, 0x02, 255, 120]);
  });

  test('unknown phase cannot send a lamp command', () {
    expect(ilinkFramesForPhase('unexpected'), isEmpty);
  });
}