import 'package:deskmate_display/keystroke_capture.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

// 물리 키는 dwell 짝짓기 · auto-repeat 억제에만 쓰인다. 어떤 키인지는 저장되지 않는다.
const _a = PhysicalKeyboardKey.keyA;
const _b = PhysicalKeyboardKey.keyB;
const _back = PhysicalKeyboardKey.backspace;

void main() {
  test('classifies keys without reading their content', () {
    expect(
      classifyKeyEvent(const KeyDownEvent(
        physicalKey: _back,
        logicalKey: LogicalKeyboardKey.backspace,
        timeStamp: Duration.zero,
      )),
      KeyKind.backspace,
    );
    expect(
      classifyKeyEvent(const KeyDownEvent(
        physicalKey: _a,
        logicalKey: LogicalKeyboardKey.keyA,
        character: 'a',
        timeStamp: Duration.zero,
      )),
      KeyKind.char,
    );
    // 문자를 만들지 않는 키는 other 다. 화살표·수식키가 타이핑 리듬을 흐리면 안 된다.
    expect(
      classifyKeyEvent(const KeyDownEvent(
        physicalKey: PhysicalKeyboardKey.shiftLeft,
        logicalKey: LogicalKeyboardKey.shiftLeft,
        timeStamp: Duration.zero,
      )),
      KeyKind.other,
    );
  });

  test('reports full idle before anything is typed', () {
    final capture = KeystrokeCapture();
    final sample = capture.extract(10);

    expect(sample.eventCount, 0);
    expect(sample.idleRatio, 1.0);
    expect(sample.typingActive, isFalse);
    // 값이 없는 걸 0 으로 채우면 '리듬 완벽' 으로 잘못 읽힌다.
    expect(sample.dwellMeanMs, isNull);
    expect(sample.flightMeanMs, isNull);
  });

  test('measures dwell and flight from real press/release pairs', () {
    final capture = KeystrokeCapture();
    // 100ms 눌렀다 떼고, 200ms 뒤에 다음 키를 누른다.
    capture.press(_a, KeyKind.char, 1.0);
    capture.release(_a, 1.1);
    capture.press(_b, KeyKind.char, 1.2);
    capture.release(_b, 1.3);

    final sample = capture.extract(2.0);

    expect(sample.eventCount, 2);
    expect(sample.dwellMeanMs, closeTo(100, 0.01));
    expect(sample.flightMeanMs, closeTo(200, 0.01));
    expect(sample.correctionRate, 0.0);
    expect(sample.typingActive, isTrue);
  });

  test('counts backspace into the correction rate', () {
    final capture = KeystrokeCapture();
    capture.press(_a, KeyKind.char, 1.0);
    capture.release(_a, 1.05);
    capture.press(_back, KeyKind.backspace, 1.2);
    capture.release(_back, 1.25);
    capture.press(_b, KeyKind.char, 1.4);
    capture.release(_b, 1.45);

    expect(capture.extract(2.0).correctionRate, closeTo(1 / 3, 1e-4));
  });

  test('treats a held key as one keystroke', () {
    final capture = KeystrokeCapture();
    expect(capture.press(_a, KeyKind.char, 1.0), isTrue);
    // auto-repeat: 떼지 않은 채 같은 키가 다시 들어온다.
    expect(capture.press(_a, KeyKind.char, 1.1), isFalse);
    expect(capture.press(_a, KeyKind.char, 1.2), isFalse);
    capture.release(_a, 1.3);

    expect(capture.pressTotal, 1);
    expect(capture.extract(2.0).eventCount, 1);
  });

  test('ignores KeyRepeatEvent so auto-repeat stays one keystroke', () {
    final capture = KeystrokeCapture();
    capture.handle(
      const KeyDownEvent(
        physicalKey: _a,
        logicalKey: LogicalKeyboardKey.keyA,
        character: 'a',
        timeStamp: Duration.zero,
      ),
      1.0,
    );
    capture.handle(
      const KeyRepeatEvent(
        physicalKey: _a,
        logicalKey: LogicalKeyboardKey.keyA,
        character: 'a',
        timeStamp: Duration.zero,
      ),
      1.1,
    );

    expect(capture.pressTotal, 1);
  });

  test('drops long pauses from the rhythm stats but counts them as idle', () {
    final capture = KeystrokeCapture();
    // 두 타건 사이에 10초 공백. flightGapMaxS(2.0s) 를 넘으므로 리듬에서 빠지고,
    // idleGapS(3.0s) 를 넘으므로 공백으로 잡힌다.
    capture.press(_a, KeyKind.char, 1.0);
    capture.release(_a, 1.05);
    capture.press(_b, KeyKind.char, 11.0);
    capture.release(_b, 11.05);

    final sample = capture.extract(11.0);

    expect(sample.flightMeanMs, isNull, reason: '2초 넘는 간격은 리듬 통계에서 뺀다');
    expect(sample.idleRatio, isNotNull);
    expect(sample.idleRatio!, greaterThan(0.9), reason: '60초 창에서 대부분이 공백이다');
  });

  test('derives flight_cv from the captured spread', () {
    final capture = KeystrokeCapture();
    var t = 1.0;
    for (final gap in [0.10, 0.30, 0.10, 0.30]) {
      capture.press(_a, KeyKind.char, t);
      capture.release(_a, t + 0.05);
      t += gap;
    }
    capture.press(_b, KeyKind.char, t);
    capture.release(_b, t + 0.05);

    final sample = capture.extract(t + 1);
    expect(sample.flightCv, isNotNull);
    expect(sample.flightCv!, greaterThan(0), reason: '간격이 들쭉날쭉하면 CV 가 0 이 아니다');
  });
}
